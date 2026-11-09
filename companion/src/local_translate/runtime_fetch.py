"""Download pinned llama.cpp, speech.cpp, and whisper.cpp release archives."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

from local_translate.constants import (
    LLAMA_CPP_REPO,
    LLAMA_CPP_VERSION,
    SPEECH_CPP_REPO,
    SPEECH_CPP_VERSION,
    WHISPER_CPP_REPO,
    WHISPER_CPP_VERSION,
)
from local_translate.errors import CompanionError


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


def verify_sha256_sums(sums_text: str, asset_name: str, file_path: Path) -> None:
    expected = None
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == asset_name:
            expected = parts[0]
            break
    if not expected:
        raise CompanionError("checksum_missing", f"SHA256SUMS に {asset_name} がありません")
    actual = sha256_file(file_path)
    if actual != expected:
        raise CompanionError("checksum_mismatch", f"{asset_name} の SHA-256 が一致しません")


def extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package:
            package.extractall(destination)
        return
    with tarfile.open(archive, "r:gz") as package:
        package.extractall(destination, filter="data")


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
    def __init__(self, runtimes_dir: Path, opener=download_file) -> None:
        self.runtimes_dir = runtimes_dir
        self.opener = opener

    def llama_server_path(self, backend: str, platform: str | None = None) -> Path | None:
        platform = platform or os.sys.platform
        root = self.runtimes_dir / "llama.cpp" / LLAMA_CPP_VERSION / backend
        return find_executable(root, "llama-server")

    def speech_path(self, backend: str, platform: str | None = None) -> Path | None:
        platform = platform or os.sys.platform
        root = self.runtimes_dir / "speech.cpp" / SPEECH_CPP_VERSION / backend
        return find_executable(root, "speech")

    def whisper_path(self, platform: str | None = None) -> Path | None:
        root = self.runtimes_dir / "whisper.cpp" / WHISPER_CPP_VERSION
        found = find_executable(root, "whisper-cli")
        return found or find_executable(root, "main")

    def ensure_llama(self, backend: str, platform: str | None = None) -> Path:
        platform = platform or os.sys.platform
        existing = self.llama_server_path(backend, platform)
        if existing:
            return existing
        asset = llama_asset_name(platform, backend)
        url = github_release_url(LLAMA_CPP_REPO, LLAMA_CPP_VERSION, asset)
        return self._install(url, asset, self.runtimes_dir / "llama.cpp" / LLAMA_CPP_VERSION / backend, "llama-server")

    def ensure_speech(self, backend: str, platform: str | None = None) -> Path:
        platform = platform or os.sys.platform
        existing = self.speech_path(backend, platform)
        if existing:
            return existing
        asset = speech_asset_name(platform, backend)
        tag = f"v{SPEECH_CPP_VERSION}"
        url = github_release_url(SPEECH_CPP_REPO, tag, asset)
        destination = self.runtimes_dir / "speech.cpp" / SPEECH_CPP_VERSION / backend
        binary = self._install(url, asset, destination, "speech")
        sums_url = github_release_url(SPEECH_CPP_REPO, tag, "SHA256SUMS")
        sums_path = destination / "SHA256SUMS"
        archive = destination / asset
        try:
            self.opener(sums_url, sums_path)
            verify_sha256_sums(sums_path.read_text(encoding="utf-8"), asset, archive)
        except CompanionError:
            raise
        except Exception as exc:
            raise CompanionError("checksum_unavailable", f"speech.cpp の SHA-256 を確認できません: {exc}") from exc
        return binary

    def ensure_whisper(self, platform: str | None = None) -> Path:
        platform = platform or os.sys.platform
        existing = self.whisper_path(platform)
        if existing:
            return existing
        asset = whisper_asset_name(platform)
        url = github_release_url(WHISPER_CPP_REPO, WHISPER_CPP_VERSION, asset)
        root = self.runtimes_dir / "whisper.cpp" / WHISPER_CPP_VERSION
        binary = self._install(url, asset, root, "whisper-cli")
        if binary.name != "whisper-cli":
            alt = find_executable(root, "main")
            if alt:
                return alt
        return binary

    def _install(self, url: str, asset: str, destination: Path, binary_name: str) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        archive = destination / asset
        try:
            self.opener(url, archive)
            extract_archive(archive, destination)
        except CompanionError:
            raise
        except Exception as exc:
            raise CompanionError("runtime_download_failed", f"ランタイムを取得できません: {url} ({exc})") from exc
        binary = find_executable(destination, binary_name)
        if binary is None and binary_name == "whisper-cli":
            binary = find_executable(destination, "main")
        if binary is None:
            raise CompanionError("runtime_binary_missing", f"{binary_name} がアーカイブにありません")
        mark_executable(binary)
        return binary


def archive_bytes_for_tests(name: str, binary_name: str, payload: bytes = b"#!/bin/sh\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        info = zipfile.ZipInfo(binary_name)
        info.external_attr = 0o755 << 16
        package.writestr(info, payload)
    return buffer.getvalue()
