"""Download preset weights after consent. Hugging Face for GGUF, `speech pull` for the catalog."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from local_translate.errors import CompanionError
from local_translate.event_bus import EventBus
from local_translate.model_registry import ModelRegistry, sha256_file
from local_translate.presets import get_preset

# hub 2.x の model_info は timeout=None を素通しして無期限に待つ。hf_hub_download 側の
# ETag 取得(10 秒)とそろえ、429/5xx・切断・タイムアウトには 1 回だけ再試行する。
_HF_METADATA_TIMEOUT_SEC = 10.0
_HF_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


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
                path, digest = await self._huggingface_verified(preset.repo, preset.filename, preset.size_gb, model_id)
                self.registry.mark_file(model_id, path, digest)
            self.events.publish("model.download.done", model_id=model_id)
        except Exception as exc:
            self.registry.set_download_status(model_id, "error", 0.0, str(exc))
            self.events.publish("model.download.error", model_id=model_id, message=str(exc))
            if isinstance(exc, CompanionError):
                raise
            raise CompanionError("download_failed", str(exc), 500) from exc

    async def _huggingface_verified(self, repo: str, filename: str, size_gb: float, model_id: str) -> tuple[Path, str]:
        """Download one HF file at the revision the metadata came from, then check it against the published LFS hash."""
        expected_hash, revision = await asyncio.to_thread(self._fetch_metadata, repo, filename)
        local_dir = self.models_dir / repo.replace("/", "__")
        final_path = local_dir / filename
        expected_bytes = max(int(size_gb * 1024 * 1024 * 1024), 1)
        watcher = asyncio.create_task(self._watch_progress(model_id, local_dir, final_path, expected_bytes))
        try:
            path = await asyncio.to_thread(self._huggingface, repo, filename, revision)
            digest = await asyncio.to_thread(sha256_file, path)
        finally:
            watcher.cancel()
        if expected_hash and expected_hash != digest:
            # 落としたファイルを残すとレジストリ解決が無検証の実体を掴み、llama-server を起動できてしまう。
            await asyncio.to_thread(_remove_download_artifacts, final_path, local_dir)
            raise CompanionError("checksum_mismatch", f"{filename} の SHA-256 が Hugging Face の公開値と一致しません")
        return path, digest

    def _fetch_metadata(self, repo: str, filename: str) -> tuple[str | None, str | None]:
        """The sha256 in the LFS pointer (None for non-LFS files) and the commit SHA the download must pin to."""
        from huggingface_hub import HfApi

        info = _model_info_with_retry(HfApi(), repo)
        for sibling in info.siblings or []:
            if sibling.rfilename == filename:
                lfs = getattr(sibling, "lfs", None) or {}
                return lfs.get("sha256") or None, info.sha
        return None, info.sha

    async def _watch_progress(self, model_id: str, local_dir: Path, final_path: Path, expected_bytes: int) -> None:
        """Publish incremental progress while the blocking download runs in a thread."""
        while True:
            await asyncio.sleep(0.5)
            current = _largest_size(local_dir, final_path)
            if expected_bytes <= 0 or current <= 0:
                continue
            progress = min(0.99, current / expected_bytes)
            self.registry.set_download_status(model_id, "downloading", progress)
            self.events.publish("model.download.progress", model_id=model_id, progress=round(progress, 3))

    def _huggingface(self, repo: str, filename: str, revision: str | None) -> Path:
        # to_thread のワーカー上では registry に触らない。書き込みがループ側の save と
        # 重なると models.json が破損するため、進捗更新は復帰後のループ側で行う。
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from huggingface_hub import hf_hub_download

        local_dir = self.models_dir / repo.replace("/", "__")
        local_dir.mkdir(parents=True, exist_ok=True)
        # メタ取得と同じ revision(コミット SHA)に固定する。途中でブランチが進んでも
        # メタのハッシュと本体の一致が崩れないようにするため。None は既定の main 扱い。
        return Path(hf_hub_download(repo_id=repo, filename=filename, local_dir=str(local_dir), revision=revision))

    async def _speech_pull(self, catalog_name: str, model_id: str) -> None:
        binary = await self.speech_binary_getter()
        from local_translate.process_spawn import create_hidden_subprocess

        process = await create_hidden_subprocess(
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
        path, digest = await self._huggingface_verified(repo, filename, 0.1, model_id)
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


def _model_info_with_retry(api, repo: str):
    """model_info with a timeout, retried once on 429/5xx and transport errors."""
    from huggingface_hub.utils import HfHubHTTPError

    try:
        return api.model_info(repo_id=repo, files_metadata=True, timeout=_HF_METADATA_TIMEOUT_SEC)
    except _hf_transient_exceptions() as exc:
        if isinstance(exc, HfHubHTTPError) and exc.response.status_code not in _HF_TRANSIENT_STATUS_CODES:
            raise
    return api.model_info(repo_id=repo, files_metadata=True, timeout=_HF_METADATA_TIMEOUT_SEC)


def _hf_transient_exceptions() -> tuple[type[BaseException], ...]:
    """Timeouts and broken connections. hub 2.x raises httpx2's, older releases requests' (an IOError)."""
    try:
        from httpx2 import HTTPError
    except ImportError:
        return (TimeoutError, ConnectionError, OSError)
    return (TimeoutError, ConnectionError, OSError, HTTPError)


def _remove_download_artifacts(final_path: Path, local_dir: Path) -> None:
    """Delete the rejected download and leftover *.incomplete so no unverified bytes stay on disk."""
    final_path.unlink(missing_ok=True)
    for incomplete in local_dir.rglob("*.incomplete"):
        incomplete.unlink(missing_ok=True)


def _largest_size(local_dir: Path, final_path: Path) -> int:
    """The biggest local artifact, so partial .incomplete files count toward progress."""
    sizes = [0]
    try:
        if final_path.is_file():
            sizes.append(final_path.stat().st_size)
        for incomplete in local_dir.rglob("*.incomplete"):
            sizes.append(incomplete.stat().st_size)
    except OSError:
        pass
    return max(sizes)
