import { useEffect, useState } from "preact/hooks";
import { TRANSLATION_LANGUAGES } from "../lib/languages";
import type { AsrCurrent, CurrentModels, ModelRow, Settings, TranslateCurrent, UiState } from "../lib/types";

interface LicenseView {
  name: string;
  url: string;
  summary: string;
  fetched_text?: string | null;
}

interface CommandView {
  name?: string;
  description?: string;
  shortcut?: string;
}

const empty: UiState = { connected: false, events: [] };

const MODEL_KIND_ORDER = ["translate", "asr", "vad"] as const;
const MODEL_KIND_LABELS: Record<string, string> = {
  translate: "翻訳",
  asr: "音声認識",
  vad: "発話区間検出",
};

export function SettingsApp() {
  const [state, setState] = useState<UiState>(empty);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState("");
  const [modelPath, setModelPath] = useState("");
  const [host, setHost] = useState("");
  const [term, setTerm] = useState({ source: "", target: "" });
  const [commands, setCommands] = useState<CommandView[]>([]);
  const [autoHost, setAutoHost] = useState(false);

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
    void chrome.runtime.sendMessage({ type: "list-commands" }).then((rows) => {
      if (Array.isArray(rows)) setCommands(rows as CommandView[]);
    });
    return () => port.disconnect();
  }, []);

  const settings = state.settings;
  const current = state.status?.current;

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
        {state.connected && current ? (
          <CurrentModelsPanel current={current} models={state.models ?? []} suggestion={state.status?.suggestion} onChange={() => void reload()} />
        ) : (
          <div class="signal-path">
            <span class={state.connected ? "dot live" : "dot"} />
            <span>{state.connected ? "コンパニオンに接続しました" : "未接続"}</span>
          </div>
        )}
        <p class="note">{state.error || (!state.connected ? "コンパニオンの起動を待っています。" : "")}</p>
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
              <LanguageSelect value={settings.target_lang} includeAuto={false} onChange={(value) => void save({ target_lang: value })} />
            </label>
            <label>翻訳元
              <LanguageSelect value={settings.source_lang} includeAuto onChange={(value) => void save({ source_lang: value })} />
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
        {MODEL_KIND_ORDER.map((kind) => {
          const rows = (state.models ?? []).filter((model) => model.kind === kind);
          if (!rows.length) return null;
          return (
            <div class="models" key={kind}>
              <h3>{MODEL_KIND_LABELS[kind]}</h3>
              {rows.map((model) => (
                <ModelLine
                  key={model.id}
                  model={model}
                  suggested={model.id === state.status?.suggestion.model_id}
                  onChange={() => void reload()}
                />
              ))}
            </div>
          );
        })}
        {(state.models ?? []).filter((model) => !MODEL_KIND_ORDER.includes(model.kind as (typeof MODEL_KIND_ORDER)[number])).map((model) => (
          <ModelLine key={model.id} model={model} suggested={false} onChange={() => void reload()} />
        ))}
        {(state.events ?? []).slice(-3).map((event, index) => (
          <p class="note" key={`${event.at ?? index}-${event.type}`}>
            {event.type}
            {event.model_id ? ` ${event.model_id}` : ""}
            {typeof event.progress === "number" ? ` ${Math.round(event.progress * 100)}%` : ""}
            {event.message ? ` — ${event.message}` : ""}
          </p>
        ))}
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
          {/* 音声認識の選択はモデル一覧の「使う」に統一し、ここでは読み取り専用。 */}
          <p class="note">
            音声認識モデル: {current?.asr.selected.label ?? settings.active_asr_model}
            {current?.asr.restart_required ? "（次の字幕開始時に切り替え）" : ""} — モデル一覧の「使う」で切り替えます
          </p>
          <div class="inline">
            <label>字幕位置
              <select value={settings.subtitle_style.position} onChange={(event) => void save({ subtitle_style: { ...settings.subtitle_style, position: (event.target as HTMLSelectElement).value as "top" | "bottom" } })}>
                <option value="bottom">下</option>
                <option value="top">上</option>
              </select>
            </label>
            <label>文字サイズ
              <input type="number" value={settings.subtitle_style.font_size_px} onChange={(event) => void save({ subtitle_style: { ...settings.subtitle_style, font_size_px: Number((event.target as HTMLInputElement).value) } })} />
            </label>
            <label>文字色
              <input value={settings.subtitle_style.text_color} onChange={(event) => void save({ subtitle_style: { ...settings.subtitle_style, text_color: (event.target as HTMLInputElement).value } })} />
            </label>
            <label>
              <input type="checkbox" checked={settings.subtitle_style.show_original} onChange={(event) => void save({ subtitle_style: { ...settings.subtitle_style, show_original: (event.target as HTMLInputElement).checked } })} />
              原文も表示
            </label>
            <label>
              <input type="checkbox" checked={settings.translate_interim} onChange={(event) => void save({ translate_interim: (event.target as HTMLInputElement).checked })} />
              仮確定も翻訳（既定オフ）
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
            <label>
              <input type="checkbox" checked={autoHost} onChange={(event) => setAutoHost((event.target as HTMLInputElement).checked)} />
              開いたら翻訳
            </label>
            <button type="button" onClick={() => void save({ site_rules: [...settings.site_rules, { host, auto_translate: autoHost, enabled: true }] }).then(() => setHost(""))}>追加</button>
          </div>
        </section>
      )}

      <section>
        <h2>ショートカット</h2>
        <div class="glossary">
          {commands.filter((command) => command.description).map((command) => (
            <div class="inline" key={command.name}>
              <span>{command.description}</span>
              <span class="stamp">{command.shortcut || "未設定"}</span>
            </div>
          ))}
        </div>
        <p class="note">キーの変更は chrome://extensions/shortcuts で行います。</p>
      </section>

      <section>
        <h2>About</h2>
        <p>{state.about?.built_with ?? "Built with PLaMo"}</p>
        <p class="note">PLaMo Community License。モデル重みは再配布せず、初回ダウンロード時に同意を取ります。</p>
        <p class="note">Haas-ja は Apache 2.0。speech.cpp は MIT。</p>
        <p class="note">{state.about?.web_store.reason}</p>
        <p class="note">{state.about?.windows_haas.reason}</p>
        <p class="note">{state.about?.multilingual_asr.reason}</p>
      </section>
    </main>
  );
}

