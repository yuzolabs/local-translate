import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const extensionDir = path.join(root, "apps/extension/.output/chrome-mv3");
const hostPath = path.join(root, "companion/.venv/bin/local-translate-host");
const userDataDir = "/tmp/lt-page-profile";
const display = process.env.DISPLAY ? null : spawn("Xvfb", [":97", "-screen", "0", "1280x800x24"], { stdio: "ignore" });
if (!process.env.DISPLAY) {
  process.env.DISPLAY = ":97";
  await new Promise((resolve) => setTimeout(resolve, 400));
}
mkdirSync(path.join(userDataDir, "NativeMessagingHosts"), { recursive: true });
writeFileSync(
  path.join(userDataDir, "NativeMessagingHosts/com.localtranslate.host.json"),
  JSON.stringify({
    name: "com.localtranslate.host",
    description: "Local Translate companion host",
    path: hostPath,
    type: "stdio",
    allowed_origins: ["chrome-extension://jiobdbkfdjchlpmfmgkkmbgmkdmehjfe/"],
  }, null, 2),
);
const server = spawn("python3", ["-m", "http.server", "8766", "--bind", "127.0.0.1"], {
  cwd: "/tmp/lt-fixture",
  stdio: "ignore",
});
const chrome = spawn("google-chrome", [
  `--user-data-dir=${userDataDir}`,
  "--no-first-run",
  "--remote-debugging-port=9343",
  "about:blank",
], {
  stdio: "ignore",
  env: {
    ...process.env,
    LOCAL_TRANSLATE_STATE_DIR: "/tmp/lt-smoke-state",
    LOCAL_TRANSLATE_DATA_DIR: "/tmp/lt-smoke-data",
  },
});
const deadline = Date.now() + 10000;
while (Date.now() < deadline) {
  try {
    if ((await fetch("http://127.0.0.1:9343/json/version")).ok) break;
  } catch {
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}
const browser = await chromium.connectOverCDP("http://127.0.0.1:9343");
const session = await browser.newBrowserCDPSession();
const loaded = await session.send("Extensions.loadUnpacked", { path: extensionDir });
await session.send("Target.createTarget", { url: "http://127.0.0.1:8766/" });
let page;
const pageDeadline = Date.now() + 8000;
while (Date.now() < pageDeadline) {
  page = browser.contexts().flatMap((context) => context.pages()).find((item) => item.url().includes("8766"));
  if (page) break;
  await new Promise((resolve) => setTimeout(resolve, 200));
}
if (!page) throw new Error("fixture page missing");
await page.waitForSelector("#prose");
await session.send("Target.createTarget", { url: `chrome-extension://${loaded.id}/options.html` });
let options;
const optionsDeadline = Date.now() + 8000;
while (Date.now() < optionsDeadline) {
  options = browser.contexts().flatMap((context) => context.pages()).find((item) => item.url().includes("options.html"));
  if (options) break;
  await new Promise((resolve) => setTimeout(resolve, 200));
}
if (!options) throw new Error("options page missing");
await options.waitForTimeout(1000);
const sent = await options.evaluate(async () => {
  const tabs = await chrome.tabs.query({});
  const tab = tabs.find((item) => item.url?.includes("8766"));
  if (!tab?.id) return { error: "tab missing", tabs: tabs.map((item) => item.url) };
  await chrome.tabs.sendMessage(tab.id, { type: "start-page-translation" });
  return { tabId: tab.id };
});
console.log("sent", JSON.stringify(sent));
await page.waitForFunction(() => {
  const node = document.querySelector(".lt-translation");
  return node && node.getAttribute("data-state") === "done" && (node.textContent || "").length > 0;
}, null, { timeout: 120000 });
console.log("page", await page.locator(".lt-translation").innerText());
await options.evaluate(async (tabId) => {
  await chrome.tabs.sendMessage(tabId, { type: "translate-selection", text: "Please save your changes." });
}, sent.tabId);
await page.waitForFunction(() => {
  const host = [...document.querySelectorAll("[data-lt-root]")].find((item) => item.shadowRoot?.textContent?.includes("選択範囲"));
  const text = host?.shadowRoot?.querySelector("#body")?.textContent ?? "";
  const stable = text === (window).__ltSelection && text.length > 4;
  window.__ltSelection = text;
  return stable && !text.includes("翻訳しています");
}, null, { timeout: 120000 });
const selectionText = await page.evaluate(() => {
  const host = [...document.querySelectorAll("[data-lt-root]")].find((item) => item.shadowRoot?.textContent?.includes("選択範囲"));
  return host?.shadowRoot?.querySelector("#body")?.textContent ?? "";
});
console.log("selection", selectionText);
await page.locator("#box").focus();
await options.evaluate(async (tabId) => {
  await chrome.tabs.sendMessage(tabId, { type: "translate-input" });
}, sent.tabId);
await page.waitForFunction(() => {
  return [...document.querySelectorAll("[data-lt-root]")].some((host) => {
    const text = host.shadowRoot?.textContent ?? "";
    return text.includes("置き換える") && !text.includes("翻訳しています");
  });
}, null, { timeout: 120000 });
const inputText = await page.evaluate(() => {
  const host = [...document.querySelectorAll("[data-lt-root]")].find((item) => item.shadowRoot?.textContent?.includes("置き換える"));
  return host?.shadowRoot?.querySelector("#preview")?.textContent ?? "";
});
console.log("input", inputText);
await browser.close();
chrome.kill();
server.kill();
display?.kill();
