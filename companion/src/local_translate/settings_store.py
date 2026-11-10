"""Global settings, including glossary, GPU overrides, subtitle style, and site rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_translate.constants import DEFAULT_FORM, DEFAULT_IDLE_TIMEOUT_SEC, DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG
from local_translate.errors import CompanionError
from local_translate.json_store import read_text_allowing_replace, replace_file_despite_open_readers
from local_translate.languages import LANGUAGE_CODES
from local_translate.presets import ModelPreset, get_preset

SETTING_KEYS = {
    "target_lang",
    "source_lang",
    "form",
    "glossary",
    "gpu_backend",
    "n_gpu_layers",
    "context_size",
    "parallel",
    "idle_timeout_sec",
    "active_translate_model",
    "active_asr_model",
    "asr_engine",
    "subtitle_backend",
    "translate_interim",
    "subtitle_style",
    "site_rules",
    "experimental_igpu",
    "whisper_model",
}


# ASR の選択は active_asr_model 1 つ。asr_engine と whisper_model は互換のため残し、
# 保存済み値と旧拡張からの POST は実際に使われていたモデルへ読み替える。
SPEECH_CPP_ASR_FALLBACK = "Haas-ja-v1.0"
QWEN3_ASR_MODEL = "qwen3-asr-0.6b"
WHISPER_ASR_FALLBACK = "whisper-base"


def is_speech_cpp_asr_preset(model_id: str) -> bool:
    preset = get_preset(model_id)
    return bool(preset and preset.kind == "asr" and preset.runtime == "speech.cpp")


def is_whisper_asr_preset(model_id: str) -> bool:
    preset = get_preset(model_id)
    return bool(preset and preset.kind == "asr" and preset.runtime == "whisper.cpp")


def derive_asr_engine(model_id: str) -> str:
    """選択中の ASR モデルから旧設定 asr_engine の値を導出する。"""
    preset = get_preset(model_id)
    if preset and preset.kind == "asr" and preset.runtime == "whisper.cpp":
        return "whisper"
    if preset and preset.model_id == QWEN3_ASR_MODEL:
        return "qwen3-asr"
    return "haas"


def resolve_legacy_asr_model(engine: str, active_asr_model: str, whisper_model: str) -> str:
    """移行前の 3 つの設定が実際に使っていたモデルを 1 つに決める。

    - engine whisper → whisper_model（whisper のプリセットでなければ whisper-base）
    - engine qwen3-asr → qwen3-asr-0.6b
    - engine haas → active_asr_model が speech.cpp の音声認識プリセットならそのまま、
      それ以外（silero-vad、whisper-*、不明な ID）→ Haas-ja-v1.0
    """
    if engine == "whisper":
        if is_whisper_asr_preset(whisper_model):
            return whisper_model
        return WHISPER_ASR_FALLBACK
    if engine == "qwen3-asr":
        return QWEN3_ASR_MODEL
    if is_speech_cpp_asr_preset(active_asr_model):
        return active_asr_model
    return SPEECH_CPP_ASR_FALLBACK


def default_settings() -> dict[str, Any]:
    return {
        "target_lang": DEFAULT_TARGET_LANG,
        "source_lang": DEFAULT_SOURCE_LANG,
        "form": DEFAULT_FORM,
        "glossary": [],
        "gpu_backend": "auto",
        "n_gpu_layers": None,
        "context_size": None,
        "parallel": None,
        "idle_timeout_sec": DEFAULT_IDLE_TIMEOUT_SEC,
        "active_translate_model": "plamo-3-translate-0.8b-GGUF",
        "active_asr_model": "Haas-ja-v1.0",
        "asr_engine": "haas",
        "subtitle_backend": "speech-serve",
        "translate_interim": False,
        "subtitle_style": {
            "font_size_px": 18,
            "text_color": "#f4f7f6",
            "background": "rgba(20, 32, 43, 0.78)",
            "position": "bottom",
            "show_original": True,
        },
        "site_rules": [],
        "experimental_igpu": True,
        "whisper_model": "whisper-base",
    }


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = default_settings()
        # 設定ファイルに保存されていたキー。新規導入時の既定モデル判定に使う。
        self.stored_keys: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._normalize_asr_selection()
            return
        stored = json.loads(read_text_allowing_replace(self.path))
        if isinstance(stored, dict):
            filtered = {key: value for key, value in stored.items() if key in SETTING_KEYS}
            self.stored_keys = set(filtered)
            self.data.update(filtered)
        self._normalize_asr_selection()

    def _normalize_asr_selection(self) -> None:
        """保存済み設定を active_asr_model 中心の形へ移行し、揃っていれば保存しなおす。"""
        data = self.data
        resolved = resolve_legacy_asr_model(str(data["asr_engine"]), str(data["active_asr_model"]), str(data["whisper_model"]))
        changed = resolved != data["active_asr_model"]
        data["active_asr_model"] = resolved
        engine = derive_asr_engine(resolved)
        changed = changed or engine != data["asr_engine"]
        data["asr_engine"] = engine
        if is_whisper_asr_preset(resolved):
            changed = changed or data["whisper_model"] != resolved
            data["whisper_model"] = resolved
        if changed:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file_despite_open_readers(temporary, self.path)

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.data))

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(patch) - SETTING_KEYS)
        if unknown:
            raise CompanionError("unknown_setting", f"未知の設定です: {', '.join(unknown)}")
        patch = _translate_legacy_asr_patch(dict(patch), self.data)
        merged = self.snapshot()
        merged.update(patch)
        _sync_derived_asr_fields(merged)
        validate_settings(merged)
        self.data = merged
        self.save()
        return self.snapshot()


def _translate_legacy_asr_patch(patch: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """旧拡張が POST する asr_engine / whisper_model を active_asr_model へ読み替える。"""
    if "active_asr_model" in patch or not ({"asr_engine", "whisper_model"} & set(patch)):
        return patch
    engine = str(patch.pop("asr_engine", current["asr_engine"]))
    if engine not in {"haas", "whisper", "qwen3-asr"}:
        raise CompanionError("invalid_asr_engine", "音声認識エンジンは haas / whisper / qwen3-asr です")
    whisper_model = str(patch.pop("whisper_model", current["whisper_model"]))
    patch["active_asr_model"] = resolve_legacy_asr_model(engine, str(current["active_asr_model"]), whisper_model)
    return patch


def _sync_derived_asr_fields(merged: dict[str, Any]) -> None:
    """active_asr_model から asr_engine（と whisper 同期の whisper_model）を導出する。"""
    resolved = str(merged["active_asr_model"])
    merged["asr_engine"] = derive_asr_engine(resolved)
    if is_whisper_asr_preset(resolved):
        merged["whisper_model"] = resolved


def validate_settings(data: dict[str, Any]) -> None:
    target = data["target_lang"]
    source = data["source_lang"]
    if target not in LANGUAGE_CODES:
        raise CompanionError("invalid_language", f"翻訳先言語が未対応です: {target}")
    if source != "auto" and source not in LANGUAGE_CODES:
        raise CompanionError("invalid_language", f"翻訳元言語が未対応です: {source}")
    if data["form"] not in {"polite", "plain"}:
        raise CompanionError("invalid_form", "文体は polite か plain です")
    if data["form"] == "plain" and target != "ja" and data["form"] not in {"polite", "plain"}:
        raise CompanionError("invalid_form", "文体の指定が不正です")
    if data["gpu_backend"] not in {"auto", "cuda", "vulkan", "cpu"}:
        raise CompanionError("invalid_backend", "GPU バックエンドは auto / cuda / vulkan / cpu です")
    if data["asr_engine"] not in {"haas", "whisper", "qwen3-asr"}:
        raise CompanionError("invalid_asr_engine", "音声認識エンジンは haas / whisper / qwen3-asr です")
    # 発話区間検出や翻訳モデル、不明な ID は ASR の選択として拒否する。
    asr_preset = get_preset(data["active_asr_model"])
    if not isinstance(asr_preset, ModelPreset) or asr_preset.kind != "asr":
        raise CompanionError("invalid_asr_model", f"音声認識モデルとして選べません: {data['active_asr_model']}")
    if data["subtitle_backend"] not in {"speech-serve", "segment-worker"}:
        raise CompanionError("invalid_subtitle_backend", "字幕バックエンドが不正です")
    for key in ("n_gpu_layers", "context_size", "parallel", "idle_timeout_sec"):
        value = data[key]
        if value is None and key != "idle_timeout_sec":
            continue
        if key == "idle_timeout_sec" and not isinstance(value, int):
            raise CompanionError("invalid_number", "アイドルタイムアウトは秒の整数です")
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise CompanionError("invalid_number", f"{key} は整数です")
    if not isinstance(data["glossary"], list):
        raise CompanionError("invalid_glossary", "用語集は配列です")
    for item in data["glossary"]:
        if not isinstance(item, dict) or "source" not in item or "target" not in item:
            raise CompanionError("invalid_glossary", "用語集の各項は source と target を持ちます")
    if not isinstance(data["site_rules"], list):
        raise CompanionError("invalid_site_rule", "サイト別設定は配列です")
    for rule in data["site_rules"]:
        validate_site_rule(rule)
    style = data["subtitle_style"]
    if not isinstance(style, dict):
        raise CompanionError("invalid_subtitle_style", "字幕スタイルが不正です")
    if style.get("position") not in {"bottom", "top"}:
        raise CompanionError("invalid_subtitle_style", "字幕位置は bottom か top です")


def validate_site_rule(rule: dict[str, Any]) -> None:
    if not isinstance(rule, dict) or not str(rule.get("host", "")).strip():
        raise CompanionError("invalid_site_rule", "サイト別設定には host が必要です")
    target = rule.get("target_lang")
    if target is not None and target not in LANGUAGE_CODES:
        raise CompanionError("invalid_language", f"サイト別の翻訳先が未対応です: {target}")
    form = rule.get("form")
    if form is not None and form not in {"polite", "plain"}:
        raise CompanionError("invalid_form", "サイト別の文体が不正です")


def site_rule_for_host(rules: list[dict[str, Any]], host: str) -> dict[str, Any] | None:
    host = host.lower()
    exact = None
    wildcard = None
    for rule in rules:
        if rule.get("enabled") is False:
            continue
        pattern = str(rule.get("host", "")).lower()
        if pattern == host:
            exact = rule
        elif pattern.startswith("*.") and host.endswith(pattern[1:]):
            wildcard = rule
    return exact or wildcard
