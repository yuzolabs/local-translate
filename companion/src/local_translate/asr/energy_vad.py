"""Streaming energy gate used when a test injects audio time without speech.cpp."""

from __future__ import annotations

import array
import sys


class EnergyVad:
    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 450.0,
        min_speech_ms: int = 250,
        min_silence_ms: int = 600,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.min_silence_ms = min_silence_ms
        self._speech_ms = 0
        self._silence_ms = 0
        self.speaking = False
        self.speech_start_ms: int | None = None

    def push(self, pcm: bytes, at_ms: int) -> str | None:
        """Return 'start', 'end', or None. Times are the caller's audio clock."""
        rms = pcm16_rms(pcm)
        duration_ms = int(len(pcm) / 2 * 1000 / self.sample_rate) if pcm else 0
        if rms >= self.threshold:
            self._silence_ms = 0
            self._speech_ms += duration_ms
            if not self.speaking and self._speech_ms >= self.min_speech_ms:
                self.speaking = True
                self.speech_start_ms = max(0, at_ms - self._speech_ms)
                return "start"
            return None
        self._speech_ms = 0
        if not self.speaking:
            return None
        self._silence_ms += duration_ms
        if self._silence_ms >= self.min_silence_ms:
            self.speaking = False
            self.speech_start_ms = None
            return "end"
        return None


def pcm16_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    samples = array.array("h")
    usable = pcm[: len(pcm) - (len(pcm) % 2)]
    samples.frombytes(usable)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0
    total = sum(sample * sample for sample in samples)
    return (total / len(samples)) ** 0.5
