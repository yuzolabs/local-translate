import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { chromeExecutable, companionHostPath, shouldStartXvfb } from "./host-platform.mjs";

const extensionDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../.output/chrome-mv3");
const display = shouldStartXvfb() ? spawn("Xvfb", [":98", "-screen", "0", "1280x800x24"], { stdio: "ignore" }) : null;
if (display) {
  process.env.DISPLAY = ":98";
  await new Promise((resolve) => setTimeout(resolve, 400));
}
const userDataDir = path.join(tmpdir(), "lt-ui-profile");
const hostManifestDir = path.join(userDataDir, "NativeMessagingHosts");
mkdirSync(hostManifestDir, { recursive: true });
writeFileSync(
  path.join(hostManifestDir, "com.localtranslate.host.json"),
  JSON.stringify({
    name: "com.localtranslate.host",
    description: "Local Translate companion host",
    path: companionHostPath(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..")),
    type: "stdio",
    allowed_origins: ["chrome-extension://jiobdbkfdjchlpmfmgkkmbgmkdmehjfe/"],
  }, null, 2),
);
const chrome = spawn(
  chromeExecutable(),
  [`--user-data-dir=${userDataDir}`, "--no-first-run", "--remote-debugging-port=9342", "about:blank"],
  { stdio: "ignore", env: { ...process.env, LOCAL_TRANSLATE_STATE_DIR: path.join(tmpdir(), "lt-smoke-state"), LOCAL_TRANSLATE_DATA_DIR: path.join(tmpdir(), "lt-smoke-data") } },
);
const deadline = Date.now() + 10000;
while (Date.now() < deadline) {
  try {
    const response = await fetch("http://127.0.0.1:9342/json/version");
    if (response.ok) break;
  } catch {
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}
const browser = await chromium.connectOverCDP("http://127.0.0.1:9342");
const session = await browser.newBrowserCDPSession();
const loaded = await session.send("Extensions.loadUnpacked", { path: extensionDir });
await session.send("Target.createTarget", { url: `chrome-extension://${loaded.id}/options.html` });
let page;
const pageDeadline = Date.now() + 8000;
while (Date.now() < pageDeadline) {
  page = browser.contexts().flatMap((context) => context.pages()).find((item) => item.url().includes("options.html"));
  if (page) break;
  await new Promise((resolve) => setTimeout(resolve, 200));
}
if (!page) throw new Error("options page missing");
await page.getByRole("heading", { name: "試し翻訳" }).waitFor();
await page.waitForTimeout(1500);
const connected = await page.locator(".stamp").innerText();
console.log("stamp", connected);
await page.locator("textarea").fill("Hello.");
await page.getByRole("button", { name: "翻訳する" }).click();
await page.waitForFunction(() => {
  const preview = document.querySelector(".preview");
  return preview && preview.textContent && !preview.textContent.includes("翻訳しています");
}, null, { timeout: 120000 });
console.log("preview", await page.locator(".preview").innerText());
await browser.close();
chrome.kill();
display?.kill();
