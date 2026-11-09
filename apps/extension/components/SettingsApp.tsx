import { useEffect, useState } from "preact/hooks";
import type { ModelRow, Settings, UiState } from "../lib/types";

const empty: UiState = { connected: false, events: [] };

export function SettingsApp() {
  const [state, setState] = useState<UiState>(empty);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState("");
  const [modelPath, setModelPath] = useState("");
  const [host, setHost] = useState("");
  const [term, setTerm] = useState({ source: "", target: "" });

  async function reload(): Promise<void> {
    const next = (await chrome.runtime.sendMessage({ type: "get-state" })) as UiState;
    setState(next);
  }

  useEffect(() => {
    const port = chrome.runtime.connect({ name: "ui" });
    port.onMessage.addListener((message) => {
      if (message.type === "state") setState(message.state as UiState);
    });
    void reload();
    return () => port.disconnect();
  }, []);

  const settings = state.settings;
  const port = state.status?.llama.port ?? state.status?.suggestion.model_id;

  async function save(patch: Partial<Settings>): Promise<void> {
    await chrome.runtime.sendMessage({ type: "update-settings", settings: patch });
    await reload();
  }

  async function tryTranslate(): Promise<void> {
    setResult("翻訳しています…");
    const response = (await chrome.runtime.sendMessage({ type: "try-translate", text: draft })) as { error?: string } | string;
    setResult(typeof response === "string" ? response : response.error ?? "翻訳結果がありません");
  }

  return (
    <main class="app">
      <header class="signal">
        <div class="signal-path">
          <span class={state.connected ? "dot live" : "dot"} />
          <span>拡張</span>
          <span>→</span>
          <span>127.0.0.1</span>
          <span>→</span>
          <span>{state.status?.llama.state ?? "未接続"}</span>
          <span class="stamp">{state.connected ? `:${state.status?.llama.port ?? "—"}` : "切断"}</span>
        </div>
        <p class="note">{state.error || state.status?.suggestion.reason || "コンパニオンの起動を待っています。"}</p>
      </header>

      <section>
        <h1>試し翻訳</h1>
        <textarea value={draft} onInput={(event) => setDraft((event.target as HTMLTextAreaElement).value)} placeholder="ここに原文を入れる" />
        <div class="inline">
          <button type="button" onClick={() => void tryTranslate()}>翻訳する</button>
          <button type="button" onClick={() => void chrome.runtime.sendMessage({ type: "start-subtitles" })}>字幕を開始</button>
          <button type="button" onClick={() => void chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => tab?.id && chrome.tabs.sendMessage(tab.id, { type: "start-page-translation" }))}>このページを翻訳</button>
        </div>
        <div class="preview">{result}</div>
      </section>

      {settings && (
        <section>
          <h2>翻訳</h2>
          <div class="inline">
            <label>翻訳先
              <input value={settings.target_lang} onChange={(event) => void save({ target_lang: (event.target as HTMLInputElement).value })} />
            </label>
            <label>翻訳元
              <input value={settings.source_lang} onChange={(event) => void save({ source_lang: (event.target as HTMLInputElement).value })} />
            </label>
            <label>文体
              <select value={settings.form} onChange={(event) => void save({ form: (event.target as HTMLSelectElement).value as Settings["form"] })}>
                <option value="polite">です・ます</option>
                <option value="plain">だ・である</option>
              </select>
            </label>
          </div>
        </section>
      )}

      {settings && (
        <section>
          <h2>用語集</h2>
          <div class="glossary">
            {settings.glossary.map((item, index) => (
              <div class="inline" key={`${item.source}-${index}`}>
                <span>{item.source} → {item.target}</span>
                <button type="button" onClick={() => void save({ glossary: settings.glossary.filter((_, itemIndex) => itemIndex !== index) })}>削除</button>
              </div>
            ))}
            <div class="inline">
              <input placeholder="原文" value={term.source} onInput={(event) => setTerm({ ...term, source: (event.target as HTMLInputElement).value })} />
              <input placeholder="訳語" value={term.target} onInput={(event) => setTerm({ ...term, target: (event.target as HTMLInputElement).value })} />
              <button type="button" onClick={() => void save({ glossary: [...settings.glossary, term] }).then(() => setTerm({ source: "", target: "" }))}>追加</button>
            </div>
          </div>
        </section>
      )}

      <section>
        <h2>モデル</h2>
        <div class="models">
          {(state.models ?? []).map((model) => (
            <ModelLine key={model.id} model={model} onChange={() => void reload()} />
          ))}
        </div>
        <div class="inline">
          <input placeholder="任意 GGUF のパス" value={modelPath} onInput={(event) => setModelPath((event.target as HTMLInputElement).value)} />
          <button type="button" onClick={() => void chrome.runtime.sendMessage({ type: "register-model", id: `custom-${Date.now()}`, path: modelPath }).then(reload)}>登録する</button>
        </div>
      </section>

      {settings && (
        <section>
          <h2>GPU と待機</h2>
          <div class="inline">
            <label>バックエンド
              <select value={settings.gpu_backend} onChange={(event) => void save({ gpu_backend: (event.target as HTMLSelectElement).value as Settings["gpu_backend"] })}>
                <option value="auto">自動</option>
                <option value="cuda">CUDA</option>
                <option value="vulkan">Vulkan</option>
                <option value="cpu">CPU</option>
              </select>
            </label>
            <label>層数 -ngl
              <input type="number" value={settings.n_gpu_layers ?? ""} onChange={(event) => void save({ n_gpu_layers: numberOrNull(event) })} />
            </label>
            <label>コンテキスト
              <input type="number" value={settings.context_size ?? ""} onChange={(event) => void save({ context_size: numberOrNull(event) })} />
            </label>
            <label>並列
              <input type="number" value={settings.parallel ?? ""} onChange={(event) => void save({ parallel: numberOrNull(event) })} />
            </label>
            <label>アイドル停止（秒）
              <input type="number" value={settings.idle_timeout_sec} onChange={(event) => void save({ idle_timeout_sec: Number((event.target as HTMLInputElement).value) })} />
            </label>
          </div>
          <p class="note">{state.status?.gpu.experimental_igpu ? "Intel 内蔵 GPU は実験的対応です。" : state.status?.gpu.name}</p>
        </section>
      )}

      {settings && (
        <section>
          <h2>字幕と音声認識</h2>
          <div class="inline">
            <label>エンジン
              <select value={settings.asr_engine} onChange={(event) => void save({ asr_engine: (event.target as HTMLSelectElement).value as Settings["asr_engine"] })}>
                <option value="haas">Haas-ja（日本語）</option>
                <option value="whisper">whisper.cpp（多言語）</option>
                <option value="qwen3-asr">Qwen3-ASR（多言語・speech.cpp）</option>
              </select>
            </label>
            <label>whisper モデル
              <select value={settings.whisper_model} onChange={(event) => void save({ whisper_model: (event.target as HTMLSelectElement).value })}>
                <option value="whisper-base">base（CPU 疎通）</option>
                <option value="whisper-large-v3-turbo">large-v3-turbo</option>
              </select>
            </label>
            <label>字幕位置
              <select value={settings.subtitle_style.position} onChange={(event) => void save({ subtitle_style: { ...settings.subtitle_style, position: (event.target as HTMLSelectElement).value as "top" | "bottom" } })}>
                <option value="bottom">下</option>
                <option value="top">上</option>
              </select>
            </label>
            <label>文字サイズ
              <input type="number" value={settings.subtitle_style.font_size_px} onChange={(event) => void save({ subtitle_style: { ...settings.subtitle_style, font_size_px: Number((event.target as HTMLInputElement).value) } })} />
            </label>
          </div>
        </section>
      )}

      {settings && (
        <section>
          <h2>サイト別</h2>
          {settings.site_rules.map((rule, index) => (
            <div class="inline" key={rule.host}>
              <span>{rule.host}</span>
              <span>{rule.target_lang ?? "共通"}</span>
              <span>{rule.auto_translate ? "自動" : "手動"}</span>
              <button type="button" onClick={() => void save({ site_rules: settings.site_rules.filter((_, ruleIndex) => ruleIndex !== index) })}>削除</button>
            </div>
          ))}
          <div class="inline">
            <input placeholder="example.com" value={host} onInput={(event) => setHost((event.target as HTMLInputElement).value)} />
            <button type="button" onClick={() => void save({ site_rules: [...settings.site_rules, { host, auto_translate: false, enabled: true }] }).then(() => setHost(""))}>追加</button>
          </div>
        </section>
      )}

      <section>
        <h2>About</h2>
        <p>{state.about?.built_with ?? "Built with PLaMo"}</p>
        <p class="note">{state.about?.web_store.reason}</p>
        <p class="note">{state.about?.multilingual_asr.reason}</p>
        <p class="note">経路の印: {String(port ?? "—")}</p>
      </section>
    </main>
  );
}

function ModelLine({ model, onChange }: { model: ModelRow; onChange: () => void }) {
  return (
    <div class="model">
      <div>
        <strong>{model.id}</strong>
        <div class="note">{model.note} / {model.status}{model.progress ? ` ${Math.round(model.progress * 100)}%` : ""}</div>
      </div>
      <div class="inline">
        {!model.consented && <button type="button" onClick={() => void chrome.runtime.sendMessage({ type: "consent", modelId: model.id }).then(onChange)}>同意する</button>}
        {model.consented && model.status !== "ready" && model.status !== "running" && (
          <button type="button" onClick={() => void chrome.runtime.sendMessage({ type: "download-model", modelId: model.id }).then(onChange)}>ダウンロード</button>
        )}
        <button type="button" onClick={() => void chrome.runtime.sendMessage({ type: "activate-model", modelId: model.id }).then(onChange)}>使う</button>
      </div>
    </div>
  );
}

function numberOrNull(event: Event): number | null {
  const value = (event.target as HTMLInputElement).value;
  return value === "" ? null : Number(value);
}
