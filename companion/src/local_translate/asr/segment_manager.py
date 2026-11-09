"""Retranscribe the open phrase every 1–2 seconds, then translate only the final segment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from local_translate.asr.energy_vad import EnergyVad

Transcriber = Callable[[bytes], Awaitable[str]]
Translator = Callable[[str, list[dict[str, str]]], Awaitable[str]]


@dataclass
class Caption:
    seq: int
    start_ms: int
    end_ms: int | None
    text: str
    translation: str | None
    final: bool

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "translation": self.translation,
            "final": self.final,
        }


class SegmentManager:
    def __init__(
        self,
        transcriber: Transcriber,
        translator: Translator | None = None,
        *,
        sample_rate: int = 16000,
        retranscribe_ms: int = 1500,
        translate_interim: bool = False,
        vad: EnergyVad | None = None,
    ) -> None:
        self.transcriber = transcriber
        self.translator = translator
        self.sample_rate = sample_rate
        self.retranscribe_ms = retranscribe_ms
        self.translate_interim = translate_interim
        self.vad = vad or EnergyVad(sample_rate=sample_rate)
        self._pcm = bytearray()
        self._samples = 0
        self._open = False
        self._open_start = 0
        self._open_pcm = bytearray()
        self._last_transcribe_ms = -10_000
        self._seq = 0
        self._busy = False
        self._history: list[dict[str, str]] = []

    @property
    def audio_ms(self) -> int:
        return int(self._samples * 1000 / self.sample_rate)

    async def push_pcm(self, pcm: bytes) -> list[Caption]:
        if not pcm:
            return []
        self._pcm.extend(pcm)
        self._samples += len(pcm) // 2
        event = self.vad.push(pcm, self.audio_ms)
        captions: list[Caption] = []
        if event == "start":
            self._open = True
            self._open_start = self.vad.speech_start_ms or self.audio_ms
            self._open_pcm = bytearray(pcm)
            self._last_transcribe_ms = self.audio_ms
            captions.extend(await self._transcribe(final=False))
        elif self._open:
            self._open_pcm.extend(pcm)
            if event == "end":
                captions.extend(await self._transcribe(final=True))
                self._open = False
                self._open_pcm.clear()
            elif self.audio_ms - self._last_transcribe_ms >= self.retranscribe_ms:
                captions.extend(await self._transcribe(final=False))
                self._last_transcribe_ms = self.audio_ms
        return captions

    async def flush(self) -> list[Caption]:
        if not self._open or not self._open_pcm:
            return []
        captions = await self._transcribe(final=True)
        self._open = False
        self._open_pcm.clear()
        return captions

    async def _transcribe(self, final: bool) -> list[Caption]:
        if self._busy or not self._open_pcm:
            return []
        self._busy = True
        try:
            text = (await self.transcriber(bytes(self._open_pcm))).strip()
        finally:
            self._busy = False
        if not text and not final:
            return []
        translation = None
        if self.translator and (final or self.translate_interim) and text:
            translation = await self.translator(text, self._history[-2:])
        self._seq += 1
        if final and text:
            self._history.append({"source": text, "translation": translation or ""})
            del self._history[:-4]
        return [
            Caption(
                seq=self._seq,
                start_ms=self._open_start,
                end_ms=self.audio_ms if final else None,
                text=text,
                translation=translation,
                final=final,
            )
        ]
