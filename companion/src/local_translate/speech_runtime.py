"""Own the speech.cpp and whisper.cpp child processes the daemon starts."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from local_translate.asr.speech_serve import SpeechServeProcess
from local_translate.asr.whisper_backend import WhisperCppBackend
from local_translate.asr.worker_client import SpeechWorkerClient
from local_translate.backend_choice import speech_device_name, whisper_backend_name
from local_translate.errors import CompanionError
from local_translate.event_bus import EventBus
from local_translate.gpu_detect import GpuInfo
from local_translate.llama_server import pick_free_port
from local_translate.model_registry import ModelRegistry
from local_translate.presets import get_preset
from local_translate.runtime_fetch import RuntimeInstaller
from local_translate.settings_store import SettingsStore


class SpeechRuntime:
    def __init__(
        self,
        installer: RuntimeInstaller,
        registry: ModelRegistry,
        settings: SettingsStore,
        events: EventBus,
        gpu: GpuInfo,
    ) -> None:
        self.installer = installer
        self.registry = registry
        self.settings = settings
        self.events = events
        self.gpu = gpu
        self.worker: SpeechWorkerClient | None = None
        self.serve: SpeechServeProcess | None = None
        self.log_path: Path | None = None
        self._lock = asyncio.Lock()

    def device_name(self) -> str:
        return speech_device_name(
            self.settings.data.get("gpu_backend", "auto"),
            self.gpu,
            bool(self.settings.data.get("experimental_igpu")),
        )

    def runtime_backend(self) -> str:
        return "vulkan" if self.device_name() == "gpu" else "cpu"

    def whisper_runtime_backend(self) -> str:
        """whisper.cpp backend. CUDA when llama.cpp would also use CUDA."""
        return whisper_backend_name(self.settings.data.get("gpu_backend", "auto"), self.gpu)

    def active_catalog_name(self) -> str:
        engine = self.settings.data["asr_engine"]
        if engine == "qwen3-asr":
            return "qwen3-asr-0.6b"
        if engine == "haas":
            preset = get_preset(self.settings.data["active_asr_model"])
            if preset and preset.catalog_name:
                return preset.catalog_name
            return "haas-ja-v1.0"
        return self.settings.data["asr_engine"]

    async def ensure_speech_binary(self) -> str:
        binary = await asyncio.to_thread(self.installer.ensure_speech, self.runtime_backend())
        return str(binary)

    async def ensure_worker(self) -> SpeechWorkerClient:
        async with self._lock:
            catalog = self.active_catalog_name()
            if catalog.startswith("whisper"):
                raise CompanionError("worker_not_for_whisper", "whisper.cpp は worker では起動しません")
            if self.worker and self.worker.model == catalog and self.worker.process and self.worker.process.returncode is None:
                return self.worker
            if self.worker:
                await self.worker.stop()
            binary = await self.ensure_speech_binary()
            self.worker = SpeechWorkerClient(binary, catalog, device=self.device_name(), log_path=self.log_path)
            await self.worker.start()
            self.events.publish("asr.worker.ready", model=catalog)
            return self.worker

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if self.settings.data["asr_engine"] == "whisper":
            return await self._whisper(pcm, sample_rate)
        worker = await self.ensure_worker()
        result = await worker.transcribe(pcm, sample_rate, request_id=uuid.uuid4().hex)
        return str(result.get("text") or "")

    async def ensure_serve(self) -> SpeechServeProcess:
        async with self._lock:
            if self.serve and self.serve.process and self.serve.process.returncode is None and self.serve.port:
                return self.serve
            binary = await self.ensure_speech_binary()
            catalog = self.active_catalog_name()
            if catalog.startswith("whisper"):
                catalog = "haas-ja-v1.0"
            port = pick_free_port()
            self.serve = SpeechServeProcess(binary, [catalog, "silero-vad"], device=self.device_name(), log_path=self.log_path)
            self.events.publish("asr.serve.starting", model=catalog, port=port)
            await self.serve.start(port)
            self.events.publish("asr.serve.ready", model=catalog, port=port)
            return self.serve

    async def _whisper(self, pcm: bytes, sample_rate: int) -> str:
        model_id = self.settings.data.get("whisper_model") or "whisper-base"
        record = self.registry.file_record(model_id)
        if not record:
            raise CompanionError("whisper_model_missing", "whisper モデルを先にダウンロードしてください", 409)
        binary = await asyncio.to_thread(self.installer.ensure_whisper, backend=self.whisper_runtime_backend())
        backend = WhisperCppBackend(str(binary), __import__("pathlib").Path(record["path"]))
        return await backend.transcribe_pcm(pcm, sample_rate)

    async def stop(self) -> None:
        if self.worker:
            await self.worker.stop()
            self.worker = None
        if self.serve:
            await self.serve.stop()
            self.serve = None
