"""PFML3 prompts for PLaMo and a generic instruction prompt for other GGUF models."""

from __future__ import annotations

PFML3_STOP = ["<|plamo:tag|>", "<|plamo:bos|>"]


def build_pfml3_prompt(
    text: str,
    target_lang: str,
    source_lang: str | None = None,
    glossary: list[dict[str, str]] | None = None,
    context: str | None = None,
    form: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Build a handwritten PFML3 prompt. The string has no newlines, matching the model card."""
    parts = ["<|plamo:tag|>dataset<|plamo:msg|>translation"]
    if context:
        parts.append("<|plamo:tag|>context<|plamo:msg|>")
        parts.append(context)
    for turn in history or []:
        turn_source = turn.get("source_lang") or source_lang
        turn_target = turn.get("target_lang") or target_lang
        turn_form = turn.get("form") or form
        parts.append("<|plamo:tag|>input")
        if turn_source and turn_source != "auto":
            parts.append(f"<|plamo:key|>lang<|plamo:val|>{turn_source}")
        parts.append("<|plamo:msg|>")
        parts.append(turn["source"])
        parts.append("<|plamo:tag|>output")
        parts.append(f"<|plamo:key|>lang<|plamo:val|>{turn_target}")
        if turn_form and turn_target == "ja":
            parts.append(f"<|plamo:key|>form<|plamo:val|>{turn_form}")
        parts.append("<|plamo:msg|>")
        parts.append(turn["translation"])
    if glossary:
        parts.append("<|plamo:tag|>glossary")
        for item in glossary:
            parts.append("<|plamo:index|>")
            parts.append(item["source"])
            parts.append("<|plamo:item|>")
            parts.append(item["target"])
    parts.append("<|plamo:tag|>input")
    if source_lang and source_lang != "auto":
        parts.append(f"<|plamo:key|>lang<|plamo:val|>{source_lang}")
    parts.append("<|plamo:msg|>")
    parts.append(text)
    parts.append("<|plamo:tag|>output")
    parts.append(f"<|plamo:key|>lang<|plamo:val|>{target_lang}")
    if form and target_lang == "ja":
        parts.append(f"<|plamo:key|>form<|plamo:val|>{form}")
    parts.append("<|plamo:msg|>")
    return "".join(parts)


def build_generic_translation_messages(
    text: str,
    target_lang: str,
    source_lang: str | None = None,
    glossary: list[dict[str, str]] | None = None,
    context: str | None = None,
    form: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Instruction prompt for a custom GGUF chat model that is not PLaMo."""
    lines = [
        "You translate text. Reply with the translation only.",
        f"Target language: {target_lang}.",
    ]
    if source_lang and source_lang != "auto":
        lines.append(f"Source language: {source_lang}.")
    if form and target_lang == "ja":
        lines.append(f"Japanese style: {form}.")
    if context:
        lines.append(f"Context: {context}")
    if glossary:
        lines.append("Glossary:")
        for item in glossary:
            lines.append(f"- {item['source']} => {item['target']}")
    messages = [{"role": "system", "content": "\n".join(lines)}]
    for turn in history or []:
        messages.append({"role": "user", "content": turn["source"]})
        messages.append({"role": "assistant", "content": turn["translation"]})
    messages.append({"role": "user", "content": text})
    return messages
