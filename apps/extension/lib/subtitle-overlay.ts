import type { Caption, SubtitleStyle } from "./types";

const DEFAULT_STYLE: SubtitleStyle = {
  font_size_px: 18,
  text_color: "#f4f7f6",
  background: "rgba(20, 32, 43, 0.78)",
  position: "bottom",
  show_original: true,
};

let host: HTMLElement | null = null;
let style = DEFAULT_STYLE;
let active = false;
const lines = new Map<number, Caption>();

export function setSubtitleSession(next: boolean, nextStyle?: SubtitleStyle): void {
  active = next;
  if (nextStyle) style = nextStyle;
  if (!active) {
    host?.remove();
    host = null;
    lines.clear();
    return;
  }
  ensureHost();
  trackVideo();
}

export function applySubtitleStyle(next: SubtitleStyle): void {
  style = next;
  if (active && host) paint();
}

export function renderCaption(caption: Caption): void {
  if (!active) setSubtitleSession(true);
  if (!caption.final) {
    lines.set(caption.seq, caption);
  } else {
    for (const [seq, line] of lines) {
      if (!line.final && line.start_ms === caption.start_ms) lines.delete(seq);
    }
    lines.set(caption.seq, caption);
  }
  paint();
}

function ensureHost(): void {
  if (host) return;
  host = document.createElement("div");
  host.dataset.ltRoot = "true";
  host.style.position = "fixed";
  host.style.zIndex = "2147483645";
  host.style.pointerEvents = "none";
  host.attachShadow({ mode: "open" });
  document.documentElement.append(host);
}

function trackVideo(): void {
  if (typeof requestAnimationFrame !== "function") return;
  const tick = () => {
    if (!active || !host) return;
    const video = pickVideo();
    if (!video) {
      host.style.display = "none";
    } else {
      const rect = video.getBoundingClientRect();
      host.style.display = "block";
      host.style.left = `${rect.left}px`;
      host.style.top = `${rect.top}px`;
      host.style.width = `${rect.width}px`;
      host.style.height = `${rect.height}px`;
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function pickVideo(): HTMLVideoElement | null {
  const videos = [...document.querySelectorAll("video")];
  return (
    videos.find((video) => !video.paused && video.videoWidth > 0) ??
    videos.sort((left, right) => right.clientWidth * right.clientHeight - left.clientWidth * left.clientHeight)[0] ??
    null
  );
}

function paint(): void {
  if (!host?.shadowRoot) return;
  const visible = [...lines.values()].slice(-3);
  const position = style.position === "top" ? "top: 8%" : "bottom: 8%";
  host.shadowRoot.innerHTML = `
    <style>
      .stack { position: absolute; left: 8%; right: 8%; ${position}; display: flex; flex-direction: column; gap: 4px; text-align: center; }
      .line { color: ${style.text_color}; background: ${style.background}; font-size: ${style.font_size_px}px; line-height: 1.45; padding: 4px 8px; border-radius: 6px; }
      .orig { opacity: .78; font-size: .86em; }
    </style>
    <div class="stack">
      ${visible
        .map((line) => {
          const original = style.show_original ? `<div class="orig">${escapeHtml(line.text)}</div>` : "";
          const translation = line.translation ? `<div>${escapeHtml(line.translation)}</div>` : "";
          return `<div class="line">${original}${translation || (!line.final ? `<div>${escapeHtml(line.text)}</div>` : "")}</div>`;
        })
        .join("")}
    </div>
  `;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char);
}
