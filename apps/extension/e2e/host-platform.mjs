import { execFileSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const HOST_NAME = "com.localtranslate.host";
const CHROME_HOST_KEY = `HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\${HOST_NAME}`;

export function chromeExecutable() {
  if (process.platform !== "win32") return "google-chrome";
  const candidates = [
    process.env.CHROME_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate)) ?? "chrome.exe";
}

export function shouldStartXvfb() {
  return process.platform !== "win32" && !process.env.DISPLAY;
}

export function companionHostPath(root) {
  if (process.platform === "win32") {
    return path.join(root, "companion/.venv/Scripts/local-translate-host.exe");
  }
  return path.join(root, "companion/.venv/bin/local-translate-host");
}

export function registerWindowsMockHost(hostScript, extensionId) {
  const batPath = path.join(tmpdir(), `lt-e2e-host-${process.pid}.bat`);
  const python = process.env.LT_E2E_PYTHON;
  const command = python ? `"${python}" "${hostScript}"` : `py -3 "${hostScript}"`;
  writeFileSync(batPath, `@echo off\r\n${command}\r\n`, "utf8");
  const manifestPath = path.join(tmpdir(), `lt-e2e-host-${process.pid}.json`);
  writeFileSync(
    manifestPath,
    JSON.stringify(
      {
        name: HOST_NAME,
        description: "Mock host for e2e",
        path: batPath,
        type: "stdio",
        allowed_origins: [`chrome-extension://${extensionId}/`],
      },
      null,
      2,
    ),
    "utf8",
  );
  const previous = queryRegistryDefault(CHROME_HOST_KEY);
  execFileSync("reg", ["add", CHROME_HOST_KEY, "/ve", "/d", manifestPath, "/f"], { stdio: "ignore" });
  return () => restoreRegistry(CHROME_HOST_KEY, previous);
}

function queryRegistryDefault(key) {
  try {
    const output = execFileSync("reg", ["query", key, "/ve"], { encoding: "utf8" });
    const match = output.match(/REG_SZ\s+(.+)/);
    return match ? match[1].trim() : "";
  } catch {
    return null;
  }
}

function restoreRegistry(key, previous) {
  if (previous === null) {
    execFileSync("reg", ["delete", key, "/f"], { stdio: "ignore" });
    return;
  }
  execFileSync("reg", ["add", key, "/ve", "/d", previous, "/f"], { stdio: "ignore" });
}
