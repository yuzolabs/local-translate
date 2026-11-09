"""Split source text so input plus output stay within the training limit."""

from __future__ import annotations

import re

from local_translate.constants import TRAINING_MAX_TOKENS

_SENTENCE_END = re.compile(r"[。！？!?\.\n]")


def estimate_token_count(text: str) -> int:
    """Conservative estimate: one token per non-ASCII character, one per three ASCII characters."""
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    other = len(text) - ascii_chars
    return max(1, other + (ascii_chars + 2) // 3)


def chunk_char_budget(context_size: int, reserved_output_tokens: int = 512) -> int:
    room = min(context_size, TRAINING_MAX_TOKENS) - reserved_output_tokens - 64
    room = max(128, room)
    return room


def split_source_text(text: str, max_tokens: int) -> list[str]:
    if estimate_token_count(text) <= max_tokens:
        return [text]
    boundaries = [match.end() for match in _SENTENCE_END.finditer(text)]
    boundaries.append(len(text))
    chunks: list[str] = []
    start = 0
    cursor = start
    for boundary in boundaries:
        if boundary <= start:
            continue
        piece = text[start:boundary]
        if estimate_token_count(piece) <= max_tokens:
            cursor = boundary
            continue
        if cursor > start:
            chunks.append(text[start:cursor])
            start = cursor
        if estimate_token_count(text[start:boundary]) <= max_tokens:
            cursor = boundary
            continue
        chunks.extend(_split_by_tokens(text[start:boundary], max_tokens))
        start = boundary
        cursor = boundary
    if start < len(text):
        chunks.append(text[start:])
    return [chunk for chunk in chunks if chunk] or [text]


def _split_by_tokens(text: str, max_tokens: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max(32, max_tokens))
        while end > start + 1 and estimate_token_count(text[start:end]) > max_tokens:
            end -= max(1, (end - start) // 4)
        if end <= start:
            end = start + 1
        chunks.append(text[start:end])
        start = end
    return chunks
