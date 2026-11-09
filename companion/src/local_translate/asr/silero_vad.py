"""Run speech.cpp's silero-vad on a WAVE file. The model stays a child process, not an import."""

from __future__ import annotations

import array
import asyncio
import json
import sys
import wave
from pathlib import Path

from local_translate.errors import CompanionError


def write_pcm16_wav(path: Path, pcm: bytes, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


async def silero_regions(
    speech_bin: str,
    wav_path: Path,
    *,
    min_silence_ms: int = 500,
) -> list[dict[str, float]]:
    process = await asyncio.create_subprocess_exec(
        speech_bin,
        "vad",
        "silero-vad",
        "--format",
        "json",
        "--min-silence-duration-ms",
        str(min_silence_ms),
        str(wav_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise CompanionError("silero_failed", stderr.decode("utf-8", errors="replace")[:400], 500)
    line = stdout.decode("utf-8", errors="replace").strip().splitlines()
    if not line:
        return []
    payload = json.loads(line[-1])
    return list(payload.get("regions") or [])


def trim_pcm_to_regions(pcm: bytes, regions: list[dict[str, float]], sample_rate: int = 16000) -> bytes:
    if not regions:
        return pcm
    samples = array.array("h")
    usable = pcm[: len(pcm) - (len(pcm) % 2)]
    samples.frombytes(usable)
    if sys.byteorder != "little":
        samples.byteswap()
    pieces = array.array("h")
    for region in regions:
        start = max(0, int(float(region["start"]) * sample_rate))
        end = min(len(samples), int(float(region["end"]) * sample_rate))
        if end > start:
            pieces.extend(samples[start:end])
    if sys.byteorder != "little":
        pieces.byteswap()
    return pieces.tobytes() or pcm
