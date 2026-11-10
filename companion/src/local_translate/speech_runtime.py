"""Own the speech.cpp and whisper.cpp child processes the daemon starts."""

from __future__ import annotations

import asyncio
import time
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
from local_translate.presets import ModelPreset, get_preset
from local_translate.runtime_fetch import RuntimeInstaller
from local_translate.settings_store import SettingsStore


def effective_subtitle_backend(settings_data: dict) -> str:
    """ASR が whisper.cpp のとき、字幕は subtitle_backend 設定によらず
    segment-worker 経路で動かす（speech serve は whisper を扱えないため）。"""
    preset = get_preset(settings_data["active_asr_model"])
    if preset and preset.kind == "asr" and preset.runtime == "whisper.cpp":
        return "segment-worker"
    return str(settings_data["subtitle_backend"])


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
        # 字幕セッション中に選択が変わったとき、次の字幕開始で載せ替える必要があるか。
        self.restart_required = False
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

    def active_asr_preset(self) -> ModelPreset | None:
        """選択中の ASR モデルのプリセット。設定は一本化済みで ASR プリセットが保証される。"""
        return get_preset(self.settings.data["active_asr_model"])

    def active_catalog_name(self) -> str:
        """speech.cpp のカタログ名。選択が whisper のときはカタログを持たないので例外。"""
        preset = self.active_asr_preset()
        if preset and preset.kind == "asr" and preset.runtime == "speech.cpp" and preset.catalog_name:
            return preset.catalog_name
        raise CompanionError("asr_catalog_unavailable", "選択中の音声認識モデルは speech.cpp のカタログを持ちません", 409)

    def serve_catalog(self) -> str | None:
        """いま稼働中の serve のカタログ名。停止していれば None。"""
        if self.serve and self.serve.process and self.serve.process.returncode is None and self.serve.port:
            return self.serve.models[0] if self.serve.models else None
        return None

    def desired_serve_catalog(self) -> str | None:
        """選択中の ASR が speech.cpp のとき次に上げるべきカタログ。whisper なら不要。"""
        preset = self.active_asr_preset()
        if preset and preset.kind == "asr" and preset.runtime == "speech.cpp":
            return preset.catalog_name
        return None

    async def sync_serve_selection(self, active_subtitle_sessions: int) -> None:
        """ASR の選択変更を serve へ反映する。

        セッションがないときは古い serve を止めて次の開始で新カタログを起動する。
        別の字幕セッションが動いている間は止めず、「次の字幕開始時に切り替え」を示す。
        """
        desired = self.desired_serve_catalog()
        current = self.serve_catalog()
        if current == desired:
            self.restart_required = False
            return
        if active_subtitle_sessions > 0:
            self.restart_required = True
            return
        if self.serve:
            await self.serve.stop()
            self.serve = None
        self.restart_required = False

    async def ensure_speech_binary(self) -> str:
        binary = await asyncio.to_thread(self.installer.ensure_speech, self.runtime_backend())
        return str(binary)

    async def ensure_worker(self) -> SpeechWorkerClient:
        async with self._lock:
            preset = self.active_asr_preset()
            if preset is None or preset.runtime != "speech.cpp":
                raise CompanionError("worker_not_for_whisper", "whisper.cpp は worker では起動しません")
            catalog = preset.catalog_name
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
        preset = self.active_asr_preset()
        if preset is None or preset.runtime == "whisper.cpp":
            return await self._whisper(pcm, sample_rate)
        worker = await self.ensure_worker()
        result = await worker.transcribe(pcm, sample_rate, request_id=uuid.uuid4().hex)
        return str(result.get("text") or "")

    async def ensure_serve(self) -> SpeechServeProcess:
        async with self._lock:
            catalog = self.active_catalog_name()
            if self.serve and self.serve.process and self.serve.process.returncode is None and self.serve.port:
                if self.serve.models[:1] == [catalog]:
                    self.serve.last_used = time.monotonic()
                    self.restart_required = False
                    return self.serve
                # カタログが変わっていたら次の字幕開始時に起動し直す。
                await self.serve.stop()
            binary = await self.ensure_speech_binary()
            port = pick_free_port()
            self.serve = SpeechServeProcess(binary, [catalog, "silero-vad"], device=self.device_name(), log_path=self.log_path)
            self.events.publish("asr.serve.starting", model=catalog, port=port)
            await self.serve.start(port)
            self.events.publish("asr.serve.ready", model=catalog, port=port)
            self.restart_required = False
            return self.serve

    async def _whisper(self, pcm: bytes, sample_rate: int) -> str:
        model_id = self.settings.data["active_asr_model"]
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
