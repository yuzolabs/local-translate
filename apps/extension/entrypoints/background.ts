import { DaemonClient, type DaemonEndpoint } from "../lib/daemon-client";
import { NATIVE_HOST_NAME } from "../lib/identity";
import { effectiveSettings, hostShouldAutoTranslate } from "../lib/site-settings";
import { LruTranslationCache, translationCacheKey, type CacheStorage } from "../lib/translation-cache";
import { TranslationQueue } from "../lib/translation-queue";
import type { Caption, Settings, UiState } from "../lib/types";
import { TRANSLATION_PRIORITY } from "../lib/types";

interface TranslateJob {
  requestId: string;
  text: string;
  priority: number;
  sourceLang?: string;
  targetLang?: string;
  context?: string;
  tabId?: number;
}

let endpoint: DaemonEndpoint | null = null;
let nativePort: chrome.runtime.Port | null = null;
let connectionError = "";
let connecting: Promise<void> | null = null;
let uiState: UiState = { connected: false, events: [] };
let eventsSocket: WebSocket | null = null;
let eventsReconnect: ReturnType<typeof setTimeout> | null = null;
let lastProgressRefreshAt = 0;
const queue = new TranslationQueue(1);
const cache = new LruTranslationCache(400, chromeStorage());
const ports = new Set<chrome.runtime.Port>();

export default defineBackground(() => {
  chrome.runtime.onInstalled.addListener(() => {
    registerMenus();
  });
  registerMenus();
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => undefined);
  chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (!tab?.id) return;
    if (info.menuItemId === "translate-selection") {
      chrome.tabs.sendMessage(tab.id, { type: "translate-selection", text: info.selectionText ?? "" });
    }
    if (info.menuItemId === "translate-page") {
      chrome.tabs.sendMessage(tab.id, { type: "start-page-translation" });
    }
  });
  chrome.commands.onCommand.addListener(async (command) => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return;
    if (command === "translate-selection") chrome.tabs.sendMessage(tab.id, { type: "translate-selection" });
    if (command === "translate-input") chrome.tabs.sendMessage(tab.id, { type: "translate-input" });
  });
  chrome.runtime.onConnect.addListener((port) => {
    ports.add(port);
    port.onDisconnect.addListener(() => ports.delete(port));
    if (port.name === "translate") {
      port.onMessage.addListener((message) => {
        void handleTranslatePort(port, message as TranslateJob);
      });
    }
    if (port.name === "ui") {
      void refreshUiState();
    }
  });
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (typeof message?.type === "string" && message.type.startsWith("offscreen:")) return false;
    void handleMessage(message, sender).then(sendResponse, (error: Error) => sendResponse({ error: error.message }));
    return true;
  });
});

function registerMenus(): void {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "translate-selection", title: "選択範囲を翻訳", contexts: ["selection"] });
    chrome.contextMenus.create({ id: "translate-page", title: "ページを翻訳", contexts: ["page"] });
  });
}

async function handleMessage(message: Record<string, unknown>, sender: chrome.runtime.MessageSender): Promise<unknown> {
  switch (message.type) {
    case "get-state":
      await refreshUiState();
      return uiState;
    case "update-settings":
      return updateSettings(message.settings as Partial<Settings>);
    case "model-license":
      return (await client()).getJson(`/v1/models/${message.modelId}/license`);
    case "list-commands":
      return chrome.commands.getAll();
    case "consent":
      return (await client()).postJson(`/v1/models/${message.modelId}/consent`, { accepted: true });
    case "download-model":
      return (await client()).postJson(`/v1/models/${message.modelId}/download`, {});
    case "activate-model":
      return (await client()).postJson(`/v1/models/${message.modelId}/activate`, {});
    case "register-model":
      return (await client()).postJson("/v1/models", { id: message.id, path: message.path });
    case "install-runtime":
      return (await client()).postJson(`/v1/runtimes/${message.runtime}/install`, {});
    case "try-translate":
      return translateText(String(message.text ?? ""), TRANSLATION_PRIORITY.selection, {});
    case "effective-settings":
      return effectiveSettings(await loadSettings(), String(message.host ?? ""));
    case "should-auto-translate":
      return hostShouldAutoTranslate(await loadSettings(), String(message.host ?? ""));
    case "start-subtitles":
      return startSubtitles(sender.tab?.id);
    case "stop-subtitles":
      return stopSubtitles();
    case "caption":
      if (sender.tab?.id) return undefined;
      await forwardCaption(message.caption as Caption);
      return undefined;
    default:
      return { error: "不明なメッセージです" };
  }
}

