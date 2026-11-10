import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "preact";
import { SettingsApp } from "../components/SettingsApp";
import type { ModelRow, Settings, UiState } from "../lib/types";

type PortListener = (message: { type: string; state?: UiState }) => void;

const settings: Settings = {
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
    text_color: "#f4f7f6",
    background: "rgba(20, 32, 43, 0.78)",
    position: "bottom",
    show_original: true,
  },
  site_rules: [],
  experimental_igpu: true,
  whisper_model: "whisper-base",
};

function modelRow(overrides: Partial<ModelRow>): ModelRow {
  return {
    id: "plamo-3-translate-0.8b-GGUF",
    kind: "translate",
    status: "ready",
    progress: 1,
    consented: true,
    license_id: "plamo-community",
    preset: true,
    ...overrides,
  };
}

function uiState(overrides: Partial<UiState> = {}): UiState {
  return {
    connected: true,
    events: [],
    settings,
    models: [modelRow({})],
    ...overrides,
  };
}

interface ChromeMock {
  pushState(state: UiState): void;
  handlers: Map<string, (message: Record<string, unknown>) => unknown>;
}

function installChromeMock(): ChromeMock {
  const portListeners = new Set<PortListener>();
  const handlers = new Map<string, (message: Record<string, unknown>) => unknown>();
  const runtime = {
    sendMessage: (message: Record<string, unknown>) => {
      const handler = handlers.get(String(message.type));
      return Promise.resolve(handler ? handler(message) : {});
    },
    connect: ({ name }: { name: string }) => ({
      name,
      onMessage: { addListener: (listener: PortListener) => portListeners.add(listener) },
      disconnect: () => portListeners.clear(),
    }),
  };
  const chromeMock = {
    runtime,
    commands: { getAll: () => Promise.resolve([]) },
    tabs: { query: async () => [], sendMessage: async () => undefined },
  };
  vi.stubGlobal("chrome", chromeMock);
  return {
    handlers,
    pushState(state: UiState) {
      for (const listener of portListeners) listener({ type: "state", state });
    },
  };
}

async function renderSettingsApp(): Promise<void> {
  document.body.innerHTML = "";
  container = document.createElement("div");
  document.body.append(container);
  render(<SettingsApp />, container);
  await flush();
}

async function flush(): Promise<void> {
  for (let index = 0; index < 4; index += 1) {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

async function waitFor(predicate: () => boolean, timeoutMs = 1500): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  expect(predicate()).toBe(true);
}

function textOf(node: Element | null): string {
  return (node?.textContent ?? "").trim();
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll("button")].find(
    (button) => textOf(button) === label,
  ) as HTMLButtonElement | undefined;
}

let mock: ChromeMock;
let container: HTMLElement | null = null;

beforeEach(() => {
  mock = installChromeMock();
});

