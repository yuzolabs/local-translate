"""Translate through llama-server, splitting long text and streaming deltas."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from local_translate.errors import CompanionError
from local_translate.json_store import append_jsonl
from local_translate.languages import LANGUAGE_CODES
from local_translate.paths import AppPaths
from local_translate.presets import get_preset
from local_translate.prompt_builder import build_generic_translation_messages, build_pfml3_prompt
from local_translate.text_splitter import chunk_char_budget, estimate_token_count, split_source_text


@dataclass
class TranslateRequest:
    text: str
    target_lang: str = "ja"
    source_lang: str = "auto"
    form: str | None = None
    glossary: list[dict[str, str]] = field(default_factory=list)
    context: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    model_id: str | None = None
    stream: bool = True


def validate_translate_request(payload: dict) -> TranslateRequest:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise CompanionError("empty_text", "翻訳するテキストが空です")
    if len(text) > 100_000:
        raise CompanionError("text_too_long", "テキストが長すぎます")
    target = payload.get("target_lang") or "ja"
    source = payload.get("source_lang") or "auto"
    if target not in LANGUAGE_CODES:
        raise CompanionError("invalid_language", f"翻訳先言語が未対応です: {target}")
    if source != "auto" and source not in LANGUAGE_CODES:
        raise CompanionError("invalid_language", f"翻訳元言語が未対応です: {source}")
    form = payload.get("form")
    if form is not None and form not in {"polite", "plain"}:
        raise CompanionError("invalid_form", "文体は polite か plain です")
    if form and target != "ja":
        form = None
    glossary = payload.get("glossary") or []
    if not isinstance(glossary, list):
        raise CompanionError("invalid_glossary", "用語集は配列です")
    history = payload.get("history") or []
    if not isinstance(history, list):
        raise CompanionError("invalid_history", "履歴は配列です")
    return TranslateRequest(
        text=text,
        target_lang=target,
        source_lang=source,
        form=form,
        glossary=glossary,
        context=payload.get("context") or None,
        history=history,
        model_id=payload.get("model_id"),
        stream=bool(payload.get("stream", True)),
    )


class TranslateService:
    def __init__(self, llama, paths: AppPaths, settings) -> None:
        self.llama = llama
        self.paths = paths
        self.settings = settings
        self._slots = asyncio_semaphore(1)

    def set_parallel(self, parallel: int) -> None:
        self._slots = asyncio_semaphore(max(1, parallel))

    async def stream(self, request: TranslateRequest) -> AsyncIterator[str]:
        started = time.perf_counter()
        model_id = request.model_id or self.settings.data["active_translate_model"]
        async with self._slots:
            chunks = self._chunks(request)
            parts: list[str] = []
            for index, chunk in enumerate(chunks):
                if index:
                    yield "\n"
                    parts.append("\n")
                async for delta in self._translate_chunk(request, chunk, model_id):
                    parts.append(delta)
                    yield delta
        latency_ms = int((time.perf_counter() - started) * 1000)
        append_jsonl(
            self.paths.metrics_path,
            {
                "kind": "translate",
                "model_id": model_id,
                "chars": len(request.text),
                "latency_ms": latency_ms,
                "ok": True,
                "at": time.time(),
            },
        )

    def _chunks(self, request: TranslateRequest) -> list[str]:
        context = self.settings.data.get("context_size") or 4096
        budget = chunk_char_budget(int(context))
        history = request.history[-2:]
        return split_source_text(request.text, budget)

    async def _translate_chunk(self, request: TranslateRequest, text: str, model_id: str) -> AsyncIterator[str]:
        preset = get_preset(model_id)
        use_pfml3 = preset is not None and preset.runtime == "llama.cpp"
        if use_pfml3:
            prompt = build_pfml3_prompt(
                text,
                request.target_lang,
                source_lang=None if request.source_lang == "auto" else request.source_lang,
                glossary=request.glossary,
                context=request.context,
                form=request.form,
                history=request.history[-2:],
            )
            max_tokens = min(1024, max(64, estimate_token_count(text) + 32))
            async for delta in self.llama.complete_stream(prompt, max_tokens=max_tokens, use_chat=False):
                yield delta
            return
        messages = build_generic_translation_messages(
            text,
            request.target_lang,
            source_lang=request.source_lang,
            glossary=request.glossary,
            context=request.context,
            form=request.form,
            history=request.history[-2:],
        )
        async for delta in self.llama.complete_stream("", max_tokens=1024, use_chat=True, messages=messages):
            yield delta


def asyncio_semaphore(size: int):
    import asyncio

    return asyncio.Semaphore(size)
