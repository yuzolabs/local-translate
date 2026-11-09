"""whisper.cpp child process for multilingual ASR. Haas remains the Japanese default."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from local_translate.asr.silero_vad import write_pcm16_wav
from local_translate.errors import CompanionError


class WhisperCppBackend:
    def __init__(self, binary: str, model_path: Path, threads: int = 2) -> None:
        self.binary = binary
        self.model_path = model_path
        self.threads = threads

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000, language: str = "auto") -> str:
        if not self.model_path.is_file():
            raise CompanionError("whisper_model_missing", "whisper のモデルファイルがありません", 409)
        directory = self.model_path.parent / "whisper-work"
        directory.mkdir(parents=True, exist_ok=True)
        wav_path = directory / "utterance.wav"
        write_pcm16_wav(wav_path, pcm, sample_rate)
        argv = [
            self.binary,
            "-m",
            str(self.model_path),
            "-f",
            str(wav_path),
            "-t",
            str(self.threads),
            "--output-json",
            "--output-file",
            str(directory / "utterance"),
        ]
        if language and language != "auto":
            argv.extend(["-l", language])
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise CompanionError("whisper_failed", stderr.decode("utf-8", errors="replace")[:400], 500)
        json_path = directory / "utterance.json"
        if json_path.is_file():
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            text = payload.get("transcription") or payload.get("text")
            if isinstance(text, str):
                return text.strip()
            if isinstance(payload.get("transcription"), list):
                return " ".join(str(item.get("text", "")) for item in payload["transcription"]).strip()
        return stdout.decode("utf-8", errors="replace").strip()
