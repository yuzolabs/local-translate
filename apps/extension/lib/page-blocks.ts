export interface PageBlock {
  id: string;
  text: string;
  tag: string;
  score: number;
}

const BLOCK_TAGS = new Set(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td", "th", "figcaption", "dd", "pre"]);
const SKIP_TAGS = new Set(["script", "style", "noscript", "svg", "canvas", "textarea", "input", "select", "button", "nav", "footer", "code"]);
const TAG_WEIGHT: Record<string, number> = {
  p: 1,
  h1: 0.9,
  h2: 0.86,
  h3: 0.82,
  li: 0.72,
  blockquote: 0.84,
  td: 0.46,
  th: 0.4,
  figcaption: 0.55,
  dd: 0.62,
  pre: 0.32,
};

export function extractPageBlocks(root: ParentNode): PageBlock[] {
  const elements = [...root.querySelectorAll(BLOCK_SELECTOR())].filter(isTranslatableBlock);
  const seen = new Set<string>();
  const blocks: PageBlock[] = [];
  elements.forEach((element, index) => {
    const text = (element.textContent ?? "").replace(/\s+/g, " ").trim();
    if (text.length < 2) return;
    const key = text.normalize("NFKC");
    if (seen.has(key)) return;
    const score = scoreBlock(element, text);
    if (score < 0.15) return;
    seen.add(key);
    blocks.push({ id: `block-${index}`, text, tag: element.tagName.toLowerCase(), score });
  });
  return blocks.sort((left, right) => right.score - left.score);
}

export function isHiddenElement(element: Element): boolean {
  if (element.hasAttribute("hidden") || element.getAttribute("aria-hidden") === "true") return true;
  const style = (element as HTMLElement).style;
  return style?.display === "none" || style?.visibility === "hidden";
}

function BLOCK_SELECTOR(): string {
  return [...BLOCK_TAGS].join(",");
}

function isTranslatableBlock(element: Element): boolean {
  if (!BLOCK_TAGS.has(element.tagName.toLowerCase())) return false;
  if (element.closest("[data-lt-root], [data-lt-skip]")) return false;
  if (hasSkippedAncestor(element)) return false;
  if (isHiddenElement(element)) return false;
  if (element.querySelector(BLOCK_SELECTOR())) return false;
  return true;
}

function hasSkippedAncestor(element: Element): boolean {
  let parent = element.parentElement;
  while (parent) {
    if (SKIP_TAGS.has(parent.tagName.toLowerCase())) return true;
    parent = parent.parentElement;
  }
  return false;
}

function scoreBlock(element: Element, text: string): number {
  const tag = element.tagName.toLowerCase();
  const lengthScore = Math.min(text.length / 180, 1);
  let linkLength = 0;
  element.querySelectorAll("a").forEach((link) => {
    linkLength += (link.textContent ?? "").length;
  });
  const linkDensity = text.length ? Math.min(linkLength / text.length, 0.95) : 0;
  return (TAG_WEIGHT[tag] ?? 0.5) * (0.4 + 0.6 * lengthScore) * (1 - linkDensity);
}
