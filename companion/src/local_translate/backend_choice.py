"""Choose the llama.cpp backend and the speech.cpp device from one setting."""

from __future__ import annotations

from local_translate.errors import CompanionError
from local_translate.gpu_detect import GpuInfo


def resolve_llama_backend(requested: str, gpu: GpuInfo) -> str:
    """Resolve auto, cuda, vulkan, or cpu to the backend llama.cpp should run."""
    if requested == "auto":
        if gpu.cuda_available:
            return "cuda"
        if gpu.vulkan_available and (not gpu.experimental_igpu or gpu.backend == "vulkan"):
            return "vulkan"
        return "cpu"
    if requested == "cuda":
        if not gpu.cuda_available:
            raise CompanionError("cuda_unavailable", "CUDA が検出できません")
        return "cuda"
    if requested == "vulkan":
        if not gpu.vulkan_available:
            raise CompanionError("vulkan_unavailable", "Vulkan が検出できません")
        return "vulkan"
    if requested == "cpu":
        return "cpu"
    raise CompanionError("invalid_backend", "GPU バックエンドは auto / cuda / vulkan / cpu です")


def speech_device_name(requested: str, gpu: GpuInfo, experimental_igpu: bool) -> str:
    """speech.cpp device. cuda means the NVIDIA GPU through Vulkan, which is the only GPU build."""
    if requested == "cpu":
        return "cpu"
    if requested in {"cuda", "vulkan"} and gpu.vulkan_available:
        return "gpu"
    if requested == "auto" and gpu.vulkan_available and not gpu.experimental_igpu:
        return "gpu"
    if gpu.experimental_igpu and experimental_igpu and gpu.vulkan_available:
        return "gpu"
    return "cpu"


def whisper_backend_name(requested: str, gpu: GpuInfo) -> str:
    """whisper.cpp backend. CUDA only when that GPU is present; otherwise stay on CPU."""
    if requested in {"auto", "cuda"} and gpu.cuda_available:
        return "cuda"
    return "cpu"
