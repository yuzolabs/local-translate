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
    mock.handlers.set("get-state", () =>
      uiState({
        models: [modelRow({ id: "plamo-3-translate-8b-GGUF", status: "missing", consented: true, active: false })],
      }),
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