// 選択中のモデルは「使用中」、Runtime に読み込まれているモデルは「稼働中」。
function translateStateLabel(translate: TranslateCurrent): string {
  if (translate.state === "ready") return "稼働中";
  if (translate.state === "starting") return "起動中";
  if (translate.state === "not_downloaded") return "未ダウンロード";
  return "待機中（初回利用時に起動）";
}

function asrStateLabel(asr: AsrCurrent): string {
  if (asr.restart_required) return "次の字幕開始時に切り替え";
  if (asr.state === "ready") return "稼働中";
  if (asr.state === "not_downloaded") return "未ダウンロード";
  return "待機中（字幕開始時に起動）";
}

function backendLabel(backend: string | null): string {
  if (!backend) return "";
  return backend.toUpperCase();
}

function deviceLabel(device: string): string {
  return device === "gpu" ? "GPU" : device.toUpperCase();
}

function quantLabel(quant: string | undefined): string {
  return quant && quant !== "custom" ? ` · ${quant}` : "";
}

function CurrentModelsPanel({
  current,
  models,
  suggestion,
  onChange,
}: {
  current: CurrentModels;
  models: ModelRow[];
  suggestion?: { model_id: string; reason: string };
  onChange: () => void;
}) {
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState("");
  const translate = current.translate;
  const asr = current.asr;
  const switchingModel = translate.loaded && translate.loaded.id !== translate.selected.id;
  const translateDetail = [
    translateStateLabel(translate),
    translate.backend ? `${backendLabel(translate.backend)} · ctx ${translate.context_size ?? "—"} · 並列 ${translate.parallel ?? "—"}` : "",
    translate.backend_error ?? "",
  ]
    .filter(Boolean)
    .join(" · ");
  const asrDetail = [asrStateLabel(asr), deviceLabel(asr.device), asr.runtime].filter(Boolean).join(" · ");
  const suggestedRow = suggestion ? models.find((model) => model.id === suggestion.model_id) : undefined;
  const suggestedReady = suggestedRow?.status === "ready" || suggestedRow?.status === "running";
  const suggestedActive = Boolean(suggestedRow?.active);

  async function switchToSuggested(): Promise<void> {
    if (!suggestion) return;
    setSwitching(true);
    setSwitchError("");
    try {
      const response = (await chrome.runtime.sendMessage({ type: "activate-model", modelId: suggestion.model_id })) as
        | { error?: string }
        | undefined;
      if (response && typeof response === "object" && response.error) {
        setSwitchError(response.error);
        return;
      }
      onChange();
    } finally {
      setSwitching(false);
    }
  }

  return (
    <div class="current-models">
      <div class="current-line">
        <span class={translate.state === "ready" ? "dot live" : "dot"} />
        <strong>翻訳</strong>
        <span>{translate.selected.label}{quantLabel(translate.selected.quant)}</span>
        {switchingModel && <span class="badge suggested">切り替え中</span>}
      </div>
      <p class="note">{translateDetail}</p>
      <div class="current-line">
        <span class={asr.state === "ready" ? "dot live" : "dot"} />
        <strong>字幕</strong>
        <span>{asr.selected.label}{quantLabel(asr.selected.quant)}</span>
      </div>
      <p class="note">{asrDetail}</p>
      {suggestion && suggestedRow && (
        <div class="inline">
          <span class="note">
            推奨: {suggestedRow.display_name ?? suggestion.model_id}{quantLabel(suggestedRow.quant)} — {suggestion.reason}
          </span>
          {suggestedReady && !suggestedActive && (
            <button type="button" disabled={switching} onClick={() => void switchToSuggested()}>
              {switching ? "切り替え中…" : "推奨に切り替え"}
            </button>
          )}
          {!suggestedReady && !suggestedActive && (
            <span class="note">未ダウンロードです。一覧の「推奨」の行からダウンロードできます。</span>
          )}
          {switchError && <span class="fault">{switchError}</span>}
        </div>
      )}
    </div>
  );
}

