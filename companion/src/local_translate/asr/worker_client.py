"""JSON Lines client for `speech worker`. One resident process, one request at a time."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

from local_translate.errors import CompanionError


class SpeechWorkerClient:
    def __init__(self, binary: str, model: str, device: str = "cpu", threads: int = 2, log_path: Path | None = None) -> None:
        self.binary = binary
        self.model = model
        self.device = device
        self.threads = threads
        self.log_path = log_path
        self.process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self.last_used = time.monotonic()

    async def start(self) -> dict[str, Any]:
        if self.process and self.process.returncode is None:
            return {"type": "ready", "reused": True}
        # stderr は常時誰かが読む必要がある。PIPE のまま置くと 64KB で子が詰まるため
        # daemon ログファイルへ流す。stdout は JSON Lines プロトコル用に PIPE を維持する。
        log_handle = None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(self.log_path, "ab")
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.binary,
                "worker",
                self.model,
                "--device",
                self.device,
                "--threads",
                str(self.threads),
                "--no-warmup",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log_handle or asyncio.subprocess.DEVNULL,
            )
        finally:
            if log_handle:
                log_handle.close()
        self._reader = asyncio.create_task(self._read_stdout())
        ready = await asyncio.wait_for(self._queue.get(), timeout=600)
        if ready.get("type") == "fatal":
            raise CompanionError("speech_worker_failed", str(ready.get("error")), 500)
        if ready.get("type") != "ready":
            raise CompanionError("speech_worker_failed", f"worker の起動応答が不正です: {ready}", 500)
        self.last_used = time.monotonic()
        return ready

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000, request_id: str = "r") -> dict[str, Any]:
        if not self.process or not self.process.stdin:
            raise CompanionError("speech_worker_stopped", "speech worker が起動していません", 409)
        self.last_used = time.monotonic()
        chunk_bytes = sample_rate * 2
        async with self._write_lock:
            for index, offset in enumerate(range(0, len(pcm), chunk_bytes)):
                piece = pcm[offset : offset + chunk_bytes]
                await self._send(
                    {
                        "type": "chunk",
                        "id": request_id,
                        "seq": index,
                        "pcm": base64.b64encode(piece).decode("ascii"),
                    }
                )
            await self._send({"type": "transcribe", "id": request_id, "sample_rate": sample_rate})
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            message = await asyncio.wait_for(self._queue.get(), timeout=max(1, deadline - time.monotonic()))
            if message.get("id") != request_id:
                continue
            if message.get("type") in {"end", "error", "cancelled"}:
                if message["type"] == "error":
                    raise CompanionError("speech_worker_error", str(message.get("error")), 502)
                return message
        raise CompanionError("speech_worker_timeout", "文字起こしがタイムアウトしました", 504)

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process and process.stdin:
            process.stdin.close()
        if self._reader:
            self._reader.cancel()
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()

    async def _send(self, message: dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        line = json.dumps(message, ensure_ascii=False) + "\n"
        self.process.stdin.write(line.encode("utf-8"))
        await self.process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        while True:
            line = await self.process.stdout.readline()
            if not line:
                return
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                await self._queue.put({"type": "fatal", "error": {"message": f"worker stdout が JSON ではありません: {exc}"}})
                return
            await self._queue.put(message)
