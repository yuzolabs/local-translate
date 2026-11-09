"""Preset registry for PLaMo GGUF, Haas-ja, and the multilingual ASR models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPreset:
    model_id: str
    kind: str
    filename: str
    repo: str
    size_gb: float
    quant: str
    license_id: str
    alias: str
    runtime: str
    catalog_name: str = ""
    note: str = ""


PRESETS: tuple[ModelPreset, ...] = (
    ModelPreset(
        "plamo-3-translate-0.8b-GGUF",
        "translate",
        "plamo-3-translate-0.8b-Q4_K_M.gguf",
        "pfnet/plamo-3-translate-0.8b-GGUF",
        0.57,
        "Q4_K_M",
        "plamo-community",
        "plamo-3-translate",
        "llama.cpp",
        note="CPU と統合 GPU の開発標準",
    ),
    ModelPreset(
        "plamo-3-translate-2b-GGUF",
        "translate",
        "plamo-3-translate-2b-Q4_K_M.gguf",
        "pfnet/plamo-3-translate-2b-GGUF",
        1.81,
        "Q4_K_M",
        "plamo-community",
        "plamo-3-translate",
        "llama.cpp",
        note="低 VRAM",
    ),
    ModelPreset(
        "plamo-3-translate-8b-GGUF",
        "translate",
        "plamo-3-translate-8b-Q4_K_M.gguf",
        "pfnet/plamo-3-translate-8b-GGUF",
        5.28,
        "Q4_K_M",
        "plamo-community",
        "plamo-3-translate",
        "llama.cpp",
        note="8GB 以上",
    ),
    ModelPreset(
        "plamo-3-translate-31b-GGUF-Q4_K_M",
        "translate",
        "plamo-3-translate-31b-Q4_K_M.gguf",
        "pfnet/plamo-3-translate-31b-GGUF",
        19.53,
        "Q4_K_M",
        "plamo-community",
        "plamo-3-translate",
        "llama.cpp",
        note="24GB 以上",
    ),
    ModelPreset(
        "plamo-3-translate-31b-GGUF-Q3_K_M",
        "translate",
        "plamo-3-translate-31b-Q3_K_M.gguf",
        "pfnet/plamo-3-translate-31b-GGUF",
        15.86,
        "Q3_K_M",
        "plamo-community",
        "plamo-3-translate",
        "llama.cpp",
        note="16GB ではコンテキスト縮小とセットで選ぶ",
    ),
    ModelPreset(
        "plamo-3-translate-31b-GGUF-Q2_K",
        "translate",
        "plamo-3-translate-31b-Q2_K.gguf",
        "pfnet/plamo-3-translate-31b-GGUF",
        12.61,
        "Q2_K",
        "plamo-community",
        "plamo-3-translate",
        "llama.cpp",
        note="最小の 31B。精度は他の量子化より落ちる",
    ),
    ModelPreset(
        "Haas-ja-v1.0",
        "asr",
        "Haas-616M-ja-v1.0-Q8_0.gguf",
        "sakasegawa/Haas-ja-v1.0-GGUF",
        0.66,
        "Q8_0",
        "apache-2.0",
        "haas-ja-v1.0",
        "speech.cpp",
        catalog_name="haas-ja-v1.0",
        note="日本語 ASR の既定。speech.cpp がカタログの SHA-256 を検証して取得する",
    ),
    ModelPreset(
        "Haas-ja-v1.0-F16",
        "asr",
        "Haas-616M-ja-v1.0-F16.gguf",
        "sakasegawa/Haas-ja-v1.0-GGUF",
        1.23,
        "F16",
        "apache-2.0",
        "haas-ja-v1.0",
        "speech.cpp",
        catalog_name="haas-ja-v1.0:f16",
        note="Q8_0 との品質比較用",
    ),
    ModelPreset(
        "silero-vad",
        "vad",
        "silero-vad-309K-v6.2-F32.gguf",
        "sakasegawa/silero-vad-GGUF",
        0.001,
        "F32",
        "mit",
        "silero-vad",
        "speech.cpp",
        catalog_name="silero-vad",
        note="字幕の発話区間検出",
    ),
    ModelPreset(
        "qwen3-asr-0.6b",
        "asr",
        "Qwen3-ASR-0.6B-Q8_0.gguf",
        "sakasegawa/Qwen3-ASR-0.6B-GGUF",
        0.84,
        "Q8_0",
        "apache-2.0",
        "qwen3-asr-0.6b",
        "speech.cpp",
        catalog_name="qwen3-asr-0.6b",
        note="speech.cpp 上の多言語代替",
    ),
    ModelPreset(
        "whisper-large-v3-turbo",
        "asr",
        "ggml-large-v3-turbo.bin",
        "ggerganov/whisper.cpp",
        1.6,
        "GGML",
        "mit",
        "whisper-large-v3-turbo",
        "whisper.cpp",
        note="多言語 ASR の採用候補",
    ),
    ModelPreset(
        "whisper-base",
        "asr",
        "ggml-base.bin",
        "ggerganov/whisper.cpp",
        0.15,
        "GGML",
        "mit",
        "whisper-base",
        "whisper.cpp",
        note="CPU 機で多言語経路を疎通するための小さいモデル",
    ),
)

PRESET_BY_ID = {preset.model_id: preset for preset in PRESETS}


def get_preset(model_id: str) -> ModelPreset | None:
    return PRESET_BY_ID.get(model_id)