function ModelLine({ model, suggested, onChange }: { model: ModelRow; suggested: boolean; onChange: () => void }) {
  const [license, setLicense] = useState<LicenseView | null>(null);
  const [licenseError, setLicenseError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyAction, setBusyAction] = useState("");

  async function showLicense(): Promise<void> {
    setLicenseError("");
    const response = (await chrome.runtime.sendMessage({ type: "model-license", modelId: model.id })) as LicenseView & { error?: string };
    if (response?.error) {
      setLicenseError(response.error);
      return;
    }
    setLicense(response);
  }

  // 「使う」は llama-server の起動待ち（31B では数秒以上）、「ダウンロード」は開始まで
  // 時間がかかる。押している間は行内に状況を出し、background が返す { error } を捨てない。
  async function run(action: "consent" | "download-model" | "activate-model"): Promise<void> {
    setBusyAction(action);
    setActionError("");
    try {
      const response = (await chrome.runtime.sendMessage({ type: action, modelId: model.id })) as
        | { error?: string }
        | undefined;
      if (response && typeof response === "object" && response.error) {
        setActionError(response.error);
        return;
      }
      onChange();
    } finally {
      setBusyAction("");
    }
  }

  const busyNote = busyAction === "activate-model" ? "切り替え中…" : busyAction === "download-model" ? "ダウンロードを開始しています…" : busyAction === "consent" ? "記録しています…" : "";
  // 選択中は「使用中」、Runtime に読み込まれているモデルは「稼働中」。
  const active = Boolean(model.active);
  const running = model.status === "running";
  const usable = model.status === "ready" || running;
  const statusText = running
    ? "稼働中"
    : model.status === "ready"
      ? "ダウンロード済み"
      : model.status === "downloading"
        ? `ダウンロード中 ${Math.round((model.progress ?? 0) * 100)}%`
        : model.status === "error"
          ? "ダウンロードに失敗しました"
          : "未ダウンロード";
  // 発話区間検出は選ぶ対象ではなく、字幕が内部で使う。
  const selectable = model.kind !== "vad";

  return (
    <div class={active ? "model active" : "model"}>
      <div>
        <div class="model-title">
          <strong>{model.display_name ?? model.id}</strong>
          {active && <span class="badge active">使用中</span>}
          {suggested && !active && <span class="badge suggested">推奨</span>}
        </div>
        <div class="note">
          {model.id}{model.quant && model.quant !== "custom" ? ` · ${model.quant}` : ""} · {statusText}
          {model.size_gb ? ` · ${model.size_gb}GB` : ""}
        </div>
        {model.note && <div class="note">{model.note}</div>}
        {busyNote && <div class="note">{busyNote}</div>}
        {actionError && <div class="fault">{actionError}</div>}
        {license && (
          <div class="note">
            <div>{license.name}</div>
            <div>{license.summary}</div>
            {license.url && <a href={license.url} target="_blank" rel="noreferrer">{license.url}</a>}
            {license.fetched_text && <pre class="license-text">{license.fetched_text.slice(0, 1800)}</pre>}
          </div>
        )}
        {licenseError && <div class="fault">{licenseError}</div>}
      </div>
      <div class="inline">
        {!model.consented && <button type="button" disabled={Boolean(busyAction)} onClick={() => void showLicense()}>ライセンスを見る</button>}
        {!model.consented && license && (
          <button type="button" disabled={Boolean(busyAction)} onClick={() => void run("consent")}>同意して記録する</button>
        )}
        {model.consented && !usable && (
          <button type="button" disabled={Boolean(busyAction)} onClick={() => void run("download-model")}>ダウンロード</button>
        )}
        {selectable && !active && (
          <button
            type="button"
            disabled={Boolean(busyAction) || !usable}
            title={usable ? undefined : "未ダウンロードです。ダウンロードしてから選べます。"}
            onClick={() => void run("activate-model")}
          >
            使う
          </button>
        )}
      </div>
    </div>
  );
}

function LanguageSelect({ value, includeAuto, onChange }: { value: string; includeAuto: boolean; onChange: (value: string) => void }) {
  return (
    <select value={value} onChange={(event) => onChange((event.target as HTMLSelectElement).value)}>
      {includeAuto && <option value="auto">自動判別</option>}
      {TRANSLATION_LANGUAGES.map(([code, name]) => (
        <option key={code} value={code}>{name}</option>
      ))}
    </select>
  );
}

function numberOrNull(event: Event): number | null {
  const value = (event.target as HTMLInputElement).value;
  return value === "" ? null : Number(value);
}
