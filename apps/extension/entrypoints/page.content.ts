import { translateFocusedInput, watchInputs } from "../lib/input-assist";
import { injectPageStyles, startPageTranslation } from "../lib/page-translator";
import { closeSelectionPopup, selectionAnchor, showSelectionPopup } from "../lib/selection-popup";
import { renderCaption, setSubtitleSession } from "../lib/subtitle-overlay";
import { shouldStartAutoTranslate } from "../lib/site-settings";

export default defineContentScript({
  matches: ["<all_urls>"],
  runAt: "document_idle",
  main() {
    injectPageStyles();
    watchInputs();
    document.addEventListener("mouseup", () => {
      const anchor = selectionAnchor();
      if (!anchor) return;
      const chip = document.createElement("div");
      chip.dataset.ltRoot = "true";
      chip.style.position = "fixed";
      chip.style.left = `${anchor.x}px`;
      chip.style.top = `${anchor.y + 6}px`;
      chip.style.zIndex = "2147483646";
      const shadow = chip.attachShadow({ mode: "open" });
      shadow.innerHTML = `<style>button{background:#17242c;color:#e6efe9;border:0;border-radius:999px;padding:3px 8px;font:12px sans-serif}</style><button type="button">翻訳</button>`;
      shadow.querySelector("button")?.addEventListener("click", () => {
        showSelectionPopup(anchor.text, anchor.x, anchor.y);
        chip.remove();
      });
      document.querySelectorAll("[data-lt-chip]").forEach((node) => node.remove());
      chip.dataset.ltChip = "true";
      document.documentElement.append(chip);
      setTimeout(() => chip.remove(), 4000);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeSelectionPopup();
    });
    chrome.runtime.onMessage.addListener((message) => {
      if (message.type === "start-page-translation") startPageTranslation();
      if (message.type === "translate-selection") {
        const anchor = selectionAnchor();
        const text = message.text || anchor?.text || "";
        if (text) showSelectionPopup(text, anchor?.x ?? 24, anchor?.y ?? 24);
      }
      if (message.type === "translate-input") translateFocusedInput();
      if (message.type === "subtitle-session") setSubtitleSession(Boolean(message.active));
      if (message.type === "caption") renderCaption(message.caption);
    });
    void chrome.runtime.sendMessage({ type: "should-auto-translate", host: location.host }).then((auto) => {
      if (shouldStartAutoTranslate(auto)) startPageTranslation();
    });
  },
});
