import { describe, expect, it, vi } from "vitest";
import { TRANSLATION_LANGUAGES } from "../lib/languages";
import { extractPageBlocks } from "../lib/page-blocks";
import { parseSseChunk } from "../lib/sse-parser";
import { applySubtitleStyle, renderCaption, setSubtitleSession } from "../lib/subtitle-overlay";
import { closeSelectionPopup, showSelectionPopup } from "../lib/selection-popup";
import { effectiveSettings, hostShouldAutoTranslate, shouldStartAutoTranslate, siteRuleForHost } from "../lib/site-settings";
import { LruTranslationCache, translationCacheKey, type CacheStorage } from "../lib/translation-cache";
import { TranslationQueue } from "../lib/translation-queue";
import type { Settings } from "../lib/types";

const settings = {
  target_lang: "ja",
  source_lang: "auto",
  form: "polite",
  glossary: [],
  gpu_backend: "auto",
  n_gpu_layers: null,
  context_size: null,
  parallel: 1,
  idle_timeout_sec: 600,
  active_translate_model: "plamo-3-translate-0.8b-GGUF",
  active_asr_model: "Haas-ja-v1.0",
  asr_engine: "haas",
  subtitle_backend: "speech-serve",
  translate_interim: false,
  subtitle_style: {
    font_size_px: 18,
    text_color: "#fff",
    background: "#000",
    position: "bottom",
    show_original: true,
  },
  site_rules: [],
  experimental_igpu: true,
  whisper_model: "whisper-base",
} satisfies Settings;

describe("languages", () => {
  it("lists the 53 PLaMo languages, with simplified and traditional Chinese separate", () => {
    expect(TRANSLATION_LANGUAGES).toHaveLength(53);
    expect(TRANSLATION_LANGUAGES.map(([code]) => code)).toContain("zh-hans");
    expect(TRANSLATION_LANGUAGES.map(([code]) => code)).toContain("zh-hant");
  });
});

describe("page blocks", () => {
  it("keeps visible prose and skips scripts, hidden nodes, and our own UI", () => {
    document.body.innerHTML = `
      <article>
        <p>Visible paragraph about the meeting.</p>
        <p hidden>Hidden paragraph</p>
        <script>const secret = "do not translate";</script>
        <nav><p>Menu item that is navigation</p></nav>
        <div data-lt-root="true"><p>Overlay copy</p></div>
        <ul><li>First item</li><li>First item</li></ul>
      </article>
    `;
    const blocks = extractPageBlocks(document);
    const texts = blocks.map((block) => block.text);
    expect(texts).toContain("Visible paragraph about the meeting.");
    expect(texts).toContain("First item");
    expect(texts).not.toContain("Hidden paragraph");
    expect(texts.join(" ")).not.toContain("do not translate");
    expect(texts).not.toContain("Overlay copy");
    expect(texts.filter((text) => text === "First item")).toHaveLength(1);
  });
});

describe("translation queue", () => {
  it("runs higher priority work first when the slot is busy", async () => {
    const queue = new TranslationQueue(1);
    const order: string[] = [];
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const first = queue.enqueue(4, async () => {
      order.push("low");
      await gate;
      return "low";
    });
    await Promise.resolve();
    const second = queue.enqueue(0, async () => {
      order.push("high");
      return "high";
    });
    release();
    await Promise.all([first, second]);
    expect(order).toEqual(["low", "high"]);
  });
});

describe("translation cache", () => {
  it("hashes the direction, model, and glossary, then evicts the oldest entry", async () => {
    const memory: Record<string, string> = {};
    const storage: CacheStorage = {
      async read() {
        return { ...memory };
      },
      async write(entries) {
        Object.keys(memory).forEach((key) => delete memory[key]);
        Object.assign(memory, entries);
      },
    };
    const cache = new LruTranslationCache(1, storage);
    const left = await translationCacheKey({
      text: "Hello",
      sourceLang: "en",
      targetLang: "ja",
      modelId: "m",
      form: "polite",
      glossary: [],
    });
    const right = await translationCacheKey({
      text: "Hello",
      sourceLang: "en",
      targetLang: "ja",
      modelId: "m",
      form: "plain",
      glossary: [{ source: "Hello", target: "やあ" }],
    });
    expect(left).not.toBe(right);
    await cache.set(left, "こんにちは");
    await cache.set(right, "やあ");
    expect(await cache.get(left)).toBeNull();
    expect(await cache.get(right)).toBe("やあ");
  });
});

