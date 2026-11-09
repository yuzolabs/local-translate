import { spawn, type ChildProcess } from "node:child_process";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, expect, test } from "@playwright/test";

const e2eDir = path.dirname(fileURLToPath(import.meta.url));
const extensionDir = path.resolve(e2eDir, "../.output/chrome-mv3");
// LT_E2E_DAEMON=real keeps the old behavior (no daemon; bare UI assertions).
// The default spins up e2e/mock-daemon.mjs and a native-messaging shim so the
// connected flows (models, license, consent, translate) run against mocks.
const mockMode = (process.env.LT_E2E_DAEMON ?? "mock") !== "real";
const mockPort = 9639 + (process.pid % 40);
const mockToken = "mock-token";

test("options page shows the local translation controls", async () => {
  const display = await ensureDisplay();
  const userDataDir = path.join("/tmp", `lt-e2e-${process.pid}`);
  const port = 9400 + (process.pid % 200);
  const mockDaemon = mockMode
    ? spawn("node", [path.join(e2eDir, "mock-daemon.mjs")], {
        env: { ...process.env, LT_MOCK_PORT: String(mockPort), LT_MOCK_TOKEN: mockToken },
        stdio: "ignore",
      })
    : null;
  const chrome = spawn(
    "google-chrome",
    [
      `--user-data-dir=${userDataDir}`,
      "--no-first-run",
      `--remote-debugging-port=${port}`,
      "about:blank",
    ],
    {
      stdio: "ignore",
      // The native-messaging shim inherits this env to find the mock port.
      env: { ...process.env, LT_MOCK_PORT: String(mockPort), LT_MOCK_TOKEN: mockToken },
    },
  );
  try {
    if (mockMode) await waitMockDaemon();
    await waitForDebugger(port);
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const session = await browser.newBrowserCDPSession();
    const loaded = await session.send("Extensions.loadUnpacked", { path: extensionDir });
    if (mockMode) registerMockHost(userDataDir, loaded.id);
    await session.send("Target.createTarget", { url: `chrome-extension://${loaded.id}/options.html` });
    const page = await waitForPage(browser, "options.html");
    await expect(page.getByRole("heading", { name: "試し翻訳" })).toBeVisible();
    await expect(page.getByRole("button", { name: "翻訳する" })).toBeVisible();
    await expect(page.getByText("Built with PLaMo")).toBeVisible();
    if (mockMode) await exerciseMockFlows(page);
    await browser.close();
  } finally {
    chrome.kill();
    mockDaemon?.kill();
    display?.kill();
  }
});

async function exerciseMockFlows(page): Promise<void> {
  await expect(page.getByText("plamo-3-translate-8b-GGUF")).toBeVisible();
  await page.getByRole("button", { name: "ライセンスを見る" }).click();
  await expect(page.getByText("モックのライセンス概要です。")).toBeVisible();
  await page.getByRole("button", { name: "同意して記録する" }).click();
  await expect(page.getByRole("button", { name: "ダウンロード" })).toBeVisible();
  await page.locator("textarea").fill("Hello.");
  await page.getByRole("button", { name: "翻訳する" }).click();
  await expect(page.locator(".preview")).toHaveText("モック訳", { timeout: 10000 });
}

function registerMockHost(userDataDir: string, extensionId: string): void {
  const hostDir = path.join(userDataDir, "NativeMessagingHosts");
  mkdirSync(hostDir, { recursive: true });
  const hostScript = path.join(e2eDir, "nm-mock-host.py");
  chmodSync(hostScript, 0o755);
  writeFileSync(
    path.join(hostDir, "com.localtranslate.host.json"),
    JSON.stringify(
      {
        name: "com.localtranslate.host",
        description: "Mock host for e2e",
        path: hostScript,
        type: "stdio",
        allowed_origins: [`chrome-extension://${extensionId}/`],
      },
      null,
      2,
    ),
  );
}

async function waitMockDaemon(): Promise<void> {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${mockPort}/v1/status`, {
        headers: { Authorization: `Bearer ${mockToken}` },
      });
      if (response.ok) return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error("モック daemon が起動しません");
}

async function ensureDisplay(): Promise<ChildProcess | null> {
  if (process.env.DISPLAY) return null;
  const xvfb = spawn("Xvfb", [":98", "-screen", "0", "1280x800x24"], { stdio: "ignore" });
  process.env.DISPLAY = ":98";
  await new Promise((resolve) => setTimeout(resolve, 400));
  return xvfb;
}

async function waitForDebugger(port: number): Promise<void> {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  throw new Error("Chrome のデバッグポートが開きません");
}

async function waitForPage(browser: Awaited<ReturnType<typeof chromium.connectOverCDP>>, suffix: string) {
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    const page = browser.contexts().flatMap((context) => context.pages()).find((item) => item.url().includes(suffix));
    if (page) return page;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("オプションページが開きません");
}
