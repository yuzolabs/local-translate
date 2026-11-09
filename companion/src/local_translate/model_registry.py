"""Preset and custom GGUF registry, consent, and download bookkeeping."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from local_translate.errors import CompanionError
from local_translate.presets import PRESETS, ModelPreset, get_preset


class ModelRegistry:
    def __init__(self, path: Path, models_dir: Path) -> None:
        self.path = path
        self.models_dir = models_dir
        self.data: dict[str, Any] = {"consent": {}, "custom": [], "files": {}, "downloads": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            self.data.update(stored)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def has_consent(self, model_id: str) -> bool:
        return model_id in self.data["consent"]

    def record_consent(self, model_id: str, license_id: str) -> dict[str, Any]:
        record = {"license_id": license_id, "accepted_at": time.time()}
        self.data["consent"][model_id] = record
        self.save()
        return record

    def require_consent(self, model_id: str) -> None:
        if not self.has_consent(model_id):
            raise CompanionError("consent_required", "ダウンロードの前にライセンスへ同意してください", 403)

    def file_record(self, model_id: str) -> dict[str, Any] | None:
        record = self.data["files"].get(model_id)
        if not record:
            return None
        path = Path(record["path"])
        if not path.is_file():
            return None
        return record

    def mark_file(self, model_id: str, path: Path, sha256: str | None = None) -> None:
        self.data["files"][model_id] = {
            "path": str(path),
            "sha256": sha256,
            "ready_at": time.time(),
        }
        self.data["downloads"][model_id] = {"status": "ready", "progress": 1.0}
        self.save()

    def set_download_status(self, model_id: str, status: str, progress: float = 0.0, message: str = "") -> None:
        self.data["downloads"][model_id] = {"status": status, "progress": progress, "message": message}
        self.save()

    def download_status(self, model_id: str) -> dict[str, Any]:
        return dict(self.data["downloads"].get(model_id, {"status": "missing", "progress": 0.0}))

    def register_local_gguf(self, model_id: str, path: Path, alias: str | None = None, sha256: str | None = None) -> dict[str, Any]:
        if not model_id or "/" in model_id or model_id.startswith("-"):
            raise CompanionError("invalid_model_id", "モデル ID が不正です")
        if get_preset(model_id):
            raise CompanionError("reserved_model_id", "プリセットと同じ ID は使えません")
        resolved = path.expanduser().resolve()
        if resolved.suffix.lower() != ".gguf":
            raise CompanionError("invalid_model_file", "任意モデルは .gguf ファイルです")
        if not resolved.is_file():
            raise CompanionError("model_file_missing", "指定した GGUF が見つかりません", 404)
        # 非同期呼び出し元は to_thread で計算した digest を渡す。数 GB の同步ハッシュを
        # イベントループ上で行わないための引数。
        digest = sha256 if sha256 is not None else sha256_file(resolved)
        record = {
            "model_id": model_id,
            "path": str(resolved),
            "alias": alias or model_id,
            "sha256": digest,
            "kind": "translate",
            "runtime": "llama.cpp",
            "license_id": "user-supplied",
        }
        self.data["custom"] = [item for item in self.data["custom"] if item["model_id"] != model_id]
        self.data["custom"].append(record)
        self.mark_file(model_id, resolved, digest)
        self.record_consent(model_id, "user-supplied")
        return record

    def remove_custom(self, model_id: str) -> None:
        before = len(self.data["custom"])
        self.data["custom"] = [item for item in self.data["custom"] if item["model_id"] != model_id]
        if len(self.data["custom"]) == before:
            raise CompanionError("not_custom_model", "プリセットは削除できません", 404)
        self.data["files"].pop(model_id, None)
        self.save()

    def custom_by_id(self, model_id: str) -> dict[str, Any] | None:
        for item in self.data["custom"]:
            if item["model_id"] == model_id:
                return item
        return None

    def resolve_model_path(self, model_id: str) -> Path | None:
        record = self.file_record(model_id)
        if record:
            return Path(record["path"])
        preset = get_preset(model_id)
        if preset and preset.runtime == "llama.cpp":
            candidate = self.models_dir / preset.repo.replace("/", "__") / preset.filename
            if candidate.is_file():
                return candidate
        return None

    def list_models(self, active_translate: str | None, active_asr: str | None, running_id: str | None) -> list[dict[str, Any]]:
        rows = []
        for preset in PRESETS:
            rows.append(self._preset_row(preset, active_translate, active_asr, running_id))
        for custom in self.data["custom"]:
            rows.append(self._custom_row(custom, active_translate, running_id))
        return rows

    def _preset_row(
        self,
        preset: ModelPreset,
        active_translate: str | None,
        active_asr: str | None,
        running_id: str | None,
    ) -> dict[str, Any]:
        ready = self._preset_ready(preset)
        status = "ready" if ready else self.download_status(preset.model_id).get("status", "missing")
        if status == "missing" and ready:
            status = "ready"
        if running_id == preset.model_id or (preset.kind == "translate" and active_translate == preset.model_id and running_id == preset.model_id):
            status = "running"
        return {
            "id": preset.model_id,
            "kind": preset.kind,
            "format": "GGUF" if preset.runtime != "whisper.cpp" else "GGML",
            "size_gb": preset.size_gb,
            "quant": preset.quant,
            "status": status,
            "progress": self.download_status(preset.model_id).get("progress", 1.0 if ready else 0.0),
            "consented": self.has_consent(preset.model_id),
            "license_id": preset.license_id,
            "preset": True,
            "runtime": preset.runtime,
            "repo": preset.repo,
            "filename": preset.filename,
            "catalog_name": preset.catalog_name,
            "note": preset.note,
            "active": preset.model_id in {active_translate, active_asr},
        }

    def _custom_row(self, custom: dict[str, Any], active_translate: str | None, running_id: str | None) -> dict[str, Any]:
        model_id = custom["model_id"]
        ready = self.file_record(model_id) is not None
        status = "running" if running_id == model_id else ("ready" if ready else "missing")
        return {
            "id": model_id,
            "kind": "translate",
            "format": "GGUF",
            "size_gb": None,
            "quant": "custom",
            "status": status,
            "progress": 1.0 if ready else 0.0,
            "consented": True,
            "license_id": "user-supplied",
            "preset": False,
            "runtime": "llama.cpp",
            "path": custom["path"],
            "alias": custom.get("alias", model_id),
            "note": "任意 GGUF",
            "active": model_id == active_translate,
        }

    def _preset_ready(self, preset: ModelPreset) -> bool:
        if preset.runtime == "llama.cpp":
            return self.resolve_model_path(preset.model_id) is not None
        if preset.runtime == "whisper.cpp":
            return self.file_record(preset.model_id) is not None or (
                self.models_dir / preset.filename
            ).is_file()
        speech_marker = self.data["files"].get(preset.model_id)
        return bool(speech_marker)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
