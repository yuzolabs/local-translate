"""Loopback HTTP and WebSocket API. Every route requires the install token."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from typing import Any

from aiohttp import WSMsgType, web

from local_translate.asr.segment_manager import SegmentManager
from local_translate.backend_choice import resolve_llama_backend
from local_translate.asr.speech_serve import RealtimeSubtitleBridge
from local_translate.speech_runtime import effective_subtitle_backend
from local_translate.constants import (
    APP_VERSION,
    BUILT_WITH_PLAMO,
    MULTILINGUAL_ASR_DECISION,
    MULTILINGUAL_ASR_DECISION_REASON,
    WEB_STORE_DECISION,
    WEB_STORE_DECISION_REASON,
    WINDOWS_HAAS_DECISION,
    WINDOWS_HAAS_DECISION_REASON,
)
from local_translate.errors import CompanionError
from local_translate.languages import LANGUAGES
from local_translate.license_texts import LICENSES
from local_translate.model_registry import sha256_file
from local_translate.presets import get_preset
from local_translate.translate_service import validate_translate_request
from local_translate.vram_presets import suggest_runtime


def resident_memory_mb() -> int | None:
    """Current daemon RSS in MB. /proc on Linux, the working set on Windows."""
    if os.name == "nt":
        from local_translate.windows_memory import windows_resident_memory_mb

        return windows_resident_memory_mb()
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(int(line.split()[1]) / 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except (ImportError, ValueError):
        return None


def create_app(state) -> web.Application:
    app = web.Application(middlewares=[cors_middleware, error_middleware, auth_middleware])
    app["state"] = state
    app.router.add_get("/v1/status", status)
    app.router.add_get("/v1/about", about)
    app.router.add_get("/v1/languages", languages)
    app.router.add_get("/v1/models", list_models)
    app.router.add_get("/v1/models/{model_id}/license", model_license)
    app.router.add_post("/v1/models/{model_id}/consent", consent)
    app.router.add_post("/v1/models/{model_id}/download", download_model)
    app.router.add_post("/v1/models/{model_id}/activate", activate_model)
    app.router.add_post("/v1/models", register_model)
    app.router.add_delete("/v1/models/{model_id}", delete_model)
    app.router.add_get("/v1/settings", get_settings)
    app.router.add_post("/v1/settings", post_settings)
    app.router.add_post("/v1/runtimes/{runtime_name}/install", install_runtime)
    app.router.add_post("/api/translate", translate)
    app.router.add_post("/api/transcribe", transcribe)
    app.router.add_get("/api/subtitles", subtitles)
    app.router.add_get("/api/events", events)
    app.router.add_post("/v1/control", control)
    app.router.add_get("/v1/metrics", metrics)
    app.on_cleanup.append(on_cleanup)
    return app


def _state(request: web.Request):
    return request.app["state"]


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)
    origin = request.headers.get("Origin", "")
    if _origin_allowed(_state(request), origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except CompanionError as exc:
        return web.json_response({"error": {"code": exc.code, "message": exc.message}}, status=exc.status)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return await handler(request)
    if request.path in {"/api/subtitles", "/api/events"} and not _bearer(request):
        return await handler(request)
    if not _token_ok(_state(request).token, _bearer(request)):
        raise CompanionError("unauthorized", "トークンが違います", 401)
    return await handler(request)


def _bearer(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return ""


def _token_ok(expected: str, presented: str) -> bool:
    if not presented or len(presented) != len(expected):
        return False
    return secrets.compare_digest(presented, expected)


def _origin_allowed(state, origin: str) -> bool:
    prefix = "chrome-extension://"
    if not origin.startswith(prefix):
        return False
    extension_id = origin[len(prefix) :].split("/", 1)[0]
    return extension_id in state.allowed_extension_ids


async def status(request: web.Request) -> web.Response:
    state = _state(request)
    suggestion = suggest_runtime(state.gpu)
    return web.json_response(
        {
            "version": APP_VERSION,
            "gpu": state.gpu.to_dict(),
            "rss_mb": resident_memory_mb(),
            "suggestion": {
                "model_id": suggestion.model_id,
                "context_size": suggestion.context_size,
                "n_gpu_layers": suggestion.n_gpu_layers,
                "parallel": suggestion.parallel,
                "reason": suggestion.reason,
                "alternatives": list(suggestion.alternatives),
            },
            "llama": state.llama.status(),
            "active_sessions": {"subtitles": state.subtitle_sessions},
            "capabilities": [
                "translate",
                "subtitles",
                "custom-gguf",
                "cuda",
                "vulkan",
                "cpu",
                "whisper",
                "site-rules",
            ],
            "uptime_sec": int(time.time() - state.started_at),
        }
    )


async def about(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "name": "local-translate-extension",
            "version": APP_VERSION,
            "built_with": BUILT_WITH_PLAMO,
            "web_store": {"decision": WEB_STORE_DECISION, "reason": WEB_STORE_DECISION_REASON},
            "windows_haas": {"decision": WINDOWS_HAAS_DECISION, "reason": WINDOWS_HAAS_DECISION_REASON},
            "multilingual_asr": {
                "decision": MULTILINGUAL_ASR_DECISION,
                "reason": MULTILINGUAL_ASR_DECISION_REASON,
            },
        }
    )


async def languages(request: web.Request) -> web.Response:
    return web.json_response({"languages": [{"code": code, "name": name} for code, name in LANGUAGES]})


async def list_models(request: web.Request) -> web.Response:
    state = _state(request)
    running = state.llama.model_id if state.llama.state == "ready" else None
    return web.json_response(
        {
            "models": state.models.list_models(
                state.settings.data["active_translate_model"],
                state.settings.data["active_asr_model"],
                running,
            )
        }
    )


async def model_license(request: web.Request) -> web.Response:
    model_id = request.match_info["model_id"]
    preset = get_preset(model_id)
    license_id = preset.license_id if preset else "user-supplied"
    payload = dict(LICENSES[license_id])
    payload["model_id"] = model_id
    payload["fetched_text"] = await _fetch_license_text(payload.get("url") or "")
    return web.json_response(payload)


async def _fetch_license_text(url: str) -> str | None:
    if not url:
        return None
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                text = await response.text()
                return text[:8000]
    except Exception:
        return None


async def consent(request: web.Request) -> web.Response:
    state = _state(request)
    model_id = request.match_info["model_id"]
    body = await _json(request)
    if body.get("accepted") is not True:
        raise CompanionError("consent_required", "同意するには accepted: true が必要です")
    preset = get_preset(model_id)
    license_id = preset.license_id if preset else "user-supplied"
    record = state.models.record_consent(model_id, license_id)
    return web.json_response({"model_id": model_id, "consent": record})


async def download_model(request: web.Request) -> web.Response:
    state = _state(request)
    model_id = request.match_info["model_id"]
    task = state.download_tasks.get(model_id)
    if task and not task.done():
        return web.json_response({"status": "downloading", "model_id": model_id}, status=202)
    state.download_tasks[model_id] = asyncio.create_task(state.downloader.download(model_id))
    return web.json_response({"status": "started", "model_id": model_id}, status=202)


async def activate_model(request: web.Request) -> web.Response:
    state = _state(request)
    model_id = request.match_info["model_id"]
    preset = get_preset(model_id)
    if preset and preset.kind != "translate":
        # 種類が音声認識でないモデル（発話区間検出）は ASR として選べない。
        if preset.kind != "asr":
            raise CompanionError("invalid_asr_model", "発話区間検出は音声認識モデルとして選べません")
        # 未ダウンロードなら翻訳と同じく 409 で拒否する。
        if state.models.resolve_model_path(model_id) is None:
            raise CompanionError("model_not_downloaded", "モデルがダウンロードされていません", 409)
        state.settings.update({"active_asr_model": model_id})
        await state.speech.sync_serve_selection(state.subtitle_sessions)
        return web.json_response({"status": "selected", "model_id": model_id})
    await _ensure_translate_model(state, model_id)
    state.settings.update({"active_translate_model": model_id})
    return web.json_response({"status": "ready", "model_id": model_id, "llama": state.llama.status()})


async def register_model(request: web.Request) -> web.Response:
    state = _state(request)
    body = await _json(request)
    model_id = str(body.get("id") or "")
    if body.get("path"):
        from pathlib import Path

        resolved = Path(str(body["path"])).expanduser().resolve()
        digest = None
        if resolved.is_file() and resolved.suffix.lower() == ".gguf":
            digest = await asyncio.to_thread(sha256_file, resolved)
        record = state.models.register_local_gguf(model_id, resolved, body.get("alias"), sha256=digest)
        return web.json_response(record, status=201)
    if body.get("repo") and body.get("filename"):
        if body.get("consent") is not True:
            raise CompanionError("consent_required", "HF から取得する前に consent: true が必要です", 403)
        state.models.record_consent(model_id, "user-supplied")
        await state.downloader.download_custom(model_id, str(body["repo"]), str(body["filename"]))
        return web.json_response({"status": "ready", "model_id": model_id}, status=201)
    raise CompanionError("invalid_registration", "path か repo と filename を指定してください")


async def delete_model(request: web.Request) -> web.Response:
    _state(request).models.remove_custom(request.match_info["model_id"])
    return web.json_response({"status": "deleted"})


async def get_settings(request: web.Request) -> web.Response:
    return web.json_response(_state(request).settings.snapshot())


async def post_settings(request: web.Request) -> web.Response:
    state = _state(request)
    body = await _json(request)
    asr_touched = bool({"active_asr_model", "asr_engine", "whisper_model"} & set(body))
    updated = state.settings.update(body)
    state.llama.idle_timeout_sec = int(updated["idle_timeout_sec"])
    parallel = updated.get("parallel") or suggest_runtime(state.gpu).parallel
    state.translator.set_parallel(int(parallel))
    if asr_touched:
        await state.speech.sync_serve_selection(state.subtitle_sessions)
    return web.json_response(updated)


async def install_runtime(request: web.Request) -> web.Response:
    state = _state(request)
    name = request.match_info["runtime_name"]
    if name == "llama":
        backend = resolve_llama_backend(state.settings.data.get("gpu_backend") or "auto", state.gpu)
        path = await asyncio.to_thread(state.installer.ensure_llama, backend)
    elif name == "speech":
        path = await asyncio.to_thread(state.installer.ensure_speech, state.speech.runtime_backend())
    elif name == "whisper":
        path = await asyncio.to_thread(state.installer.ensure_whisper, backend=state.speech.whisper_runtime_backend())
    else:
        raise CompanionError("unknown_runtime", "runtime は llama / speech / whisper です", 404)
    return web.json_response({"status": "installed", "path": str(path)})


async def translate(request: web.Request) -> web.Response:
    state = _state(request)
    translate_request = validate_translate_request(await _json(request))
    model_id = translate_request.model_id or state.settings.data["active_translate_model"]
    await _ensure_translate_model(state, model_id)
    if not translate_request.stream:
        parts = []
        async for delta in state.translator.stream(translate_request):
            parts.append(delta)
        return web.json_response({"text": "".join(parts), "model_id": model_id})
    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
    )
    await response.prepare(request)
    try:
        async for delta in state.translator.stream(translate_request):
            await _sse(response, "delta", {"text": delta})
        await _sse(response, "done", {"model_id": model_id})
    except CompanionError as exc:
        await _sse(response, "error", {"code": exc.code, "message": exc.message})
    await response.write_eof()
    return response


async def transcribe(request: web.Request) -> web.Response:
    import base64

    state = _state(request)
    body = await _json(request)
    if "pcm_base64" not in body:
        raise CompanionError("missing_audio", "pcm_base64 が必要です")
    pcm = base64.b64decode(body["pcm_base64"])
    started = time.perf_counter()
    text = await state.speech.transcribe_pcm(pcm, int(body.get("sample_rate") or 16000))
    return web.json_response({"text": text, "latency_ms": int((time.perf_counter() - started) * 1000)})


async def subtitles(request: web.Request) -> web.WebSocketResponse:
    state = _state(request)
    socket = web.WebSocketResponse()
    await socket.prepare(request)
    if not await _ws_auth(state, request, socket):
        return socket
    state.subtitle_sessions += 1
    pcm_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    backend = effective_subtitle_backend(state.settings.data)
    runner: asyncio.Task | None = None
    try:
        if backend == "speech-serve":
            serve = await state.speech.ensure_serve()
            bridge = RealtimeSubtitleBridge(
                serve.port or 0,
                lambda text, history: _translate_caption(state, text, history),
                state.events,
                translate_interim=bool(state.settings.data["translate_interim"]),
            )
            runner = asyncio.create_task(bridge.run(pcm_queue, socket.send_json))
        else:
            runner = asyncio.create_task(_segment_loop(state, pcm_queue, socket))
        async for message in socket:
            if message.type == WSMsgType.BINARY:
                await pcm_queue.put(message.data)
            elif message.type == WSMsgType.TEXT:
                payload = json.loads(message.data)
                if payload.get("type") == "stop":
                    break
            elif message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                break
    finally:
        await pcm_queue.put(None)
        if runner is not None:
            try:
                await runner
            except Exception:
                runner.cancel()
        state.subtitle_sessions = max(0, state.subtitle_sessions - 1)
    return socket


async def _segment_loop(state, pcm_queue: asyncio.Queue, socket: web.WebSocketResponse) -> None:
    async def transcribe(pcm: bytes) -> str:
        return await state.speech.transcribe_pcm(pcm)

    async def translate_text(text: str, history: list[dict[str, str]]) -> str:
        return await _translate_caption(state, text, history)

    manager = SegmentManager(
        transcribe,
        translate_text,
        translate_interim=bool(state.settings.data["translate_interim"]),
    )
    while True:
        pcm = await pcm_queue.get()
        if pcm is None:
            for caption in await manager.flush():
                await socket.send_json(caption.to_dict())
            return
        for caption in await manager.push_pcm(pcm):
            await socket.send_json(caption.to_dict())


async def _translate_caption(state, text: str, history: list[dict[str, str]] | None = None) -> str:
    from local_translate.translate_service import TranslateRequest

    model_id = state.settings.data["active_translate_model"]
    await _ensure_translate_model(state, model_id)
    request = TranslateRequest(
        text=text,
        target_lang=state.settings.data["target_lang"],
        source_lang=state.settings.data["source_lang"],
        form=state.settings.data["form"],
        glossary=state.settings.data["glossary"],
        history=history or [],
    )
    parts = []
    async for delta in state.translator.stream(request):
        parts.append(delta)
    return "".join(parts)


async def events(request: web.Request) -> web.WebSocketResponse:
    state = _state(request)
    socket = web.WebSocketResponse()
    await socket.prepare(request)
    if not await _ws_auth(state, request, socket):
        return socket
    queue = state.events.subscribe()
    try:
        for event in state.events.recent[-20:]:
            await socket.send_json(event)
        while True:
            event = await queue.get()
            await socket.send_json(event)
    finally:
        state.events.unsubscribe(queue)
    return socket


async def control(request: web.Request) -> web.Response:
    state = _state(request)
    body = await _json(request)
    kind = body.get("type")
    if kind == "status":
        return await status(request)
    if kind == "shutdown":
        asyncio.create_task(_shutdown(state))
        return web.json_response({"status": "stopping"})
    raise CompanionError("unknown_control", "制御メッセージが不明です")


async def metrics(request: web.Request) -> web.Response:
    path = _state(request).paths.metrics_path
    if not path.exists():
        return web.json_response({"metrics": []})
    lines = path.read_text(encoding="utf-8").splitlines()[-100:]
    return web.json_response({"metrics": [json.loads(line) for line in lines if line]})


async def on_cleanup(app: web.Application) -> None:
    state = app["state"]
    await state.llama.close()
    await state.speech.stop()


async def _shutdown(state) -> None:
    # sleep で HTTP 応答の送出を先に済ませてから serve ループへ停止を伝える。
    await asyncio.sleep(0.2)
    state.stop_event.set()


async def _ensure_translate_model(state, model_id: str) -> None:
    path = state.models.resolve_model_path(model_id)
    if path is None:
        raise CompanionError("model_not_downloaded", "モデルがダウンロードされていません", 409)
    settings = state.settings.data
    preset = get_preset(model_id)
    await state.llama.ensure_started(
        model_id,
        path,
        context_size=settings.get("context_size"),
        n_gpu_layers=settings.get("n_gpu_layers"),
        parallel=settings.get("parallel"),
        gpu_backend=settings.get("gpu_backend") or "auto",
        use_jinja=preset is not None,
    )


async def _ws_auth(state, request: web.Request, socket: web.WebSocketResponse) -> bool:
    if _token_ok(state.token, _bearer(request)):
        return True
    try:
        message = await asyncio.wait_for(socket.receive(), timeout=5)
    except TimeoutError:
        await socket.close(code=4401, message=b"unauthorized")
        return False
    if message.type != WSMsgType.TEXT:
        await socket.close(code=4401, message=b"unauthorized")
        return False
    payload = json.loads(message.data)
    if payload.get("type") == "auth" and _token_ok(state.token, str(payload.get("token") or "")):
        return True
    await socket.close(code=4401, message=b"unauthorized")
    return False


async def _json(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise CompanionError("invalid_json", "JSON を読めません") from exc
    if not isinstance(body, dict):
        raise CompanionError("invalid_json", "JSON オブジェクトが必要です")
    return body


async def _sse(response: web.StreamResponse, event: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    await response.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
