import { afterEach, describe, expect, it, vi } from "vitest";
import { actionAppearance, applyActionAppearance } from "../lib/action-appearance";
import type { UiState } from "../lib/types";

function stateWith(translate: Record<string, unknown> | null, connected = true): UiState {
  if (!translate) return { connected: false, events: [] };
  return {
    connected,
    events: [],
    status: {
      version: "0",
      gpu: { backend: "cpu", name: "CPU", vram_total_mb: null, experimental_igpu: false, available_ram_mb: 8000 },
      suggestion: { model_id: "x", reason: "r", alternatives: [], context_size: 1, n_gpu_layers: 0 },
      llama: { state: "stopped", model_id: null, port: null },
      current: {
        translate: {
          selected: { id: "plamo-3-translate-8b-GGUF", label: "PLaMo 3 Translate 8B", quant: "Q4_K_M", short_label: "8B" },
          loaded: null,
          state: "stopped",
          backend: "cuda",
          backend_error: null,
          context_size: 8192,
          n_gpu_layers: 99,
          parallel: 2,
          ...translate,
        },
        asr: {
          selected: { id: "Haas-ja-v1.0", label: "Haas-ja v1.0", quant: "Q8_0", short_label: "Haas" },
          runtime: "speech.cpp",
          subtitle_pipeline: "speech-serve",
          loaded: null,
          state: "stopped",
          device: "cpu",
          restart_required: false,
        },
      },
      active_sessions: { subtitles: 0 },
    },
  };
}

describe("action appearance", () => {
  it("shows the model name and state in the tooltip with a short badge", () => {
    const ready = actionAppearance(stateWith({ state: "ready" }));
    expect(ready.title).toBe("ローカル翻訳 — PLaMo 3 Translate 8B（稼働中）");
    expect(ready.badge).toBe("8B");
    const starting = actionAppearance(stateWith({ state: "starting" }));
    expect(starting.title).toContain("起動中");
    const idle = actionAppearance(stateWith({ state: "stopped" }));
    expect(idle.title).toContain("待機中");
  });

  it("clears the badge and reports not connected when the daemon is down", () => {
    const disconnected = actionAppearance(stateWith(null));
    expect(disconnected.title).toBe("ローカル翻訳 — 未接続");
    expect(disconnected.badge).toBe("");
    const missing = actionAppearance(stateWith({ state: "not_downloaded" }));
    expect(missing.title).toContain("未ダウンロード");
    expect(missing.badge).toBe("");
  });

  it("applies the appearance to chrome.action", async () => {
    const calls: unknown[][] = [];
    vi.stubGlobal("chrome", {
      action: {
        setTitle: async (value: unknown) => void calls.push(["title", value]),
        setBadgeText: async (value: unknown) => void calls.push(["text", value]),
        setBadgeBackgroundColor: async (value: unknown) => void calls.push(["color", value]),
      },
    });
    await applyActionAppearance(stateWith({ state: "ready" }));
    expect(calls).toContainEqual(["title", { title: "ローカル翻訳 — PLaMo 3 Translate 8B（稼働中）" }]);
    expect(calls).toContainEqual(["text", { text: "8B" }]);
    await applyActionAppearance(stateWith(null));
    expect(calls).toContainEqual(["text", { text: "" }]);
    vi.unstubAllGlobals();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
