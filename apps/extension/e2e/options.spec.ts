import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, expect, test } from "@playwright/test";

const extensionDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../.output/chrome-mv3");

test("options page shows the local translation controls", async () => {
  const display = await ensureDisplay();
  const userDataDir = path.join("/tmp", `lt-e2e-${process.pid}`);
  const port = 9400 + (process.pid % 200);
  const chrome = spawn(
    "google-chrome",
    [
      `--user-data-dir=${userDataDir}`,
      "--no-first-run",
      `--remote-debugging-port=${port}`,
      "about:blank",
    ],
    { stdio: "ignore" },
  );
  try {
    await waitForDebugger(port);
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const session = await browser.newBrowserCDPSession();
    const loaded = await session.send("Extensions.loadUnpacked", { path: extensionDir });
    await session.send("Target.createTarget", { url: `chrome-extension://${loaded.id}/options.html` });
    const page = await waitForPage(browser, "options.html");
    await expect(page.getByRole("heading", { name: "試し翻訳" })).toBeVisible();
    await expect(page.getByRole("button", { name: "翻訳する" })).toBeVisible();
    await expect(page.getByText("Built with PLaMo")).toBeVisible();
    await browser.close();
  } finally {
    chrome.kill();
    display?.kill();
  }
});

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
