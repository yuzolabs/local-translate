import array
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from local_translate.asr.cer import character_error_rate
from local_translate.asr.energy_vad import EnergyVad, pcm16_rms
from local_translate.asr.segment_manager import SegmentManager
from local_translate.asr.speech_serve import SpeechServeProcess
from local_translate.asr.worker_client import SpeechWorkerClient
from local_translate.constants import TRAINING_MAX_TOKENS
from local_translate.daemon import AppState, serve
from local_translate.gpu_detect import GpuInfo
from local_translate.http_api import create_app
from local_translate.languages import LANGUAGES
from local_translate.llama_server import LLAMA_ARGV_FLAGS, build_llama_server_argv, pick_free_port
from local_translate.nm_framing import read_native_message, write_native_message
from local_translate.paths import AppPaths, default_app_paths, resolve_data_dir, resolve_state_dir
from local_translate.process_lock import ProcessLock
from local_translate.prompt_builder import build_pfml3_prompt
from local_translate.runtime_fetch import RuntimeInstaller, archive_bytes_for_tests, speech_asset_name
from local_translate.settings_store import SettingsStore, site_rule_for_host
from local_translate.text_splitter import estimate_token_count, split_source_text
from local_translate.vram_presets import suggest_runtime


class FakeLlama:
    def __init__(self) -> None:
        self.state = "stopped"
        self.model_id = None
        self.port = None
        self.alias = "plamo-3-translate"
        self.idle_timeout_sec = 600
        self.prompts: list[str] = []

    async def ensure_started(self, model_id, model_path, **kwargs):
        self.model_id = model_id
        self.state = "ready"
        self.port = 9

    async def complete_stream(self, prompt, *, max_tokens, use_chat, messages=None):
        self.prompts.append(prompt)
        yield "訳文"

    def status(self):
        return {"state": self.state, "model_id": self.model_id, "port": self.port, "alias": self.alias}

    async def close(self):
        self.state = "stopped"

    async def stop(self):
        self.state = "stopped"


def pcm_tone(samples: int, amplitude: int) -> bytes:
    data = array.array("h", [amplitude] * samples)
    if sys.byteorder != "little":
        data.byteswap()
    return data.tobytes()


def test_languages_cover_the_model_card():
    assert len(LANGUAGES) == 53
    assert dict(LANGUAGES)["ja"] == "日本語"
    assert dict(LANGUAGES)["zh-hans"] != dict(LANGUAGES)["zh-hant"]


def test_pfml3_matches_the_model_card_examples():
    basic = build_pfml3_prompt("会議は来週の月曜日に変更されました。", "en")
    assert basic == (
        "<|plamo:tag|>dataset<|plamo:msg|>translation"
        "<|plamo:tag|>input<|plamo:msg|>会議は来週の月曜日に変更されました。"
        "<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>en<|plamo:msg|>"
    )
    glossary = build_pfml3_prompt(
        "プロジェクトの管理画面を開いてください。",
        "en",
        glossary=[{"source": "管理画面", "target": "dashboard"}, {"source": "プロジェクト", "target": "project"}],
    )
    assert "<|plamo:index|>管理画面<|plamo:item|>dashboard" in glossary
    assert glossary.endswith("<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>en<|plamo:msg|>")
    polite = build_pfml3_prompt("This system is convenient.", "ja", form="polite")
    assert "<|plamo:key|>form<|plamo:val|>polite" in polite
    with_context = build_pfml3_prompt("The bank was muddy.", "ja", context="川のほとり", form="plain")
    assert with_context.index("context") < with_context.index("input")


def test_history_sits_before_glossary():
    prompt = build_pfml3_prompt(
        "次の文",
        "en",
        history=[{"source": "前の文", "translation": "Previous."}],
        glossary=[{"source": "次", "target": "next"}],
    )
    assert prompt.index("前の文") < prompt.index("glossary") < prompt.index("次の文")


def test_split_keeps_every_character_and_respects_the_training_limit():
    text = "短い文です。次も短いです。"
    assert split_source_text(text, 1000) == [text]
    chunks = split_source_text("あ" * 50, 10)
    assert "".join(chunks) == "あ" * 50
    assert all(estimate_token_count(chunk) <= 10 for chunk in chunks)
    assert TRAINING_MAX_TOKENS == 32768


def test_state_paths_do_not_hardcode_a_user():
    home = Path("/tmp/example-home")
    assert resolve_state_dir("linux", {}, home) == home / ".local" / "state" / "local-translate-extension"
    windows = resolve_data_dir("win32", {"LOCALAPPDATA": "C:/AppData"}, home)
    assert windows == Path("C:/AppData") / "local-translate-extension" / "data"


