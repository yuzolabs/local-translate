"""Start and stop a pinned llama-server. Arguments are an allowlisted list, never a shell string."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from pathlib import Path
from typing import AsyncIterator

import aiohttp

from local_translate.constants import LLAMA_SERVER_ALIAS
from local_translate.errors import CompanionError
from local_translate.event_bus import EventBus
from local_translate.gpu_detect import GpuInfo
from local_translate.presets import get_preset
from local_translate.runtime_fetch import RuntimeInstaller
from local_translate.vram_presets import resolve_launch_numbers, suggest_runtime

LLAMA_ARGV_FLAGS = {
    "-m",
    "-c",
    "--parallel",
    "--host",
    "--port",
    "--alias",
    "--jinja",
    "--no-prefill-assistant",
    "-ngl",
    "-t",
}


def build_llama_server_argv(
    binary: Path,
    model_path: Path,
    *,
    context_size: int,
    parallel: int,
    port: int,
    alias: str,
    n_gpu_layers: int,
    threads: int,
    use_jinja: bool,
) -> list[str]:
    model = str(model_path)
    if model.startswith("-") or not model_path.is_file():
        raise CompanionError("invalid_model_file", "モデルパスが不正です")
    argv = [
        str(binary),
        "-m",
        model,
        "-c",
        str(context_size),
        "--parallel",
        str(parallel),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--alias",
        alias,
        "-ngl",
        str(n_gpu_layers),
        "-t",
        str(threads),
    ]
    if use_jinja:
        argv.extend(["--jinja", "--no-prefill-assistant"])
    unknown = [flag for flag in argv if flag.startswith("-") and flag not in LLAMA_ARGV_FLAGS]
    if unknown:
        raise CompanionError("llama_flag_rejected", f"許可していない起動引数です: {unknown}")
    return argv


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LlamaServerManager:
    def __init__(
        self,
        installer: RuntimeInstaller,
        events: EventBus,
        gpu: GpuInfo,
        idle_timeout_sec: int = 600,
    ) -> None:
        self.installer = installer
        self.events = events
        self.gpu = gpu
        self.idle_timeout_sec = idle_timeout_sec
        self.process: asyncio.subprocess.Process | None = None
        self.model_id: str | None = None
        self.model_path: Path | None = None
        self.port: int | None = None
        self.alias = LLAMA_SERVER_ALIAS
        self.use_jinja = True
        self.state = "stopped"
        self.last_used = time.monotonic()
        self._session: aiohttp.ClientSession | None = None
        self._idle_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.log_path: Path | None = None

    def status(self) -> dict:
        return {
            "state": self.state,
            "model_id": self.model_id,
            "port": self.port,
            "alias": self.alias,
        }

    def touch(self) -> None:
        self.last_used = time.monotonic()

    async def stop(self) -> None:
        process = self.process
        self.process = None
        self.state = "stopped"
        self.port = None
        self.model_id = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
        self.events.publish("llama.stopped")

    async def ensure_started(
        self,
        model_id: str,
        model_path: Path,
        *,
        context_size: int | None,
        n_gpu_layers: int | None,
        parallel: int | None,
        gpu_backend: str,
        use_jinja: bool = True,
    ) -> None:
        async with self._lock:
            if (
                self.state == "ready"
                and self.model_id == model_id
                and self.process
                and self.process.returncode is None
            ):
                self.touch()
                return
            await self._stop_unlocked()
            backend = self._resolve_backend(gpu_backend)
            binary = await asyncio.to_thread(self.installer.ensure_llama, backend)
            suggestion = suggest_runtime(self.gpu)
            context, ngl, slots = resolve_launch_numbers(
                suggestion,
                context_override=context_size,
                ngl_override=n_gpu_layers,
                parallel_override=parallel,
            )
            if backend == "cpu":
                ngl = 0
            port = pick_free_port()
            preset = get_preset(model_id)
            alias = preset.alias if preset else model_id
            threads = max(1, (os.cpu_count() or 2) - 1)
            argv = build_llama_server_argv(
                binary,
                model_path,
                context_size=context,
                parallel=slots,
                port=port,
                alias=alias,
                n_gpu_layers=ngl,
                threads=threads,
                use_jinja=use_jinja,
            )
            self.events.publish("llama.starting", model_id=model_id, argv_flags=[item for item in argv if item.startswith("-")])
            log_handle = None
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log_handle = open(self.log_path, "ab")
            self.process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=log_handle or asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.STDOUT if log_handle else asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            if log_handle:
                log_handle.close()
            self.model_id = model_id
            self.model_path = model_path
            self.port = port
            self.alias = alias
            self.use_jinja = use_jinja
            self.state = "starting"
            self.touch()
            await self._wait_until_ready()
            self.state = "ready"
            self.events.publish("llama.ready", model_id=model_id, port=port)
            self._ensure_idle_task()

    def _resolve_backend(self, gpu_backend: str) -> str:
        if gpu_backend == "auto":
            if self.gpu.cuda_available:
                return "cuda"
            if self.gpu.vulkan_available and (not self.gpu.experimental_igpu or self.gpu.backend == "vulkan"):
                return "vulkan" if self.gpu.vulkan_available else "cpu"
            return "cpu"
        if gpu_backend == "cuda" and not self.gpu.cuda_available:
            raise CompanionError("cuda_unavailable", "CUDA が検出できません")
        if gpu_backend == "vulkan" and not self.gpu.vulkan_available:
            raise CompanionError("vulkan_unavailable", "Vulkan が検出できません")
        return gpu_backend

    async def _wait_until_ready(self) -> None:
        assert self.port is not None
        deadline = time.monotonic() + 600
        url = f"http://127.0.0.1:{self.port}/health"
        session = await self._client()
        while time.monotonic() < deadline:
            if self.process and self.process.returncode is not None:
                raise CompanionError("llama_exited", "llama-server が起動に失敗しました", 500)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as response:
                    if response.status == 200:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(0.4)
        raise CompanionError("llama_timeout", "llama-server の起動がタイムアウトしました", 504)

    async def complete_stream(
        self,
        prompt: str,
        *,
        max_tokens: int,
        use_chat: bool,
        messages: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        if self.state != "ready" or self.port is None:
            raise CompanionError("model_not_ready", "翻訳モデルが起動していません", 409)
        self.touch()
        session = await self._client()
        url = f"http://127.0.0.1:{self.port}/v1/" + ("chat/completions" if use_chat else "completions")
        if use_chat:
            body = {
                "model": self.alias,
                "messages": messages or [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": True,
            }
        else:
            body = {
                "model": self.alias,
                "prompt": prompt,
                "temperature": 0,
                "max_tokens": max_tokens,
                "stop": ["<|plamo:tag|>", "<|plamo:bos|>"],
                "stream": True,
            }
        async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=None)) as response:
            if response.status >= 400:
                detail = await response.text()
                raise CompanionError("llama_error", f"llama-server がエラーを返しました: {detail[:300]}", 502)
            async for raw in response.content:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                text = choice.get("text")
                if text is None:
                    text = ((choice.get("delta") or {}).get("content")) or ""
                if text:
                    yield text

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _ensure_idle_task(self) -> None:
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            if self.state != "ready":
                continue
            if time.monotonic() - self.last_used >= self.idle_timeout_sec:
                async with self._lock:
                    if self.state == "ready" and time.monotonic() - self.last_used >= self.idle_timeout_sec:
                        await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        process = self.process
        self.process = None
        self.state = "stopped"
        self.port = None
        self.model_id = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
        if process:
            self.events.publish("llama.stopped")

    async def close(self) -> None:
        if self._idle_task:
            self._idle_task.cancel()
        await self.stop()
        if self._session and not self._session.closed:
            await self._session.close()
