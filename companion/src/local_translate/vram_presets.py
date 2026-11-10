"""VRAM tier suggestions. 16 GB offers both 31B Q3 and 8B Q4; the caller picks."""

from __future__ import annotations

from dataclasses import dataclass

from local_translate.gpu_detect import GpuInfo

# nvidia-smi reports a 16 GB card as 16376 MiB, short of 16 * 1024.
VRAM_TIER_MARGIN_MB = 512


def vram_reaches_tier(vram_mb: int, tier_gib: int) -> bool:
    """True when reported VRAM reaches a marketed tier, allowing a 512 MiB shortfall."""
    return vram_mb >= tier_gib * 1024 - VRAM_TIER_MARGIN_MB


@dataclass(frozen=True)
class RuntimeSuggestion:
    model_id: str
    context_size: int
    n_gpu_layers: int
    parallel: int
    reason: str
    alternatives: tuple[str, ...] = ()


def suggest_runtime(gpu: GpuInfo, idle_timeout_sec: int = 600) -> RuntimeSuggestion:
    del idle_timeout_sec
    vram = gpu.vram_total_mb or 0
    ram = gpu.available_ram_mb or 0
    if gpu.backend == "cuda" and vram_reaches_tier(vram, 24):
        return RuntimeSuggestion(
            "plamo-3-translate-31b-GGUF-Q4_K_M",
            16384,
            99,
            2,
            "VRAM 24GB 以上のため 31B Q4_K_M とコンテキスト 16384 を提案します。",
        )
    if vram_reaches_tier(vram, 16):
        return RuntimeSuggestion(
            "plamo-3-translate-8b-GGUF",
            8192,
            99,
            2,
            "VRAM 16GB では 8B Q4_K_M、またはコンテキストを縮めた 31B Q3_K_M を選べます。",
            ("plamo-3-translate-31b-GGUF-Q3_K_M",),
        )
    if vram_reaches_tier(vram, 12):
        return RuntimeSuggestion(
            "plamo-3-translate-8b-GGUF",
            8192,
            99,
            2,
            "VRAM 12GB 以上のため 8B Q4_K_M を提案します。",
        )
    if gpu.experimental_igpu or gpu.backend in {"cpu", "vulkan"} or vram <= 8 * 1024 + VRAM_TIER_MARGIN_MB:
        context = 2048 if ram and ram < 4096 else 4096
        ngl = 8 if gpu.experimental_igpu and gpu.vulkan_available else 0
        reason = "統合 GPU または CPU のため 0.8B Q4_K_M を提案します。"
        if gpu.experimental_igpu:
            reason = "Intel 内蔵 GPU は実験的対応です。層の一部だけを Vulkan に載せ、足りない分は CPU です。"
        return RuntimeSuggestion(
            "plamo-3-translate-0.8b-GGUF",
            context,
            ngl,
            1,
            reason,
            ("plamo-3-translate-2b-GGUF",),
        )
    return RuntimeSuggestion(
        "plamo-3-translate-0.8b-GGUF",
        4096,
        0,
        1,
        "VRAM を特定できないため 0.8B を提案します。",
    )


def resolve_launch_numbers(
    suggestion: RuntimeSuggestion,
    *,
    context_override: int | None,
    ngl_override: int | None,
    parallel_override: int | None,
) -> tuple[int, int, int]:
    context = context_override if context_override else suggestion.context_size
    ngl = suggestion.n_gpu_layers if ngl_override is None else ngl_override
    parallel = parallel_override if parallel_override else suggestion.parallel
    context = max(512, min(context, 32768))
    ngl = max(0, min(ngl, 999))
    parallel = max(1, min(parallel, 8))
    return context, ngl, parallel