afterEach(async () => {
  if (container) {
    render(null, container);
    container = null;
  }
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("settings app model operations", () => {
  it("shows the switching note while an activation is in flight", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    mock.handlers.set("get-state", () => uiState());
    mock.handlers.set("activate-model", async () => {
      await gate;
      return { status: "ready" };
    });
    await renderSettingsApp();
    const useButton = findButton("使う");
    expect(useButton).toBeDefined();
    useButton!.click();
    await waitFor(() => findButton("使う")?.disabled === true && document.body.textContent!.includes("切り替え中…"));
    release!();
    await waitFor(() => !document.body.textContent!.includes("切り替え中…"));
  });

  it("surfaces background errors from activate inside the model row", async () => {
    mock.handlers.set(
      "get-state",
      () => uiState({ models: [modelRow({ id: "plamo-3-translate-8b-GGUF", status: "ready", consented: true, active: false })] }),
    );
    mock.handlers.set("activate-model", () => ({ error: "モデルがダウンロードされていません" }));
    await renderSettingsApp();
    (findButton("使う") as HTMLButtonElement).click();
    await waitFor(() => Boolean(document.querySelector(".fault")));
    expect(textOf(document.querySelector(".fault"))).toBe("モデルがダウンロードされていません");
  });

  it("surfaces background errors from consent and download too", async () => {
    mock.handlers.set(
      "get-state",
      () => uiState({ models: [modelRow({ id: "plamo-3-translate-8b-GGUF", status: "missing", consented: false })] }),
    );
    mock.handlers.set("model-license", () => ({ name: "PLaMo Community License", url: "", summary: "概要" }));
    mock.handlers.set("consent", () => ({ error: "同意に失敗しました" }));
    await renderSettingsApp();
    (findButton("ライセンスを見る") as HTMLButtonElement).click();
    await waitFor(() => findButton("同意して記録する") !== undefined);
    (findButton("同意して記録する") as HTMLButtonElement).click();
    await waitFor(() => textOf(document.querySelector(".fault")) === "同意に失敗しました");
  });
});

describe("settings app model list", () => {
  it("groups models by kind with the active and suggested badges", async () => {
    mock.handlers.set(
      "get-state",
      () =>
        uiState({
          models: [
            modelRow({ id: "plamo-3-translate-8b-GGUF", display_name: "PLaMo 3 Translate 8B", active: true, status: "running" }),
            modelRow({ id: "plamo-3-translate-0.8b-GGUF", display_name: "PLaMo 3 Translate 0.8B" }),
            modelRow({ id: "Haas-ja-v1.0", kind: "asr", display_name: "Haas-ja v1.0", active: true }),
            modelRow({ id: "silero-vad", kind: "vad", display_name: "Silero VAD" }),
          ],
          status: {
            version: "0",
            gpu: { backend: "cpu", name: "CPU", vram_total_mb: null, experimental_igpu: false, available_ram_mb: 8000 },
            suggestion: { model_id: "plamo-3-translate-0.8b-GGUF", reason: "CPU のため", alternatives: [], context_size: 2048, n_gpu_layers: 0 },
            llama: { state: "ready", model_id: "plamo-3-translate-8b-GGUF", port: 9 },
            active_sessions: { subtitles: 0 },
          },
        }),
    );
    await renderSettingsApp();
    const headings = [...document.querySelectorAll("h3")].map((node) => textOf(node));
    expect(headings).toEqual(["翻訳", "音声認識", "発話区間検出"]);
    const activeRow = document.querySelector(".model.active");
    expect(activeRow).not.toBeNull();
    expect(activeRow!.textContent).toContain("使用中");
    expect(activeRow!.textContent).toContain("稼働中");
    // 使用中の行（翻訳 8B と Haas）と発話区間検出には「使う」を出さない。0.8B だけが出す。
    const useButtons = [...document.querySelectorAll("button")].filter((button) => textOf(button) === "使う");
    expect(useButtons).toHaveLength(1);
    const suggestedRow = [...document.querySelectorAll(".model")].find((row) =>
      row.textContent!.includes("PLaMo 3 Translate 0.8B"),
    );
    expect(suggestedRow!.textContent).toContain("推奨");
    // 発話区間検出には「使う」を出さない。
    const vadRow = [...document.querySelectorAll(".model")].find((row) => row.textContent!.includes("Silero VAD"));
    expect([...(vadRow?.querySelectorAll("button") ?? [])].map((button) => textOf(button))).not.toContain("使う");
  });

  it("disables the use button with a reason when the model is not downloaded", async () => {
    mock.handlers.set(
      "get-state",
      () => uiState({ models: [modelRow({ id: "plamo-3-translate-8b-GGUF", status: "missing", consented: true })] }),
    );
    await renderSettingsApp();
    const useButton = findButton("使う")!;
    expect(useButton.disabled).toBe(true);
    expect(useButton.title).toContain("未ダウンロード");
    expect(document.body.textContent).toContain("未ダウンロード");
  });
});

describe("settings app current models header", () => {
  function currentState(overrides: { translate?: Record<string, unknown>; asr?: Record<string, unknown> } = {}) {
    return {
      translate: {
        selected: { id: "plamo-3-translate-8b-GGUF", label: "PLaMo 3 Translate 8B", quant: "Q4_K_M", short_label: "8B" },
        loaded: null,
        state: "stopped",
        backend: "cuda",
        backend_error: null,
        context_size: 8192,
        n_gpu_layers: 99,
        parallel: 2,
        ...overrides.translate,
      },
      asr: {
        selected: { id: "Haas-ja-v1.0", label: "Haas-ja v1.0", quant: "Q8_0", short_label: "Haas" },
        runtime: "speech.cpp",
        subtitle_pipeline: "speech-serve",
        loaded: null,
        state: "stopped",
        device: "gpu",
        restart_required: false,
        ...overrides.asr,
      },
    };
  }

  it("shows the selected translate and subtitle models with japanese states", async () => {
    mock.handlers.set(
      "get-state",
      () =>
        uiState({
          status: {
            version: "0",
            gpu: { backend: "cuda", name: "RTX", vram_total_mb: 16376, experimental_igpu: false, available_ram_mb: 32000 },
            suggestion: { model_id: "plamo-3-translate-8b-GGUF", reason: "VRAM 16GB", alternatives: [], context_size: 8192, n_gpu_layers: 99 },
            llama: { state: "stopped", model_id: null, port: null },
            current: currentState(),
            active_sessions: { subtitles: 0 },
          },
        }),
    );
    await renderSettingsApp();
    const header = document.querySelector("header.signal")!;
    expect(header.textContent).toContain("翻訳");
    expect(header.textContent).toContain("PLaMo 3 Translate 8B · Q4_K_M");
    expect(header.textContent).toContain("待機中（初回利用時に起動）");
    expect(header.textContent).toContain("CUDA · ctx 8192 · 並列 2");
    expect(header.textContent).toContain("字幕");
    expect(header.textContent).toContain("Haas-ja v1.0 · Q8_0");
    expect(header.textContent).toContain("待機中（字幕開始時に起動） · GPU · speech.cpp");
    // 推奨が使用中と同じなので切り替えボタンも案内も出ない。
    expect(findButton("推奨に切り替え")).toBeUndefined();
  });

  it("offers switching to a downloaded suggestion and defers while running", async () => {
    const activated: string[] = [];
    mock.handlers.set(
      "get-state",
      () =>
        uiState({
          models: [
            modelRow({}),
            modelRow({ id: "plamo-3-translate-8b-GGUF", display_name: "PLaMo 3 Translate 8B", status: "ready" }),
            modelRow({ id: "plamo-3-translate-31b-GGUF-Q2_K", display_name: "PLaMo 3 Translate 31B", status: "ready" }),
          ],
          status: {
            version: "0",
            gpu: { backend: "cuda", name: "RTX", vram_total_mb: 16376, experimental_igpu: false, available_ram_mb: 32000 },
            suggestion: { model_id: "plamo-3-translate-8b-GGUF", reason: "VRAM 16GB", alternatives: [], context_size: 8192, n_gpu_layers: 99 },
            llama: { state: "stopped", model_id: null, port: null },
            current: currentState(),
            active_sessions: { subtitles: 0 },
          },
        }),
    );
    mock.handlers.set("activate-model", (message) => {
      activated.push(String(message.modelId));
      return { status: "ready" };
    });
    await renderSettingsApp();
    const header = document.querySelector("header.signal")!;
    // 推奨は 8B（ダウンロード済み・非使用中）なので切り替えを提示する。
    expect(header.textContent).toContain("推奨: PLaMo 3 Translate 8B");
    (findButton("推奨に切り替え") as HTMLButtonElement).click();
    await waitFor(() => activated.length === 1);
    expect(activated).toEqual(["plamo-3-translate-8b-GGUF"]);
  });

  it("guides to download when the suggested model is missing", async () => {
    mock.handlers.set(
      "get-state",
      () =>
        uiState({
          models: [modelRow({}), modelRow({ id: "plamo-3-translate-31b-GGUF-Q2_K", display_name: "PLaMo 3 Translate 31B", status: "missing", consented: false })],
          status: {
            version: "0",
            gpu: { backend: "cuda", name: "RTX", vram_total_mb: 16376, experimental_igpu: false, available_ram_mb: 32000 },
            suggestion: { model_id: "plamo-3-translate-31b-GGUF-Q2_K", reason: "VRAM 16GB では 8B Q4_K_M、または 31B Q2_K", alternatives: [], context_size: 8192, n_gpu_layers: 99 },
            llama: { state: "stopped", model_id: null, port: null },
            current: currentState(),
            active_sessions: { subtitles: 0 },
          },
        }),
    );
    await renderSettingsApp();
    expect(document.body.textContent).toContain("未ダウンロードです。一覧の「推奨」の行からダウンロードできます。");
    expect(findButton("推奨に切り替え")).toBeUndefined();
  });

  it("marks a pending subtitle serve restart and backend errors", async () => {
    mock.handlers.set(
      "get-state",
      () =>
        uiState({
          status: {
            version: "0",
            gpu: { backend: "cpu", name: "CPU", vram_total_mb: null, experimental_igpu: false, available_ram_mb: 8000 },
            suggestion: { model_id: "plamo-3-translate-0.8b-GGUF", reason: "r", alternatives: [], context_size: 2048, n_gpu_layers: 0 },
            llama: { state: "ready", model_id: "plamo-3-translate-8b-GGUF", port: 9 },
            current: currentState({
              translate: { state: "ready", backend: null, backend_error: "CUDA が検出できません", loaded: { id: "plamo-3-translate-0.8b-GGUF" } },
              asr: { restart_required: true },
            }),
            active_sessions: { subtitles: 1 },
          },
        }),
    );
    await renderSettingsApp();
    const header = document.querySelector("header.signal")!;
    expect(header.textContent).toContain("CUDA が検出できません");
    expect(header.textContent).toContain("次の字幕開始時に切り替え");
    expect(header.textContent).toContain("切り替え中");
  });

  it("shows the disconnected state when the daemon is unreachable", async () => {
    mock.handlers.set("get-state", () => ({ connected: false, events: [] }));
    await renderSettingsApp();
    expect(document.querySelector("header.signal")!.textContent).toContain("未接続");
  });
});

describe("settings app subtitle section", () => {
  it("shows the active asr model read-only without engine selects", async () => {
    mock.handlers.set(
      "get-state",
      () =>
        uiState({
          models: [modelRow({}), modelRow({ id: "Haas-ja-v1.0", kind: "asr", display_name: "Haas-ja v1.0", active: true })],
          status: {
            version: "0",
            gpu: { backend: "cpu", name: "CPU", vram_total_mb: null, experimental_igpu: false, available_ram_mb: 8000 },
            suggestion: { model_id: "plamo-3-translate-0.8b-GGUF", reason: "r", alternatives: [], context_size: 2048, n_gpu_layers: 0 },
            llama: { state: "stopped", model_id: null, port: null },
            current: {
              translate: {
                selected: { id: "plamo-3-translate-0.8b-GGUF", label: "PLaMo 3 Translate 0.8B", quant: "Q4_K_M", short_label: "0.8B" },
                loaded: null,
                state: "stopped",
                backend: "cpu",
                backend_error: null,
                context_size: 2048,
                n_gpu_layers: 0,
                parallel: 1,
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
        }),
    );
    await renderSettingsApp();
    // エンジンと whisper モデルの選択はもうない。
    expect([...document.querySelectorAll("label")].map((label) => textOf(label))).not.toContain("エンジン");
    expect([...document.querySelectorAll("label")].map((label) => textOf(label))).not.toContain("whisper モデル");
    const section = [...document.querySelectorAll("section")].find((node) => textOf(node).includes("字幕と音声認識"))!;
    expect(section.textContent).toContain("音声認識モデル: Haas-ja v1.0");
    expect(section.textContent).toContain("モデル一覧の「使う」で切り替えます");
  });
});
