import { extractPageBlocks, type PageBlock } from "./page-blocks";
import { TRANSLATION_PRIORITY } from "./types";

const translated = new Set<string>();

export function startPageTranslation(): void {
  const blocks = extractPageBlocks(document);
  const queued = new Set<string>();
  const byId = new Map(blocks.map((block) => [block.id, block]));
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const id = (entry.target as HTMLElement).dataset.ltBlockId;
      if (!id || !entry.isIntersecting) continue;
      queueBlock(byId.get(id), true, queued);
    }
  });
  for (const block of blocks) {
    const element = findBlockElement(block);
    if (!element || element.dataset.ltBlockId) continue;
    element.dataset.ltBlockId = block.id;
    observer.observe(element);
    insertPlaceholder(element, block.id);
  }
  window.setTimeout(() => {
    for (const block of blocks) queueBlock(block, false, queued);
  }, 300);
}

function queueBlock(block: PageBlock | undefined, visible: boolean, queued: Set<string>): void {
  if (!block || queued.has(block.id) || translated.has(block.id)) return;
  queued.add(block.id);
  void translateBlock(block, visible);
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
