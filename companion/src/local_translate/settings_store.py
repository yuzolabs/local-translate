"""Global settings, including glossary, GPU overrides, subtitle style, and site rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_translate.constants import DEFAULT_FORM, DEFAULT_IDLE_TIMEOUT_SEC, DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG
from local_translate.errors import CompanionError
from local_translate.json_store import read_text_allowing_replace, replace_file_despite_open_readers
from local_translate.languages import LANGUAGE_CODES

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
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        stored = json.loads(read_text_allowing_replace(self.path))
        if isinstance(stored, dict):
            self.data.update({key: value for key, value in stored.items() if key in SETTING_KEYS})

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
        merged = self.snapshot()
        merged.update(patch)
        validate_settings(merged)
        self.data = merged
        self.save()
        return self.snapshot()


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
