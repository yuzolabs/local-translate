"""Detect CUDA, Vulkan, Intel iGPU, and free RAM. Detection never raises."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GpuInfo:
    backend: str
    name: str
    vram_total_mb: int | None
    vram_free_mb: int | None
    experimental_igpu: bool
    available_ram_mb: int | None
    vulkan_available: bool
    cuda_available: bool

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "name": self.name,
            "vram_total_mb": self.vram_total_mb,
            "vram_free_mb": self.vram_free_mb,
            "experimental_igpu": self.experimental_igpu,
            "available_ram_mb": self.available_ram_mb,
            "vulkan_available": self.vulkan_available,
            "cuda_available": self.cuda_available,
        }


def available_ram_mb() -> int | None:
    if os.name == "nt":
        from local_translate.windows_memory import windows_available_ram_mb

        return windows_available_ram_mb()
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_AVPHYS_PAGES")
        return int(page * pages / (1024 * 1024))
    except (AttributeError, OSError, ValueError):
        pass
    meminfo = _read_text("/proc/meminfo")
    match = re.search(r"^MemAvailable:\s+(\d+)\s+kB", meminfo, re.M)
    if match:
        return int(match.group(1)) // 1024
    return None


def detect_intel_igpu() -> bool:
    vendor = _read_text("/sys/class/drm/card0/device/vendor").strip().lower()
    if vendor in {"0x8086", "8086"}:
        return True
    cpu = _read_text("/proc/cpuinfo")
    return "GenuineIntel" in cpu and _has_pci_intel_display()


def _has_pci_intel_display() -> bool:
    lspci = shutil.which("lspci")
    if not lspci:
        return False
    try:
        result = subprocess.run([lspci, "-nn"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in result.stdout.splitlines():
        if "VGA" in line or "Display" in line or "3D" in line:
            if "8086:" in line or "Intel" in line:
                return True
    return False


def detect_nvidia() -> tuple[str, int, int] | None:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [
                binary,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    line = result.stdout.splitlines()[0]
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 3:
        return None
    try:
        return parts[0], int(float(parts[1])), int(float(parts[2]))
    except ValueError:
        return None


def detect_vulkan() -> tuple[bool, str | None]:
    binary = shutil.which("vulkaninfo")
    if binary:
        try:
            result = subprocess.run(
                [binary, "--summary"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and result.returncode == 0:
            name = None
            for line in (result.stdout + result.stderr).splitlines():
                if "deviceName" in line or "GPU" in line:
                    name = line.split("=", 1)[-1].strip() or name
            return True, name
    return _vulkan_loader_present(), None


def _vulkan_loader_present() -> bool:
    """True when a Vulkan loader library is installed, even if vulkaninfo is absent."""
    linux_loaders = (
        "/usr/lib/x86_64-linux-gnu/libvulkan.so.1",
        "/usr/lib/libvulkan.so.1",
        "/usr/lib64/libvulkan.so.1",
    )
    if any(os.path.exists(path) for path in linux_loaders):
        return True
    if os.name != "nt":
        return False
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.exists(os.path.join(system_root, "System32", "vulkan-1.dll"))


def detect_gpu() -> GpuInfo:
    ram = available_ram_mb()
    nvidia = detect_nvidia()
    vulkan_ok, vulkan_name = detect_vulkan()
    igpu = detect_intel_igpu()
    if nvidia:
        name, total, free = nvidia
        return GpuInfo(
            backend="cuda",
            name=name,
            vram_total_mb=total,
            vram_free_mb=free,
            experimental_igpu=False,
            available_ram_mb=ram,
            vulkan_available=vulkan_ok,
            cuda_available=True,
        )
    if vulkan_ok and not igpu:
        return GpuInfo(
            backend="vulkan",
            name=vulkan_name or "Vulkan GPU",
            vram_total_mb=None,
            vram_free_mb=None,
            experimental_igpu=False,
            available_ram_mb=ram,
            vulkan_available=True,
            cuda_available=False,
        )
    if igpu:
        return GpuInfo(
            backend="vulkan" if vulkan_ok else "cpu",
            name=vulkan_name or "Intel integrated GPU",
            vram_total_mb=None,
            vram_free_mb=None,
            experimental_igpu=True,
            available_ram_mb=ram,
            vulkan_available=vulkan_ok,
            cuda_available=False,
        )
    return GpuInfo(
        backend="cpu",
        name="CPU",
        vram_total_mb=None,
        vram_free_mb=None,
        experimental_igpu=False,
        available_ram_mb=ram,
        vulkan_available=vulkan_ok,
        cuda_available=False,
    )


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""