def test_vram_tiers():
    big = suggest_runtime(GpuInfo("cuda", "RTX", 24576, 20000, False, 32000, True, True))
    assert big.model_id == "plamo-3-translate-31b-GGUF-Q4_K_M"
    assert big.context_size == 16384
    mid = suggest_runtime(GpuInfo("cuda", "RTX", 16384, 14000, False, 32000, True, True))
    assert "plamo-3-translate-31b-GGUF-Q3_K_M" in mid.alternatives
    small = suggest_runtime(GpuInfo("cpu", "CPU", None, None, False, 3000, False, False))
    assert small.model_id == "plamo-3-translate-0.8b-GGUF"
    assert small.n_gpu_layers == 0
    igpu = suggest_runtime(GpuInfo("vulkan", "Intel", None, None, True, 8000, True, False))
    assert igpu.n_gpu_layers > 0
    assert "実験的" in igpu.reason


def test_site_rule_prefers_exact_host(tmp_path: Path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update(
        {
            "site_rules": [
                {"host": "*.example.com", "target_lang": "en", "auto_translate": True},
                {"host": "news.example.com", "target_lang": "ja", "form": "plain"},
            ]
        }
    )
    assert site_rule_for_host(store.data["site_rules"], "news.example.com")["target_lang"] == "ja"
    assert site_rule_for_host(store.data["site_rules"], "blog.example.com")["target_lang"] == "en"


def test_llama_argv_is_allowlisted(tmp_path: Path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"bin")
    argv = build_llama_server_argv(
        binary,
        model,
        context_size=2048,
        parallel=1,
        port=1234,
        alias="plamo-3-translate",
        n_gpu_layers=0,
        threads=2,
        use_jinja=True,
    )
    assert argv[0] == str(binary)
    assert "--jinja" in argv and "--no-prefill-assistant" in argv
    assert all(flag in LLAMA_ARGV_FLAGS for flag in argv if flag.startswith("-"))
    assert "shell" not in " ".join(argv)


def test_native_message_roundtrip():
    import io

    stream = io.BytesIO()
    write_native_message(stream, {"type": "hello", "text": "日本語"})
    stream.seek(0)
    assert read_native_message(stream) == {"type": "hello", "text": "日本語"}


def test_character_error_rate():
    assert character_error_rate("abc", "abc") == 0
    assert character_error_rate("abc", "axc") == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_segment_manager_retranscribes_then_finalizes():
    calls = {"n": 0}

    async def transcribe(pcm: bytes) -> str:
        calls["n"] += 1
        return f"文{calls['n']}"

    async def translate(text: str) -> str:
        return f"訳:{text}"

    vad = EnergyVad(sample_rate=16000, threshold=100, min_speech_ms=200, min_silence_ms=400)
    manager = SegmentManager(transcribe, translate, sample_rate=16000, retranscribe_ms=1000, vad=vad)
    speech = pcm_tone(16000, 2000)
    first = await manager.push_pcm(speech)
    assert first and first[0].final is False and first[0].translation is None
    more = await manager.push_pcm(speech)
    assert any(not caption.final for caption in more)
    silence = pcm_tone(16000, 0)
    final = await manager.push_pcm(silence)
    assert final[-1].final is True
    assert final[-1].translation == "訳:文3"
    assert pcm16_rms(silence) == 0


@pytest.mark.asyncio
async def test_http_translate_requires_token_and_streams(tmp_path: Path):
    paths = AppPaths.create(tmp_path / "state", tmp_path / "data")
    state = AppState(paths)
    state.llama = FakeLlama()
    state.translator.llama = state.llama
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    state.models.mark_file("plamo-3-translate-0.8b-GGUF", model)
    app = create_app(state)
    async with TestClient(TestServer(app)) as client:
        denied = await client.get("/v1/status")
        assert denied.status == 401
        headers = {"Authorization": f"Bearer {state.token}"}
        status = await client.get("/v1/status", headers=headers)
        assert status.status == 200
        body = await status.json()
        assert body["version"]
        origin = f"chrome-extension://{next(iter(state.allowed_extension_ids))}"
        cors = await client.get("/v1/about", headers={**headers, "Origin": origin})
        assert cors.headers["Access-Control-Allow-Origin"] == origin
        foreign = await client.get("/v1/about", headers={**headers, "Origin": "https://example.com"})
        assert "Access-Control-Allow-Origin" not in foreign.headers
        consent = await client.post(
            "/v1/models/plamo-3-translate-0.8b-GGUF/consent",
            headers=headers,
            json={"accepted": True},
        )
        assert consent.status == 200
        response = await client.post(
            "/api/translate",
            headers=headers,
            json={"text": "Hello", "target_lang": "ja", "stream": False},
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["text"] == "訳文"
        assert "<|plamo:tag|>dataset" in state.llama.prompts[0]
        models = await client.get("/v1/models", headers=headers)
        ids = [item["id"] for item in (await models.json())["models"]]
        assert "plamo-3-translate-31b-GGUF-Q4_K_M" in ids
        assert "whisper-large-v3-turbo" in ids
        custom = tmp_path / "custom.gguf"
        custom.write_bytes(b"custom-gguf")
        registered = await client.post(
            "/v1/models",
            headers=headers,
            json={"id": "my-gguf", "path": str(custom)},
        )
        assert registered.status == 201
        streamed = await client.post("/api/translate", headers=headers, json={"text": "Hello", "target_lang": "ja"})
        raw = await streamed.text()
        assert "event: delta" in raw and "訳文" in raw


@pytest.mark.asyncio
async def test_subtitle_socket_emits_final_caption(tmp_path: Path):
    paths = AppPaths.create(tmp_path / "state", tmp_path / "data")
    state = AppState(paths)
    state.llama = FakeLlama()
    state.translator.llama = state.llama
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    state.models.mark_file("plamo-3-translate-0.8b-GGUF", model)
    state.settings.update({"subtitle_backend": "segment-worker", "translate_interim": False})

    async def transcribe_pcm(pcm: bytes, sample_rate: int = 16000) -> str:
        del pcm, sample_rate
        return "会議です"

    state.speech.transcribe_pcm = transcribe_pcm
    app = create_app(state)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/api/subtitles") as socket:
            await socket.send_json({"type": "auth", "token": state.token})
            await socket.send_bytes(pcm_tone(16000, 2000))
            await socket.send_bytes(pcm_tone(16000, 0))
            await socket.send_json({"type": "stop"})
            captions = []
            while True:
                message = await socket.receive(timeout=5)
                if message.type is not aiohttp.WSMsgType.TEXT:
                    break
                captions.append(message.json())
                if captions[-1].get("final"):
                    break
    finals = [caption for caption in captions if caption.get("final")]
    assert finals
    assert finals[-1]["text"] == "会議です"
    assert finals[-1]["translation"] == "訳文"
    assert {"seq", "start_ms", "end_ms", "text", "translation", "final"} <= finals[-1].keys()


def test_registry_rejects_a_missing_custom_file(tmp_path: Path):
    from local_translate.model_registry import ModelRegistry

    registry = ModelRegistry(tmp_path / "models.json", tmp_path / "models")
    with pytest.raises(Exception):
        registry.register_local_gguf("x", tmp_path / "missing.gguf")


def test_register_local_gguf_uses_the_digest_given_from_a_worker_thread(tmp_path: Path, monkeypatch):
    from local_translate.model_registry import ModelRegistry
    from local_translate import model_registry as model_registry_module

    def fail(path):
        raise AssertionError("イベントループ上でハッシュを計算してはいけない")

    monkeypatch.setattr(model_registry_module, "sha256_file", fail)
    registry = ModelRegistry(tmp_path / "models.json", tmp_path / "models")
    gguf = tmp_path / "a.gguf"
    gguf.write_bytes(b"gguf")
    record = registry.register_local_gguf("my-model", gguf, sha256="abc123")
    assert record["sha256"] == "abc123"
    assert registry.file_record("my-model")["sha256"] == "abc123"


@pytest.mark.asyncio
async def test_register_model_hashes_off_the_event_loop(tmp_path, monkeypatch):
    import threading

    import local_translate.http_api as http_api_module

    paths = AppPaths.create(tmp_path / "state", tmp_path / "data")
    state = AppState(paths)
    state.llama = FakeLlama()
    state.translator.llama = state.llama
    loop_thread = threading.get_ident()
    real_sha256_file = http_api_module.sha256_file
    hash_threads = []

    def spy(path):
        hash_threads.append(threading.get_ident())
        return real_sha256_file(path)

    monkeypatch.setattr(http_api_module, "sha256_file", spy)
    app = create_app(state)
    async with TestClient(TestServer(app)) as client:
        headers = {"Authorization": f"Bearer {state.token}"}
        gguf = tmp_path / "custom.gguf"
        gguf.write_bytes(b"custom-gguf")
        response = await client.post("/v1/models", headers=headers, json={"id": "my-gguf", "path": str(gguf)})
        assert response.status == 201
        payload = await response.json()
        assert payload["sha256"] == real_sha256_file(gguf)
    assert hash_threads and all(thread != loop_thread for thread in hash_threads)


@pytest.mark.asyncio
async def test_model_download_hashes_off_the_event_loop(tmp_path, monkeypatch):
    import threading

    import local_translate.model_download as model_download_module

    paths = AppPaths.create(tmp_path / "state", tmp_path / "data")
    state = AppState(paths)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    def fake_huggingface(repo, filename, size_gb, model_id):
        del repo, filename, size_gb, model_id
        return model

    state.downloader._huggingface = fake_huggingface
    loop_thread = threading.get_ident()
    real_sha256_file = model_download_module.sha256_file
    hash_threads = []

    def spy(path):
        hash_threads.append(threading.get_ident())
        return real_sha256_file(path)

    monkeypatch.setattr(model_download_module, "sha256_file", spy)
    model_id = "plamo-3-translate-0.8b-GGUF"
    state.models.record_consent(model_id, "user-supplied")
    await state.downloader.download(model_id)
    assert hash_threads and all(thread != loop_thread for thread in hash_threads)
    assert state.models.file_record(model_id)["sha256"] == real_sha256_file(model)


async def start_resident_serve(tmp_path: Path, monkeypatch) -> tuple["asyncio.Task[None]", Path, dict]:
    monkeypatch.setenv("LOCAL_TRANSLATE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LOCAL_TRANSLATE_DATA_DIR", str(tmp_path / "data"))
    serve_task = asyncio.create_task(serve(port=0))
    paths = default_app_paths()
    info: dict | None = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if paths.daemon_info_path.exists():
            try:
                info = json.loads(paths.daemon_info_path.read_text(encoding="utf-8"))
                break
            except json.JSONDecodeError:
                pass
        await asyncio.sleep(0.05)
    assert isinstance(info, dict) and isinstance(info.get("port"), int)
    return serve_task, paths, info


@pytest.mark.asyncio
async def test_sigterm_stops_serve_and_releases_the_lock(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("Unix シグナルは Windows では使えません")
    import os
    import signal

    serve_task, paths, _info = await start_resident_serve(tmp_path, monkeypatch)
    lock = ProcessLock(paths.daemon_lock_path)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(serve_task, timeout=10)
    assert lock.acquire() is True
    lock.release()


@pytest.mark.asyncio
async def test_worker_stderr_does_not_block_the_child(tmp_path: Path):
    if sys.platform == "win32":
        pytest.skip("Unix のスクリプト起動を前提とします")
    script = tmp_path / "fake-worker"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'type': 'ready'}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('e' * (256 * 1024) + '\\n')\n"
        "sys.stderr.flush()\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if message.get('type') == 'transcribe':\n"
        "        sys.stdout.write(json.dumps({'type': 'end', 'id': message.get('id'), 'text': 'ok'}) + '\\n')\n"
        "        sys.stdout.flush()\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    log_path = tmp_path / "daemon.log"
    worker = SpeechWorkerClient(str(script), "fake-model", log_path=log_path)
    try:
        await asyncio.wait_for(worker.start(), timeout=30)
        result = await asyncio.wait_for(worker.transcribe(b"\x00\x00" * 16, request_id="t1"), timeout=15)
        assert result["text"] == "ok"
    finally:
        await worker.stop()
    assert len(log_path.read_bytes()) >= 200 * 1024


@pytest.mark.asyncio
async def test_serve_stderr_does_not_block_startup(tmp_path: Path):
    if sys.platform == "win32":
        pytest.skip("Unix のスクリプト起動を前提とします")
    script = tmp_path / "fake-serve"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "sys.stderr.write('e' * (256 * 1024) + '\\n')\n"
        "sys.stderr.flush()\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "class Health(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "    def log_message(self, *args):\n"
        "        pass\n"
        "HTTPServer(('127.0.0.1', port), Health).serve_forever()\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    log_path = tmp_path / "daemon.log"
    serve = SpeechServeProcess(str(script), ["fake-model"], log_path=log_path)
    try:
        await asyncio.wait_for(serve.start(pick_free_port()), timeout=30)
    finally:
        await serve.stop()
    assert len(log_path.read_bytes()) >= 200 * 1024


@pytest.mark.asyncio
async def test_control_shutdown_stops_serve_and_releases_the_lock(tmp_path, monkeypatch):
    serve_task, paths, info = await start_resident_serve(tmp_path, monkeypatch)
    lock = ProcessLock(paths.daemon_lock_path)
    assert lock.acquire() is False
    token = paths.token_path.read_text(encoding="utf-8").strip()
    async with aiohttp.ClientSession() as session:
        response = await session.post(
            f"http://127.0.0.1:{info['port']}/v1/control",
            json={"type": "shutdown"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status == 200
        assert (await response.json())["status"] == "stopping"
    await asyncio.wait_for(serve_task, timeout=10)
    assert lock.acquire() is True
    lock.release()


def _zip_installer(tmp_path: Path) -> RuntimeInstaller:
    digests = tmp_path / "state" / "runtimes.sha256.json"
    digests.parent.mkdir(parents=True, exist_ok=True)
    return RuntimeInstaller(tmp_path / "runtimes", digests_path=digests)


def test_runtime_install_refuses_an_archive_that_does_not_match_the_pin(tmp_path, monkeypatch):
    from local_translate import runtime_fetch
    from local_translate.errors import CompanionError

    asset = speech_asset_name("win32", "vulkan")
    payload = archive_bytes_for_tests(asset, "speech", b"#!/bin/sh\n")
    monkeypatch.setitem(runtime_fetch.RUNTIME_ASSET_SHA256, asset, "0" * 64)
    installer = _zip_installer(tmp_path)
    installer.opener = lambda url, destination: destination.write_bytes(payload)
    destination = installer.runtimes_dir / "speech.cpp" / "0.10.1" / "vulkan"
    with pytest.raises(Exception) as excinfo:
        installer.ensure_speech("vulkan", "win32")
    assert excinfo.value.code == "checksum_mismatch"
    assert not (destination / asset).exists()
    assert not (destination / "speech").exists() and not (destination / "speech.exe").exists()


def test_runtime_reuse_rechecks_the_recorded_digest(tmp_path, monkeypatch):
    from local_translate import runtime_fetch
    from local_translate.errors import CompanionError
    from local_translate.constants import SPEECH_CPP_VERSION

    asset = speech_asset_name("win32", "vulkan")
    payload = archive_bytes_for_tests(asset, "speech", b"payload-v1")
    monkeypatch.setitem(
        runtime_fetch.RUNTIME_ASSET_SHA256,
        asset,
        hashlib.sha256(payload).hexdigest(),
    )
    installer = _zip_installer(tmp_path)
    installer.opener = lambda url, destination: destination.write_bytes(payload)
    binary = installer.ensure_speech("vulkan", "win32")
    assert binary.is_file()
    digests = json.loads(installer.digests_path.read_text(encoding="utf-8"))
    assert digests[f"speech.cpp/{SPEECH_CPP_VERSION}/vulkan"]["binary_sha256"] == hashlib.sha256(b"payload-v1").hexdigest()
    binary.write_bytes(b"tampered")
    with pytest.raises(Exception) as excinfo:
        installer.ensure_speech("vulkan", "win32")
    assert excinfo.value.code == "runtime_binary_changed"
    assert not binary.exists()


def test_unpinned_runtime_records_the_first_download_and_rechecks_reinstalls(tmp_path, monkeypatch):
    from local_translate import runtime_fetch
    from local_translate.constants import WHISPER_CPP_VERSION
    from local_translate.errors import CompanionError

    asset = "whisper-bin-x64.zip"
    monkeypatch.delitem(runtime_fetch.RUNTIME_ASSET_SHA256, asset, raising=False)
    payload = archive_bytes_for_tests(asset, "whisper-cli", b"payload-v1")
    installer = _zip_installer(tmp_path)
    installer.opener = lambda url, destination: destination.write_bytes(payload)
    binary = installer.ensure_whisper("win32")
    digests = json.loads(installer.digests_path.read_text(encoding="utf-8"))
    assert digests[f"whisper.cpp/{WHISPER_CPP_VERSION}"]["archive_sha256"] == hashlib.sha256(payload).hexdigest()
    binary.unlink()
    tampered = archive_bytes_for_tests(asset, "whisper-cli", b"payload-v2")
    installer.opener = lambda url, destination: destination.write_bytes(tampered)
    with pytest.raises(Exception) as excinfo:
        installer.ensure_whisper("win32")
    assert excinfo.value.code == "checksum_mismatch"
