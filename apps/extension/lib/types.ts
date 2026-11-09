export interface GlossaryEntry {
  source: string;
  target: string;
}

export interface SubtitleStyle {
  font_size_px: number;
  text_color: string;
  background: string;
  position: "bottom" | "top";
  show_original: boolean;
}

export interface SiteRule {
  host: string;
  target_lang?: string;
  source_lang?: string;
  form?: "polite" | "plain";
  glossary?: GlossaryEntry[];
  auto_translate?: boolean;
  enabled?: boolean;
}

export interface Settings {
  target_lang: string;
  source_lang: string;
  form: "polite" | "plain";
  glossary: GlossaryEntry[];
  gpu_backend: "auto" | "cuda" | "vulkan" | "cpu";
  n_gpu_layers: number | null;
  context_size: number | null;
  parallel: number | null;
  idle_timeout_sec: number;
  active_translate_model: string;
  active_asr_model: string;
  asr_engine: "haas" | "whisper" | "qwen3-asr";
  subtitle_backend: "speech-serve" | "segment-worker";
  translate_interim: boolean;
  subtitle_style: SubtitleStyle;
  site_rules: SiteRule[];
  experimental_igpu: boolean;
  whisper_model: string;
}

export interface Caption {
  seq: number;
  start_ms: number;
  end_ms: number | null;
  text: string;
  translation: string | null;
  final: boolean;
}

export interface ModelRow {
  id: string;
  kind: string;
  status: string;
  progress?: number;
  consented: boolean;
  license_id: string;
  preset: boolean;
  note?: string;
  size_gb?: number | null;
  quant?: string;
  active?: boolean;
  path?: string;
}

export interface DaemonStatus {
  version: string;
  gpu: {
    backend: string;
    name: string;
    vram_total_mb: number | null;
    experimental_igpu: boolean;
    available_ram_mb: number | null;
  };
  suggestion: {
    model_id: string;
    reason: string;
    alternatives: string[];
    context_size: number;
    n_gpu_layers: number;
  };
  llama: { state: string; model_id: string | null; port: number | null };
  active_sessions: { subtitles: number };
}

export interface AboutInfo {
  built_with: string;
  web_store: { decision: string; reason: string };
  windows_haas: { decision: string; reason: string };
  multilingual_asr: { decision: string; reason: string };
}

export interface UiState {
  connected: boolean;
  error?: string;
  status?: DaemonStatus;
  models?: ModelRow[];
  settings?: Settings;
  about?: AboutInfo;
  events: Array<{ type: string; message?: string; model_id?: string }>;
}

export const TRANSLATION_PRIORITY = {
  selection: 0,
  input: 1,
  subtitle: 2,
  visibleBlock: 3,
  backgroundBlock: 4,
} as const;