async function handleTranslatePort(port: chrome.runtime.Port, job: TranslateJob): Promise<void> {
  try {
    const text = await queue.enqueue(job.priority, () =>
      translateText(job.text, job.priority, {
        sourceLang: job.sourceLang,
        targetLang: job.targetLang,
        context: job.context,
        onDelta: (partial) => port.postMessage({ type: "delta", requestId: job.requestId, text: partial }),
      }),
    );
    port.postMessage({ type: "done", requestId: job.requestId, text });
  } catch (error) {
    port.postMessage({
      type: "error",
      requestId: job.requestId,
      message: error instanceof Error ? error.message : "翻訳に失敗しました",
    });
  }
}

async function translateText(
  text: string,
  _priority: number,
  options: {
    sourceLang?: string;
    targetLang?: string;
    context?: string;
    onDelta?: (text: string) => void;
  },
): Promise<string> {
  const settings = await loadSettings();
  const sourceLang = options.sourceLang ?? settings.source_lang;
  const targetLang = options.targetLang ?? settings.target_lang;
  const key = await translationCacheKey({
    text,
    sourceLang,
    targetLang,
    modelId: settings.active_translate_model,
    form: settings.form,
    glossary: settings.glossary,
    context: options.context,
  });
  const cached = await cache.get(key);
  if (cached) {
    options.onDelta?.(cached);
    return cached;
  }
  const daemon = await client();
  const translated = await daemon.translate(
    {
      text,
      source_lang: sourceLang,
      target_lang: targetLang,
      form: settings.form,
      glossary: settings.glossary,
      context: options.context,
      model_id: settings.active_translate_model,
    },
    (partial) => options.onDelta?.(partial),
  );
  await cache.set(key, translated);
  return translated;
}

async function updateSettings(patch: Partial<Settings>): Promise<Settings> {
  const daemon = await client();
  const settings = await daemon.postJson<Settings>("/v1/settings", patch);
  await chrome.storage.local.set({ settings });
  queue.setConcurrency(settings.parallel ?? 1);
  await refreshUiState();
  return settings;
}

async function loadSettings(): Promise<Settings> {
  try {
    const settings = await (await client()).getJson<Settings>("/v1/settings");
    await chrome.storage.local.set({ settings });
    queue.setConcurrency(settings.parallel ?? 1);
    return settings;
  } catch (error) {
    const stored = await chrome.storage.local.get("settings");
    if (stored.settings) return stored.settings as Settings;
    throw error;
  }
}

async function client(): Promise<DaemonClient> {
  if (!endpoint) {
    connecting ??= connectNative().finally(() => {
      connecting = null;
    });
    await connecting;
  }
  if (!endpoint) throw new Error(connectionError || "コンパニオンに接続できていません");
  return new DaemonClient(endpoint);
}

async function connectNative(): Promise<void> {
  endpoint = await new Promise((resolve, reject) => {
    const port = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    nativePort = port;
    const timer = setTimeout(() => reject(new Error("ネイティブホストが応答しません")), 15000);
    port.onMessage.addListener((message: { type?: string; message?: string; port?: number; token?: string }) => {
      clearTimeout(timer);
      if (message.type === "error" || !message.port || !message.token) {
        reject(new Error(message.message ?? "ハンドシェイクに失敗しました"));
        return;
      }
      resolve({ port: message.port, token: message.token });
    });
    port.onDisconnect.addListener(() => {
      if (nativePort === port) nativePort = null;
      clearTimeout(timer);
      reject(new Error(chrome.runtime.lastError?.message ?? "ネイティブホストが切断されました"));
    });
    port.postMessage({ type: "hello" });
  }).catch((error: Error) => {
    connectionError = error.message;
    endpoint = null;
    return null;
  });
  if (endpoint) connectionError = "";
}

