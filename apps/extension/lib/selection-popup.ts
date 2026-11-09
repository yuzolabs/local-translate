import { TRANSLATION_PRIORITY } from "./types";

let host: HTMLElement | null = null;
let requestId = 0;

export function showSelectionPopup(text: string, x: number, y: number): void {
  const trimmed = text.trim();
  if (!trimmed) return;
  closeSelectionPopup();
  host = document.createElement("div");
  host.dataset.ltRoot = "true";
  host.style.position = "fixed";
  host.style.left = `${Math.min(x, window.innerWidth - 320)}px`;
  host.style.top = `${Math.min(y + 8, window.innerHeight - 40)}px`;
  host.style.zIndex = "2147483646";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      .card { width: min(320px, 80vw); background: #17242c; color: #e6efe9; border-radius: 10px; padding: 10px 12px; font: 14px/1.5 sans-serif; box-shadow: 0 10px 28px rgba(0,0,0,.28); }
      .label { color: #d0892f; font-size: 11px; letter-spacing: .08em; }
      .body { margin-top: 6px; white-space: pre-wrap; }
      button { margin-top: 8px; background: transparent; color: #e6efe9; border: 1px solid #1f6f6a; border-radius: 999px; padding: 2px 8px; }
    </style>
    <div class="card">
      <div class="label">選択範囲</div>
      <div class="body" id="body">翻訳しています…</div>
      <button type="button" id="close">閉じる</button>
    </div>
  `;
  shadow.getElementById("close")?.addEventListener("click", closeSelectionPopup);
  document.documentElement.append(host);
  const id = `sel-${++requestId}`;
  const port = chrome.runtime.connect({ name: "translate" });
  port.onMessage.addListener((message) => {
    if (message.requestId !== id) return;
    const body = shadow.getElementById("body");
    if (!body) return;
    body.textContent = message.type === "error" ? message.message : message.text;
  });
  port.postMessage({ type: "translate", requestId: id, text: trimmed, priority: TRANSLATION_PRIORITY.selection });
}

export function closeSelectionPopup(): void {
  host?.remove();
  host = null;
}

export function selectionAnchor(): { text: string; x: number; y: number } | null {
  const selection = window.getSelection();
  const text = selection?.toString() ?? "";
  if (!text.trim() || !selection?.rangeCount) return null;
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  return { text, x: rect.left, y: rect.bottom };
}
