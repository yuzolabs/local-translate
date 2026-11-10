"""Download pinned llama.cpp, speech.cpp, and whisper.cpp release archives."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

from local_translate.constants import (
    LLAMA_CPP_REPO,
    LLAMA_CPP_VERSION,
    RUNTIME_ASSET_SHA256,
    SPEECH_CPP_REPO,
    SPEECH_CPP_VERSION,
    WHISPER_CPP_REPO,
    WHISPER_CPP_VERSION,
)
from local_translate.errors import CompanionError
from local_translate.json_store import read_text_allowing_replace, replace_file_despite_open_readers


def llama_cudart_asset_name(platform: str, backend: str) -> str | None:
    """CUDA runtime DLLs for Windows. The llama CUDA zip does not ship them."""
    if platform == "win32" and backend == "cuda":
        return "cudart-llama-bin-win-cuda-12.4-x64.zip"
    return None


def llama_asset_name(platform: str, backend: str) -> str:
    if platform == "win32":
        if backend == "cuda":
            return f"llama-{LLAMA_CPP_VERSION}-bin-win-cuda-12.4-x64.zip"
        if backend == "vulkan":
            return f"llama-{LLAMA_CPP_VERSION}-bin-win-vulkan-x64.zip"
        return f"llama-{LLAMA_CPP_VERSION}-bin-win-cpu-x64.zip"
    if backend == "cuda":
        return f"llama-{LLAMA_CPP_VERSION}-bin-ubuntu-cuda-12.8-x64.tar.gz"
    if backend == "vulkan":
        return f"llama-{LLAMA_CPP_VERSION}-bin-ubuntu-vulkan-x64.tar.gz"
    return f"llama-{LLAMA_CPP_VERSION}-bin-ubuntu-x64.tar.gz"


def speech_asset_name(platform: str, backend: str) -> str:
    if platform == "win32":
        return f"speech-{SPEECH_CPP_VERSION}-windows-x64-vulkan.zip"
    if backend == "vulkan":
        return f"speech-{SPEECH_CPP_VERSION}-linux-x64-vulkan.zip"
    return f"speech-{SPEECH_CPP_VERSION}-linux-x64-cpu.zip"


def whisper_asset_name(platform: str) -> str:
    if platform == "win32":
        return f"whisper-bin-x64.zip"
    return "whisper-bin-ubuntu-x64.tar.gz"


def github_release_url(repo: str, tag: str, asset: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with urlopen(url, timeout=120) as response, temporary.open("wb") as handle:
        while True:
            block = response.read(1024 * 256)
            if not block:
                break
            handle.write(block)
    temporary.replace(destination)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package:
            package.extractall(destination)
        return
    with tarfile.open(archive, "r:gz") as package:
        package.extractall(destination, filter="data")


def _runtime_digest_key(destination: Path, runtimes_dir: Path) -> str:
    """Digest record key with forward slashes, so Windows and Linux share one spelling."""
    return destination.relative_to(runtimes_dir).as_posix()


def find_executable(root: Path, name: str) -> Path | None:
    candidates = [name, f"{name}.exe"]
    for candidate in candidates:
        direct = root / candidate
        if direct.is_file():
            return direct
    for path in root.rglob("*"):
        if path.name in candidates and path.is_file():
            return path
    return None


def mark_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RuntimeInstaller:
    def __init__(self, runtimes_dir: Path, opener=download_file, digests_path: Path | None = None) -> None:
        self.runtimes_dir = runtimes_dir
        self.opener = opener
        self.digests_path = digests_path or (runtimes_dir / "runtimes.sha256.json")

    def ensure_llama(self, backend: str, platform: str | None = None) -> Path:
        platform = platform or os.sys.platform
        destination = self.runtimes_dir / "llama.cpp" / LLAMA_CPP_VERSION / backend
        key = _runtime_digest_key(destination, self.runtimes_dir)
        binary = self._verified_binary(key, destination, "llama-server")
        cudart = llama_cudart_asset_name(platform, backend)
        if binary and self._cuda_runtime_ready(destination, cudart):
            return binary
        if binary is None:
            asset = llama_asset_name(platform, backend)
            url = github_release_url(LLAMA_CPP_REPO, LLAMA_CPP_VERSION, asset)
            binary = self._install(url, asset, destination, "llama-server", key)
        if cudart and not self._cuda_runtime_ready(destination, cudart):
            url = github_release_url(LLAMA_CPP_REPO, LLAMA_CPP_VERSION, cudart)
            self._extract_verified_archive(url, cudart, destination)
            if not self._cuda_runtime_ready(destination, cudart):
                raise CompanionError("runtime_binary_missing", "cudart64_12.dll が CUDA ランタイムにありません")
        return binary

    def ensure_speech(self, backend: str, platform: str | None = None) -> Path:
        platform = platform or os.sys.platform
        destination = self.runtimes_dir / "speech.cpp" / SPEECH_CPP_VERSION / backend
        key = _runtime_digest_key(destination, self.runtimes_dir)
        binary = self._verified_binary(key, destination, "speech")
        if binary:
            return binary
        asset = speech_asset_name(platform, backend)
        url = github_release_url(SPEECH_CPP_REPO, f"v{SPEECH_CPP_VERSION}", asset)
        return self._install(url, asset, destination, "speech", key)

    def ensure_whisper(self, platform: str | None = None) -> Path:
        platform = platform or os.sys.platform
        root = self.runtimes_dir / "whisper.cpp" / WHISPER_CPP_VERSION
        key = _runtime_digest_key(root, self.runtimes_dir)
        binary = self._verified_binary(key, root, "whisper-cli", alt_name="main")
        if binary:
            return binary
        asset = whisper_asset_name(platform)
        url = github_release_url(WHISPER_CPP_REPO, WHISPER_CPP_VERSION, asset)
        return self._install(url, asset, root, "whisper-cli", key, alt_name="main")

    def _load_records(self) -> dict[str, dict[str, str]]:
        if not self.digests_path.is_file():
            return {}
        try:
            data = json.loads(read_text_allowing_replace(self.digests_path))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_record(self, key: str, destination: Path, binary: Path, asset: str, archive_sha256: str) -> None:
        records = self._load_records()
        records[key] = {
            "archive": asset,
            "archive_sha256": archive_sha256,
            "binary": str(binary.relative_to(destination)),
            "binary_sha256": sha256_file(binary),
        }
        self.digests_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.digests_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        replace_file_despite_open_readers(temporary, self.digests_path)

    def _verified_binary(self, key: str, destination: Path, binary_name: str, alt_name: str | None = None) -> Path | None:
        """Return the installed binary after checking it against the recorded digest, or None to install."""
        binary = find_executable(destination, binary_name)
        if binary is None and alt_name:
            binary = find_executable(destination, alt_name)
        if binary is None:
            return None
        record = self._load_records().get(key)
        if record is None:
            # この検査の導入前に置かれたバイナリも、初回に記録して以後の改変を検査する (TOFU)。
            self._save_record(key, destination, binary, "", "")
            return binary
        if sha256_file(binary) != record.get("binary_sha256"):
            shutil.rmtree(destination, ignore_errors=True)
            raise CompanionError(
                "runtime_binary_changed",
                f"{key} のバイナリが記録した digest と一致しないため {destination} を削除しました",
            )
        return binary

    def _cuda_runtime_ready(self, destination: Path, asset: str | None) -> bool:
        if not asset:
            return True
        return any(path.name == "cudart64_12.dll" for path in destination.rglob("cudart64_12.dll"))

    def _extract_verified_archive(self, url: str, asset: str, destination: Path, key: str | None = None) -> str:
        """Download an archive, check its SHA-256, and extract it. Returns the digest."""
        destination.mkdir(parents=True, exist_ok=True)
        archive = destination / asset
        expected = RUNTIME_ASSET_SHA256.get(asset)
        if expected is None and key:
            expected = self._load_records().get(key, {}).get("archive_sha256") or None
        try:
            self.opener(url, archive)
            actual = sha256_file(archive)
        except CompanionError:
            raise
        except Exception as exc:
            raise CompanionError("runtime_download_failed", f"ランタイムを取得できません: {url} ({exc})") from exc
        # 実行前に必ず展開前のアーカイブを検証する。ピンがない資産は初回の digest を記録して
        # 以後の再インストールで照合する (TOFU)。
        if expected and actual != expected:
            archive.unlink(missing_ok=True)
            raise CompanionError("checksum_mismatch", f"{asset} の SHA-256 が一致しません")
        try:
            extract_archive(archive, destination)
        except CompanionError:
            raise
        except Exception as exc:
            raise CompanionError("runtime_download_failed", f"ランタイムを取得できません: {url} ({exc})") from exc
        return actual

    def _install(self, url: str, asset: str, destination: Path, binary_name: str, key: str, alt_name: str | None = None) -> Path:
        actual = self._extract_verified_archive(url, asset, destination, key)
        binary = find_executable(destination, binary_name)
        if binary is None and alt_name:
            binary = find_executable(destination, alt_name)
        if binary is None:
            raise CompanionError("runtime_binary_missing", f"{binary_name} がアーカイブにありません")
        mark_executable(binary)
        self._save_record(key, destination, binary, asset, actual)
        return binary


def archive_bytes_for_tests(name: str, binary_name: str, payload: bytes = b"#!/bin/sh\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        info = zipfile.ZipInfo(binary_name)
        info.external_attr = 0o755 << 16
        package.writestr(info, payload)
    return buffer.getvalue()