async function refreshUiState(): Promise<void> {
  try {
    const daemon = await client();
    const [status, models, settings, about] = await Promise.all([
      daemon.getJson<UiState["status"]>("/v1/status"),
      daemon.getJson<{ models: UiState["models"] }>("/v1/models"),
      daemon.getJson<Settings>("/v1/settings"),
      daemon.getJson<UiState["about"]>("/v1/about"),
    ]);
    uiState = {
      connected: true,
      status,
      models: models.models,
      settings,
      about,
      events: uiState.events,
    };
    await chrome.storage.local.set({ settings });
    ensureEventsSocket();
  } catch (error) {
    uiState = {
      connected: false,
      error: error instanceof Error ? error.message : connectionError,
      events: uiState.events,
      settings: uiState.settings,
    };
  }
  pushUiState();
}

function pushUiState(): void {
  for (const port of ports) {
    if (port.name === "ui") port.postMessage({ type: "state", state: uiState });
  }
}

function ensureEventsSocket(): void {
  if (!endpoint) return;
  if (eventsSocket && (eventsSocket.readyState === WebSocket.OPEN || eventsSocket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  eventsSocket = null;
  const socket = new WebSocket(`ws://127.0.0.1:${endpoint.port}/api/events`);
  eventsSocket = socket;
  socket.addEventListener("open", () => {
    if (endpoint) socket.send(JSON.stringify({ type: "auth", token: endpoint.token }));
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") return;
    try {
      recordEvent(JSON.parse(event.data) as UiState["events"][number]);
    } catch {
      return;
    }
  });
  socket.addEventListener("close", () => {
    if (eventsSocket === socket) eventsSocket = null;
    if (eventsReconnect) clearTimeout(eventsReconnect);
    eventsReconnect = setTimeout(() => ensureEventsSocket(), 2000);
  });
}

function recordEvent(event: UiState["events"][number]): void {
  uiState.events = [...uiState.events, event].slice(-20);
  const kind = String(event.type ?? "");
  if (kind === "model.download.progress") {
    const now = Date.now();
    if (now - lastProgressRefreshAt < 900) {
      pushUiState();
      return;
    }
    lastProgressRefreshAt = now;
    void refreshUiState();
    return;
  }
  if (kind.startsWith("model.download") || kind.startsWith("llama.") || kind.startsWith("asr.")) {
    void refreshUiState();
    return;
  }
  pushUiState();
}

async function startSubtitles(senderTabId?: number): Promise<{ ok: true }> {
  const [tab] = senderTabId
    ? [{ id: senderTabId }]
    : await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("字幕を出すタブがありません");
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  await ensureOffscreen();
  const daemon = await client();
  await chrome.runtime.sendMessage({
    type: "offscreen:start",
    streamId,
    port: endpoint?.port,
    token: endpoint?.token,
    tabId: tab.id,
  });
  void daemon;
  await chrome.tabs.sendMessage(tab.id, { type: "subtitle-session", active: true });
  return { ok: true };
}

async function stopSubtitles(): Promise<{ ok: true }> {
  await chrome.runtime.sendMessage({ type: "offscreen:stop" }).catch(() => undefined);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id) await chrome.tabs.sendMessage(tab.id, { type: "subtitle-session", active: false }).catch(() => undefined);
  return { ok: true };
}

async function forwardCaption(caption: Caption): Promise<void> {
  const stored = await chrome.storage.session.get("subtitleTabId");
  const tabId = stored.subtitleTabId as number | undefined;
  if (!tabId) return;
  await chrome.tabs.sendMessage(tabId, { type: "caption", caption });
}

async function ensureOffscreen(): Promise<void> {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT" as chrome.runtime.ContextType] });
  if (contexts.length) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "タブの音声を字幕用に 16kHz PCM へ変換するため",
  });
}

function chromeStorage(): CacheStorage {
  return {
    async read() {
      const stored = await chrome.storage.local.get("translationCache");
      return (stored.translationCache as Record<string, string>) ?? {};
    },
    async write(entries) {
      await chrome.storage.local.set({ translationCache: entries });
    },
  };
}

export { hostShouldAutoTranslate };
