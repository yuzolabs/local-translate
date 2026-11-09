import { extractPageBlocks, type PageBlock } from "./page-blocks";
import { TRANSLATION_PRIORITY } from "./types";

const translated = new Set<string>();

export function startPageTranslation(): void {
  const blocks = extractPageBlocks(document);
  const visible = new Set<string>();
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const id = (entry.target as HTMLElement).dataset.ltBlockId;
      if (!id) continue;
      if (entry.isIntersecting) visible.add(id);
      else visible.delete(id);
    }
  });
  for (const block of blocks) {
    const element = findBlockElement(block);
    if (!element || element.dataset.ltBlockId) continue;
    element.dataset.ltBlockId = block.id;
    observer.observe(element);
    insertPlaceholder(element, block.id);
  }
  const pending = blocks.filter((block) => !translated.has(block.id));
  pending.sort((left, right) => Number(visible.has(right.id)) - Number(visible.has(left.id)) || right.score - left.score);
  for (const block of pending) {
    void translateBlock(block, visible.has(block.id));
  }
}

function findBlockElement(block: PageBlock): HTMLElement | null {
  const candidates = [...document.querySelectorAll(block.tag)];
  return (
    (candidates.find((element) => (element.textContent ?? "").replace(/\s+/g, " ").trim() === block.text) as HTMLElement) ??
    null
  );
}

function insertPlaceholder(element: HTMLElement, id: string): void {
  if (element.nextElementSibling?.getAttribute("data-lt-for") === id) return;
  const node = document.createElement("div");
  node.className = "lt-translation";
  node.dataset.ltFor = id;
  node.dataset.state = "pending";
  node.setAttribute("data-lt-root", "true");
  node.textContent = "翻訳しています…";
  element.insertAdjacentElement("afterend", node);
}

async function translateBlock(block: PageBlock, visible: boolean): Promise<void> {
  translated.add(block.id);
  const port = chrome.runtime.connect({ name: "translate" });
  port.onMessage.addListener((message) => {
    if (message.requestId !== block.id) return;
    const node = document.querySelector(`[data-lt-for="${cssEscape(block.id)}"]`);
    if (!node) return;
    if (message.type === "delta" || message.type === "done") {
      node.textContent = message.text;
      node.setAttribute("data-state", message.type === "done" ? "done" : "pending");
    }
    if (message.type === "error") {
      node.textContent = message.message;
      node.setAttribute("data-state", "error");
    }
  });
  port.postMessage({
    type: "translate",
    requestId: block.id,
    text: block.text,
    priority: visible ? TRANSLATION_PRIORITY.visibleBlock : TRANSLATION_PRIORITY.backgroundBlock,
  });
}

function cssEscape(value: string): string {
  return value.replace(/["\\]/g, "\\$&");
}

export function injectPageStyles(): void {
  if (document.getElementById("lt-page-style")) return;
  const style = document.createElement("style");
  style.id = "lt-page-style";
  style.textContent = `
    .lt-translation {
      display: block;
      margin: 0.2em 0 0.75em;
      padding: 0.05em 0 0.05em 0.55em;
      border-left: 3px solid #1f6f6a;
      color: inherit;
      opacity: 0.88;
      font-size: 0.95em;
      line-height: 1.55;
    }
    .lt-translation[data-state="pending"] { border-left-color: #d0892f; opacity: 0.65; }
    .lt-translation[data-state="error"] { border-left-color: #b64040; }
  `;
  document.documentElement.append(style);
}
