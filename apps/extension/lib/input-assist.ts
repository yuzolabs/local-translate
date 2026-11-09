import { TRANSLATION_PRIORITY } from "./types";

let host: HTMLElement | null = null;
let current: HTMLElement | null = null;
let requestId = 0;

export function watchInputs(): void {
  document.addEventListener("focusin", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !isEditable(target)) return;
    current = target;
    showChip(target);
  });
  document.addEventListener("keydown", (event) => {
    if (event.altKey && event.key.toLowerCase() === "a" && current) {
      event.preventDefault();
      openPanel(current);
    }
  });
}

export function translateFocusedInput(): void {
  const active = document.activeElement;
  if (active instanceof HTMLElement && isEditable(active)) openPanel(active);
  else if (current) openPanel(current);
}

function isEditable(element: HTMLElement): boolean {
  if (element.closest("[data-lt-root]")) return false;
  if (element instanceof HTMLTextAreaElement) return true;
  if (element instanceof HTMLInputElement) return ["text", "search", "email", "url"].includes(element.type);
  return element.isContentEditable;
}

function fieldText(element: HTMLElement): string {
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) return element.value;
  return element.innerText;
}

function replaceField(element: HTMLElement, text: string): void {
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    element.value = text;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  element.innerText = text;
}

function showChip(element: HTMLElement): void {
  host?.remove();
  const rect = element.getBoundingClientRect();
  host = document.createElement("div");
  host.dataset.ltRoot = "true";
  host.style.position = "fixed";
  host.style.left = `${rect.right - 72}px`;
  host.style.top = `${Math.max(8, rect.top - 28)}px`;
  host.style.zIndex = "2147483646";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>button{background:#17242c;color:#e6efe9;border:0;border-radius:999px;padding:3px 8px;font:12px sans-serif}</style><button type="button">翻訳</button>`;
  shadow.querySelector("button")?.addEventListener("click", () => openPanel(element));
  document.documentElement.append(host);
}

function openPanel(element: HTMLElement): void {
  host?.remove();
  const rect = element.getBoundingClientRect();
  host = document.createElement("div");
  host.dataset.ltRoot = "true";
  host.style.position = "fixed";
  host.style.left = `${Math.min(rect.left, window.innerWidth - 340)}px`;
  host.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - 160)}px`;
  host.style.zIndex = "2147483646";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      .panel { width: min(340px, 86vw); background: #e6efe9; color: #142028; border: 1px solid #1f6f6a; border-radius: 12px; padding: 10px; font: 14px/1.5 sans-serif; }
      .preview { min-height: 2.5em; white-space: pre-wrap; }
      .row { display: flex; gap: 6px; margin-top: 8px; }
      button { border: 1px solid #1f6f6a; background: transparent; border-radius: 999px; padding: 3px 8px; }
    </style>
    <div class="panel">
      <div class="preview" id="preview">翻訳しています…</div>
      <div class="row">
        <button type="button" id="replace">置き換える</button>
        <button type="button" id="close">閉じる</button>
      </div>
    </div>
  `;
  let latest = "";
  shadow.getElementById("close")?.addEventListener("click", () => host?.remove());
  shadow.getElementById("replace")?.addEventListener("click", () => {
    if (latest) replaceField(element, latest);
    host?.remove();
  });
  document.documentElement.append(host);
  const id = `input-${++requestId}`;
  const port = chrome.runtime.connect({ name: "translate" });
  port.onMessage.addListener((message) => {
    if (message.requestId !== id) return;
    const preview = shadow.getElementById("preview");
    if (!preview) return;
    latest = message.type === "error" ? "" : message.text;
    preview.textContent = message.type === "error" ? message.message : message.text;
  });
  port.postMessage({
    type: "translate",
    requestId: id,
    text: fieldText(element),
    priority: TRANSLATION_PRIORITY.input,
  });
}
