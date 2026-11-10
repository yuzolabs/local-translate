"""/v1/status の current: 使用中（選択中）と稼働中のモデルを daemon が 1 か所で決める。

拡張はこの値を表示するだけで、設定とモデル一覧から組み立て直さない。
"""

from __future__ import annotations

from typing import Any

from local_translate.backend_choice import resolve_llama_backend
from local_translate.errors import CompanionError
from local_translate.model_registry import ModelRegistry
from local_translate.presets import get_preset, preset_for_catalog
from local_translate.speech_runtime import effective_subtitle_backend
from local_translate.vram_presets import resolve_launch_numbers, suggest_runtime


def model_identity(model_id: str, registry: ModelRegistry) -> dict[str, Any]:
    """モデル ID から表示名・短い名前・量子化を返す。拡張は ID を加工しない。"""
    preset = get_preset(model_id)
    if preset:
        return {
            "id": preset.model_id,
            "label": preset.display_name,
            "quant": preset.quant,
            "short_label": preset.short_name,
        }
    custom = registry.custom_by_id(model_id)
    alias = str((custom or {}).get("alias") or model_id)
    return {"id": model_id, "label": alias, "quant": "custom", "short_label": "GGUF"}


def build_translate_current(state) -> dict[str, Any]:
    """選択中の翻訳モデルと、llama-server の稼働状況を返す。

    state は ready / starting / stopped / not_downloaded。Backend の解決に失敗する
    設定（CUDA がないのに cuda など）でも例外にせず backend_error として返す。
    """
    settings = state.settings.data
    selected_id = str(settings["active_translate_model"])
    current: dict[str, Any] = {
        "selected": model_identity(selected_id, state.models),
        "loaded": {"id": state.llama.model_id} if state.llama.model_id else None,
        "state": state.llama.state,
        "backend": None,
        "backend_error": None,
        "context_size": None,
        "n_gpu_layers": None,
        "parallel": None,
    }
    if state.models.resolve_model_path(selected_id) is None:
        current["state"] = "not_downloaded"
    try:
        backend = resolve_llama_backend(str(settings.get("gpu_backend") or "auto"), state.gpu)
        current["backend"] = backend
    except CompanionError as exc:
        current["backend_error"] = exc.message
    suggestion = suggest_runtime(state.gpu)
    context, ngl, parallel = resolve_launch_numbers(
        suggestion,
        context_override=settings.get("context_size"),
        ngl_override=settings.get("n_gpu_layers"),
        parallel_override=settings.get("parallel"),
    )
    if current["backend"] == "cpu":
        ngl = 0
    current["context_size"] = context
    current["n_gpu_layers"] = ngl
    current["parallel"] = parallel
    return current


def _asr_loaded_model_id(speech) -> str | None:
    """稼働中の serve / worker のカタログ名からモデル ID を逆引きする。"""
    serve_catalog = speech.serve_catalog()
    if serve_catalog:
        preset = preset_for_catalog(serve_catalog)
        return preset.model_id if preset else serve_catalog
    worker = getattr(speech, "worker", None)
    if worker and worker.process and worker.process.returncode is None:
        preset = preset_for_catalog(str(worker.model))
        return preset.model_id if preset else str(worker.model)
    return None


def build_asr_current(state) -> dict[str, Any]:
    speech = state.speech
    settings = state.settings.data
    selected_id = str(settings["active_asr_model"])
    preset = get_preset(selected_id)
    runtime = preset.runtime if preset else "speech.cpp"
    downloaded = state.models.resolve_model_path(selected_id) is not None
    loaded_id = _asr_loaded_model_id(speech)
    if not downloaded:
        state_value = "not_downloaded"
    elif loaded_id is not None:
        state_value = "ready"
    else:
        # whisper.cpp は発話ごとに whisper-cli を起動するため常駐プロセスを持たない。
        state_value = "stopped"
    if runtime == "whisper.cpp":
        device: str = speech.whisper_runtime_backend()
    else:
        device = speech.device_name()
    return {
        "selected": model_identity(selected_id, state.models),
        "runtime": runtime,
        "subtitle_pipeline": effective_subtitle_backend(settings),
        "loaded": {"id": loaded_id} if loaded_id else None,
        "state": state_value,
        "device": device,
        "restart_required": bool(speech.restart_required),
    }


def build_current(state) -> dict[str, Any]:
    return {
        "translate": build_translate_current(state),
        "asr": build_asr_current(state),
    }
