"""Child `speech serve` with haas and silero-vad. Live captions use its realtime socket."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

from local_translate.asr.pcm import resample_pcm16
from local_translate.errors import CompanionError
from local_translate.event_bus import EventBus

Translator = Callable[[str, list[dict[str, str]]], Awaitable[str]]


class SpeechServeProcess:
    def __init__(self, binary: str, models: list[str], device: str = "cpu", threads: int = 2, log_path: Path | None = None) -> None:
        self.binary = binary
        self.models = models
        self.device = device
        self.threads = threads
        self.log_path = log_path
        self.process: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.last_used = time.monotonic()

    async def start(self, port: int) -> None:
        if self.process and self.process.returncode is None and self.port == port:
            return
        await self.stop()
        argv = [
            self.binary,
            "serve",
            *self.models,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--device",
            self.device,
            "--threads",
            str(self.threads),
        ]
        log_handle = None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(self.log_path, "ab")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=log_handle or asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.STDOUT if log_handle else asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            if log_handle:
                log_handle.close()
        self.port = port
        await self._wait_healthy()

    async def _wait_healthy(self) -> None:
        assert self.port is not None
        deadline = time.monotonic() + 600
        url = f"http://127.0.0.1:{self.port}/health"
        async with aiohttp.ClientSession() as session:
            while time.monotonic() < deadline:
                if self.process and self.process.returncode is not None:
                    err = b""
                    if self.process.stderr:
                        err = await self.process.stderr.read()
                    elif self.log_path and self.log_path.is_file():
                        err = self.log_path.read_bytes()[-2000:]
                    raise CompanionError("speech_serve_exited", err.decode("utf-8", errors="replace")[:400], 500)
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as response:
                        if response.status == 200:
                            self.last_used = time.monotonic()
                            return
                except (aiohttp.ClientError, TimeoutError):
                    pass
                await asyncio.sleep(0.4)
        raise CompanionError("speech_serve_timeout", "speech serve の起動がタイムアウトしました", 504)

    async def stop(self) -> None:
        process = self.process
        self.process = None
        self.port = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=8)
            except TimeoutError:
                process.kill()


class RealtimeSubtitleBridge:
    """Map speech serve's silero turns onto caption JSON. Translation runs only on final text."""

    def __init__(self, port: int, translator: Translator | None, events: EventBus, translate_interim: bool = False) -> None:
        self.port = port
        self.translator = translator
        self.events = events
        self.translate_interim = translate_interim
        self._seq = 0
        self._interim = ""
        self._item_id: str | None = None
        self._start_ms = 0
        self._history: list[dict[str, str]] = []

    async def run(self, pcm_queue: asyncio.Queue[bytes | None], caption_callback) -> None:
        url = f"http://127.0.0.1:{self.port}/v1/realtime"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                await ws.send_json(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "transcription",
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcm", "rate": 24000},
                                    "turn_detection": {
                                        "type": "server_vad",
                                        "threshold": 0.5,
                                        "prefix_padding_ms": 300,
                                        "silence_duration_ms": 500,
                                    },
                                }
                            },
                        },
                    }
                )
                sender = asyncio.create_task(self._send_audio(ws, pcm_queue))
                try:
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                                break
                            continue
                        payload = json.loads(message.data)
                        caption = await self._on_event(payload)
                        if caption:
                            await caption_callback(caption)
                finally:
                    sender.cancel()

    async def _send_audio(self, ws: aiohttp.ClientWebSocketResponse, pcm_queue: asyncio.Queue[bytes | None]) -> None:
        while True:
            pcm = await pcm_queue.get()
            if pcm is None:
                await ws.close()
                return
            resampled = resample_pcm16(pcm, 16000, 24000)
            await ws.send_json(
                {"type": "input_audio_buffer.append", "audio": base64.b64encode(resampled).decode("ascii")}
            )

    async def _on_event(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event_type = payload.get("type")
        if event_type == "input_audio_buffer.speech_started":
            self._interim = ""
            self._item_id = payload.get("item_id")
            self._start_ms = int(payload.get("audio_start_ms") or 0)
            return None
        if event_type == "conversation.item.input_audio_transcription.delta":
            self._interim += str(payload.get("delta") or "")
            if not self._interim.strip():
                return None
            self._seq += 1
            translation = None
            if self.translate_interim and self.translator:
                translation = await self.translator(self._interim, self._history[-2:])
            return {
                "seq": self._seq,
                "start_ms": self._start_ms,
                "end_ms": None,
                "text": self._interim,
                "translation": translation,
                "final": False,
            }
        if event_type == "conversation.item.input_audio_transcription.completed":
            text = str(payload.get("transcript") or self._interim).strip()
            self._seq += 1
            translation = await self.translator(text, self._history[-2:]) if self.translator and text else None
            self._interim = ""
            if text:
                self._history.append({"source": text, "translation": translation or ""})
                del self._history[:-4]
            return {
                "seq": self._seq,
                "start_ms": self._start_ms,
                "end_ms": int(payload.get("audio_end_ms") or 0) or None,
                "text": text,
                "translation": translation,
                "final": True,
            }
        if event_type == "error":
            self.events.publish("subtitle.error", message=str(payload.get("error")))
        return None