describe("site settings", () => {
  it("applies the exact host rule and auto-translate flag", () => {
    const next = {
      ...settings,
      site_rules: [
        { host: "*.example.com", target_lang: "en", auto_translate: true },
        { host: "news.example.com", target_lang: "ja", form: "plain" as const },
      ],
    };
    expect(siteRuleForHost(next.site_rules, "news.example.com")?.target_lang).toBe("ja");
    expect(effectiveSettings(next, "blog.example.com").target_lang).toBe("en");
    expect(hostShouldAutoTranslate(next, "blog.example.com")).toBe(true);
    expect(hostShouldAutoTranslate(next, "news.example.com")).toBe(false);
  });

  it("starts auto translation only on an explicit true, never on an error object", () => {
    expect(shouldStartAutoTranslate(true)).toBe(true);
    expect(shouldStartAutoTranslate({ error: "コンパニオンに接続できていません" })).toBe(false);
    expect(shouldStartAutoTranslate(undefined)).toBe(false);
    expect(shouldStartAutoTranslate("true")).toBe(false);
  });
});

describe("sse parser", () => {
  it("keeps a partial frame for the next chunk", () => {
    const parsed = parseSseChunk('event: delta\ndata: {"text":"あ"}\n\nevent: done\n');
    expect(parsed.events).toHaveLength(1);
    expect(parsed.events[0]?.event).toBe("delta");
    expect(parsed.rest).toContain("event: done");
  });
});

describe("subtitle overlay", () => {
  it("paints captions with the applied style and the original-text toggle", () => {
    document.body.innerHTML = "";
    setSubtitleSession(true);
    applySubtitleStyle({
      font_size_px: 24,
      text_color: "#123456",
      background: "#000000",
      position: "top",
      show_original: false,
    });
    renderCaption({ seq: 1, start_ms: 0, end_ms: null, text: "hello", translation: "こんにちは", final: true });
    const host = document.documentElement.querySelector("[data-lt-root]") as HTMLElement | null;
    expect(host).not.toBeNull();
    const html = host!.shadowRoot!.innerHTML;
    expect(html).toContain("24px");
    expect(html).toContain("#123456");
    expect(html).toContain("top: 8%");
    expect(html).toContain("こんにちは");
    expect(html).not.toContain('class="orig"');
    setSubtitleSession(false);
  });

  it("shows the asr and translate model names for a few seconds at session start", () => {
    vi.useFakeTimers();
    try {
      document.body.innerHTML = "";
      setSubtitleSession(true, undefined, { asr: "Haas-ja v1.0", translate: "PLaMo 3 Translate 8B" });
      const host = document.documentElement.querySelector("[data-lt-root]") as HTMLElement;
      expect(host.shadowRoot!.innerHTML).toContain("音声認識 Haas-ja v1.0 → 翻訳 PLaMo 3 Translate 8B");
      vi.advanceTimersByTime(4100);
      expect(host.shadowRoot!.innerHTML).not.toContain("音声認識");
      setSubtitleSession(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("selection popup", () => {
  it("appends the used model short name to the heading on completion", () => {
    document.body.innerHTML = "";
    const listeners: Array<(message: Record<string, unknown>) => void> = [];
    const posted: unknown[] = [];
    vi.stubGlobal("chrome", {
      runtime: {
        connect: () => ({
          onMessage: { addListener: (listener) => listeners.push(listener) },
          postMessage: (message) => posted.push(message),
        }),
      },
    });
    try {
      showSelectionPopup("Hello", 10, 10);
      const job = posted[0] as { requestId: string };
      const host = document.documentElement.querySelector("[data-lt-root]") as HTMLElement;
      const label = host.shadowRoot!.querySelector(".label")!;
      expect(label.textContent).toBe("選択範囲");
      for (const listener of listeners) {
        listener({ type: "done", requestId: job.requestId, text: "こんにちは", modelShort: "8B" });
      }
      expect(label.textContent).toBe("選択範囲 · 8B");
      expect((host.shadowRoot!.getElementById("body") as HTMLElement).textContent).toBe("こんにちは");
      closeSelectionPopup();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
