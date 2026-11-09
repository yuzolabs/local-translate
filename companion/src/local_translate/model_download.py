"""Download preset weights after consent. Hugging Face for GGUF, `speech pull` for the catalog."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from local_translate.errors import CompanionError
from local_translate.event_bus import EventBus
from local_translate.model_registry import ModelRegistry, sha256_file
from local_translate.presets import get_preset


class ModelDownloader:
    def __init__(self, registry: ModelRegistry, events: EventBus, models_dir: Path, speech_binary_getter) -> None:
        self.registry = registry
        self.events = events
        self.models_dir = models_dir
        self.speech_binary_getter = speech_binary_getter

    async def download(self, model_id: str) -> None:
        preset = get_preset(model_id)
        if preset is None:
            raise CompanionError("unknown_model", "そのモデル ID はプリセットにありません", 404)
        self.registry.require_consent(model_id)
        self.registry.set_download_status(model_id, "downloading", 0.0)
        self.events.publish("model.download.started", model_id=model_id)
        try:
            if preset.runtime == "speech.cpp":
                await self._speech_pull(preset.catalog_name, model_id)
            else:
                path = await asyncio.to_thread(self._huggingface, preset.repo, preset.filename)
                self._report_download_progress(model_id, path, preset.size_gb)
                digest = await asyncio.to_thread(sha256_file, path)
                self.registry.mark_file(model_id, path, digest)
            self.events.publish("model.download.done", model_id=model_id)
        except Exception as exc:
            self.registry.set_download_status(model_id, "error", 0.0, str(exc))
            self.events.publish("model.download.error", model_id=model_id, message=str(exc))
            if isinstance(exc, CompanionError):
                raise
            raise CompanionError("download_failed", str(exc), 500) from exc

    def _huggingface(self, repo: str, filename: str) -> Path:
        # to_thread のワーカー上では registry に触らない。書き込みがループ側の save と
        # 重なると models.json が破損するため、進捗更新は復帰後のループ側で行う。
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from huggingface_hub import hf_hub_download

        local_dir = self.models_dir / repo.replace("/", "__")
        local_dir.mkdir(parents=True, exist_ok=True)
        return Path(hf_hub_download(repo_id=repo, filename=filename, local_dir=str(local_dir)))

    def _report_download_progress(self, model_id: str, path: Path, size_gb: float) -> None:
        expected = max(size_gb * 1024 * 1024 * 1024, 1)
        self.registry.set_download_status(model_id, "downloading", min(0.99, path.stat().st_size / expected))

    async def _speech_pull(self, catalog_name: str, model_id: str) -> None:
        binary = await self.speech_binary_getter()
        process = await asyncio.create_subprocess_exec(
            binary,
            "pull",
            catalog_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            self.events.publish("model.download.progress", model_id=model_id, message=line.decode("utf-8", errors="replace").strip())
        code = await process.wait()
        if code != 0:
            raise CompanionError("speech_pull_failed", f"speech pull {catalog_name} が失敗しました", 500)
        marker = self.models_dir / "speech-catalog" / catalog_name.replace(":", "__")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(catalog_name, encoding="utf-8")
        self.registry.mark_file(model_id, marker)

    async def download_custom(self, model_id: str, repo: str, filename: str) -> None:
        if not model_id or "/" in model_id:
            raise CompanionError("invalid_model_id", "モデル ID が不正です")
        path = await asyncio.to_thread(self._huggingface, repo, filename)
        self._report_download_progress(model_id, path, 0.1)
        digest = await asyncio.to_thread(sha256_file, path)
        self.registry.mark_file(model_id, path, digest)
        self.registry.data["custom"] = [item for item in self.registry.data["custom"] if item["model_id"] != model_id]
        self.registry.data["custom"].append(
            {
                "model_id": model_id,
                "path": str(path),
                "alias": model_id,
                "kind": "translate",
                "runtime": "llama.cpp",
                "license_id": "user-supplied",
            }
        )
        self.registry.save()
        self.events.publish("model.download.done", model_id=model_id)
