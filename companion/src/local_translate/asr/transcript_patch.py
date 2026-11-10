"""Apply asr-ja-bench transcript patches. Offsets are Unicode code points."""

from __future__ import annotations


def apply_codepoint_patch(text: str, patch: list[list] | None) -> str:
    """Apply replacements from the right so earlier offsets stay valid."""
    if not patch:
        return text
    characters = list(text)
    ordered = sorted(patch, key=lambda item: int(item[0]), reverse=True)
    for start, end, replacement in ordered:
        characters[int(start) : int(end)] = list(str(replacement))
    return "".join(characters)
