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
from local_translate.runtime_fetch import (
    RuntimeInstaller,
    archive_bytes_for_tests,
    llama_asset_name,
    llama_cudart_asset_name,
    speech_asset_name,
)
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


def test_boot_applies_the_stored_parallel_to_the_translator(tmp_path: Path):
    paths = AppPaths.create(tmp_path / "state", tmp_path / "data")
    paths.ensure()
    paths.settings_path.write_text(json.dumps({"parallel": 3}), encoding="utf-8")
    state = AppState(paths)
    assert state.translator.parallel_slots == 3


def test_cuda_setting_uses_cuda_for_llama_and_vulkan_for_speech():
    from local_translate.backend_choice import resolve_llama_backend, speech_device_name, whisper_backend_name

    gpu = GpuInfo("cuda", "RTX", 16376, 14000, False, 32000, True, True)
    assert resolve_llama_backend("cuda", gpu) == "cuda"
    assert resolve_llama_backend("auto", gpu) == "cuda"
    assert speech_device_name("cuda", gpu, False) == "gpu"
    assert speech_device_name("cpu", gpu, False) == "cpu"
    assert whisper_backend_name("cuda", gpu) == "cuda"
    no_vulkan = GpuInfo("cuda", "RTX", 16376, 14000, False, 32000, False, True)
    assert speech_device_name("cuda", no_vulkan, False) == "cpu"


@pytest.mark.asyncio
async def test_install_runtime_requests_the_llama_cuda_build(tmp_path: Path):
    paths = AppPaths.create(tmp_path / "state", tmp_path / "data")
    state = AppState(paths)
    state.gpu = GpuInfo("cuda", "RTX", 16376, 14000, False, 32000, True, True)
    state.settings.update({"gpu_backend": "cuda"})
    seen: dict[str, str] = {}

    def ensure_llama(backend: str, platform: str | None = None) -> Path:
        del platform
        seen["llama"] = backend
        return tmp_path / "llama-server"

    state.installer.ensure_llama = ensure_llama
    app = create_app(state)
    async with TestClient(TestServer(app)) as client:
        headers = {"Authorization": f"Bearer {state.token}"}
        response = await client.post("/v1/runtimes/llama/install", headers=headers)
        assert response.status == 200
    assert seen["llama"] == "cuda"


def test_vram_tiers():
    big = suggest_runtime(GpuInfo("cuda", "RTX", 24576, 20000, False, 32000, True, True))
    assert big.model_id == "plamo-3-translate-31b-GGUF-Q4_K_M"
    assert big.context_size == 16384
    mid = suggest_runtime(GpuInfo("cuda", "RTX", 16384, 14000, False, 32000, True, True))
    assert "plamo-3-translate-31b-GGUF-Q3_K_M" in mid.alternatives
    reported_16gb = suggest_runtime(GpuInfo("cuda", "RTX 4070 Ti SUPER", 16376, 14000, False, 32000, True, True))
    assert reported_16gb.model_id == "plamo-3-translate-8b-GGUF"
    assert reported_16gb.context_size == 8192
    assert "plamo-3-translate-31b-GGUF-Q3_K_M" in reported_16gb.alternatives
    just_under = suggest_runtime(GpuInfo("cuda", "RTX", 16 * 1024 - 513, 10000, False, 32000, True, True))
    assert "plamo-3-translate-31b-GGUF-Q3_K_M" not in just_under.alternatives
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
    histories: list[list[dict[str, str]]] = []

    async def transcribe(pcm: bytes) -> str:
        calls["n"] += 1
        return f"文{calls['n']}"

    async def translate(text: str, history: list[dict[str, str]] | None = None) -> str:
        histories.append(list(history or []))
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
    assert histories[-1] == []
    assert pcm16_rms(silence) == 0

    # The next final segment carries the previous one as translation context.
    await manager.push_pcm(speech)
    await manager.push_pcm(speech)
    second = await manager.push_pcm(silence)
    assert second[-1].final is True
    assert second[-1].translation == "訳:文6"
    assert histories[-1] == [{"source": "文3", "translation": "訳:文3"}]


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
        assert isinstance(body["rss_mb"], int) and body["rss_mb"] > 0
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

    def fake_huggingface(repo, filename, revision):
        del repo, filename, revision
        return model

    state.downloader._huggingface = fake_huggingface
    loop_thread = threading.get_ident()
    real_sha256_file = model_download_module.sha256_file
    state.downloader._fetch_metadata = lambda repo, filename: (real_sha256_file(model), "c" * 40)
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


def test_huggingface_worker_never_touches_the_registry(tmp_path: Path, monkeypatch):
    import types

    from local_translate.event_bus import EventBus
    from local_translate.model_download import ModelDownloader
    from local_translate.model_registry import ModelRegistry

    models_dir = tmp_path / "models"
    registry = ModelRegistry(tmp_path / "models.json", models_dir)

    def poison(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"ワーカー関数から registry.{name} を呼んではいけない")

        return fail

    for name in ("save", "set_download_status", "mark_file", "record_consent"):
        monkeypatch.setattr(registry, name, poison(name))

    target = models_dir / "dummy__repo" / "model.gguf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"gguf")

    def fake_hf_hub_download(**kwargs):
        assert kwargs["local_dir"] == str(models_dir / "dummy__repo")
        return str(target)

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(hf_hub_download=fake_hf_hub_download))
    downloader = ModelDownloader(registry, EventBus(), models_dir, lambda: None)
    assert downloader._huggingface("dummy/repo", "model.gguf", "d" * 40) == target


def test_registry_save_survives_concurrent_writers(tmp_path: Path):
    import threading

    from local_translate.model_registry import ModelRegistry

    path = tmp_path / "models.json"
    registry = ModelRegistry(path, tmp_path / "models")
    # tmp 書き込み窓を広げて旧実装の truncate/rename 競合を確実に検出するためのダミーデータ。
    registry.data["ballast"] = "x" * 32768
    writer_count = 8
    rounds = 80
    failures: list[BaseException] = []
    start = threading.Barrier(writer_count + 1)

    def writer(index: int) -> None:
        try:
            start.wait()
            for step in range(rounds):
                registry.data["downloads"][f"writer-{index}"] = {"status": "downloading", "progress": step}
                registry.save()
        except BaseException as exc:
            failures.append(exc)

    stop = threading.Event()

    def reader() -> None:
        start.wait()
        probe = ModelRegistry(path, tmp_path / "models")
        try:
            while not stop.is_set():
                probe.load()
        except BaseException as exc:
            failures.append(exc)

    writers = [threading.Thread(target=writer, args=(index,)) for index in range(writer_count)]
    probe = threading.Thread(target=reader)
    for thread in writers:
        thread.start()
    probe.start()
    for thread in writers:
        thread.join()
    stop.set()
    probe.join()

    assert failures == []
    final = ModelRegistry(path, tmp_path / "models")
    for index in range(writer_count):
        assert final.data["downloads"][f"writer-{index}"] == {"status": "downloading", "progress": rounds - 1}


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


def test_windows_cuda_llama_installs_cudart_beside_the_server(tmp_path, monkeypatch):
    from local_translate import runtime_fetch

    llama_asset = llama_asset_name("win32", "cuda")
    cudart_asset = llama_cudart_asset_name("win32", "cuda")
    assert cudart_asset
    llama_zip = archive_bytes_for_tests(llama_asset, "llama-server.exe", b"server")
    cudart_zip = archive_bytes_for_tests(cudart_asset, "cudart64_12.dll", b"cudart")
    monkeypatch.setitem(runtime_fetch.RUNTIME_ASSET_SHA256, llama_asset, hashlib.sha256(llama_zip).hexdigest())
    monkeypatch.setitem(runtime_fetch.RUNTIME_ASSET_SHA256, cudart_asset, hashlib.sha256(cudart_zip).hexdigest())
    installer = _zip_installer(tmp_path)
    seen: list[str] = []

    def opener(url: str, destination: Path) -> None:
        seen.append(url)
        destination.write_bytes(cudart_zip if "cudart" in url else llama_zip)

    installer.opener = opener
    binary = installer.ensure_llama("cuda", "win32")
    assert binary.name == "llama-server.exe"
    assert (binary.parent / "cudart64_12.dll").is_file()
    assert any("cudart" in url for url in seen)
    installer.opener = lambda _url, _destination: (_ for _ in ()).throw(AssertionError("再取得しない"))
    assert installer.ensure_llama("cuda", "win32") == binary


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


def test_windows_cuda_whisper_installs_into_its_own_directory(tmp_path, monkeypatch):
    from local_translate import runtime_fetch
    from local_translate.runtime_fetch import whisper_asset_name

    asset = whisper_asset_name("win32", "cuda")
    payload = archive_bytes_for_tests(asset, "whisper-cli.exe", b"cuda-whisper")
    monkeypatch.setitem(runtime_fetch.RUNTIME_ASSET_SHA256, asset, hashlib.sha256(payload).hexdigest())
    installer = _zip_installer(tmp_path)
    installer.opener = lambda url, destination: destination.write_bytes(payload)
    binary = installer.ensure_whisper("win32", "cuda")
    assert binary.name == "whisper-cli.exe"
    assert binary.parent.name == "cuda"


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
    assert digests[f"whisper.cpp/{WHISPER_CPP_VERSION}/cpu"]["archive_sha256"] == hashlib.sha256(payload).hexdigest()
    binary.unlink()
    tampered = archive_bytes_for_tests(asset, "whisper-cli", b"payload-v2")
    installer.opener = lambda url, destination: destination.write_bytes(tampered)
    with pytest.raises(Exception) as excinfo:
        installer.ensure_whisper("win32")
    assert excinfo.value.code == "checksum_mismatch"


def test_windows_vulkan_loader_counts_when_vulkaninfo_is_missing(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows の Vulkan ローダー検出です")
    from local_translate.gpu_detect import detect_vulkan

    monkeypatch.setattr("local_translate.gpu_detect.shutil.which", lambda _name: None)
    available, name = detect_vulkan()
    assert available is True
    assert name is None


def test_windows_detached_daemon_survives_a_job_that_forbids_breakaway():
    if sys.platform != "win32":
        pytest.skip("Windows のプロセス切り離しです")
    import ctypes
    import time

    from local_translate.process_spawn import CREATE_NO_WINDOW, spawn_detached_daemon

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]

    class IoCounters(ctypes.Structure):
        _fields_ = [("count", ctypes.c_ulonglong)] * 6

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_ulong),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_ulong),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_ulong),
            ("SchedulingClass", ctypes.c_ulong),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("Basic", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("lpReserved", ctypes.c_wchar_p),
            ("lpDesktop", ctypes.c_wchar_p),
            ("lpTitle", ctypes.c_wchar_p),
            ("dwX", ctypes.c_ulong),
            ("dwY", ctypes.c_ulong),
            ("dwXSize", ctypes.c_ulong),
            ("dwYSize", ctypes.c_ulong),
            ("dwXCountChars", ctypes.c_ulong),
            ("dwYCountChars", ctypes.c_ulong),
            ("dwFillAttribute", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("wShowWindow", ctypes.c_ushort),
            ("cbReserved2", ctypes.c_ushort),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", ctypes.c_void_p),
            ("hStdOutput", ctypes.c_void_p),
            ("hStdError", ctypes.c_void_p),
        ]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", ctypes.c_void_p),
            ("hThread", ctypes.c_void_p),
            ("dwProcessId", ctypes.c_ulong),
            ("dwThreadId", ctypes.c_ulong),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    info = ExtendedLimit()
    info.Basic.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, breakaway は許さない
    kernel32.SetInformationJobObject(ctypes.c_void_p(job), 9, ctypes.byref(info), ctypes.sizeof(info))
    startup = StartupInfo()
    startup.cb = ctypes.sizeof(startup)
    created = ProcessInformation()
    helper = tmp_path_script = None
    import tempfile
    from pathlib import Path

    directory = Path(tempfile.mkdtemp())
    pidfile = directory / "pid.txt"
    helper = directory / "helper.py"
    helper.write_text(
        "from pathlib import Path\nimport sys\n"
        "from local_translate.process_spawn import spawn_detached_daemon\n"
        f"pid = spawn_detached_daemon([sys.executable, '-c', 'import time; time.sleep(30)'], Path(r'{directory}'))\n"
        f"Path(r'{pidfile}').write_text(str(pid), encoding='utf-8')\n"
        "import time; time.sleep(20)\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{helper}"'
    kernel32.CreateProcessW(None, command, None, None, False, 0x00000004 | CREATE_NO_WINDOW, None, None, ctypes.byref(startup), ctypes.byref(created))
    kernel32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(created.hProcess))
    kernel32.ResumeThread(ctypes.c_void_p(created.hThread))
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.1)
    assert pidfile.exists(), "切り離したプロセスの pid が記録されませんでした"
    grandchild = int(pidfile.read_text(encoding="utf-8"))
    kernel32.CloseHandle(ctypes.c_void_p(created.hThread))
    kernel32.CloseHandle(ctypes.c_void_p(created.hProcess))
    kernel32.CloseHandle(ctypes.c_void_p(job))
    time.sleep(0.6)
    handle = kernel32.OpenProcess(0x1000, False, grandchild)
    assert handle, "Job を閉じた後も daemon 相当のプロセスが残る必要があります"
    code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    assert code.value == 259
    killer = kernel32.OpenProcess(0x0001, False, grandchild)
    if killer:
        kernel32.TerminateProcess(ctypes.c_void_p(killer), 1)
        kernel32.CloseHandle(ctypes.c_void_p(killer))


@pytest.mark.asyncio
async def test_verify_references_summarizes_cer_without_transcripts(tmp_path: Path, monkeypatch):
    from local_translate import asr_verify

    async def fake_run_asr(speech_bin: str, model: str, wav_path: Path) -> tuple[str, float]:
        del speech_bin, model, wav_path
        return "秘密の字幕", 0.2

    monkeypatch.setattr(asr_verify, "run_asr", fake_run_asr)
    references = tmp_path / "references.jsonl"
    references.write_text(
        json.dumps({"wav": "a.wav", "text": "秘密の字幕"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report = await asr_verify.verify("speech", None, references, tmp_path / "detail.json")
    assert report["cer"]["measured"] == 0.0
    summary = asr_verify.summarize_asr_report(report)
    dumped = json.dumps(summary, ensure_ascii=False)
    assert "秘密の字幕" not in dumped
    assert "samples" not in summary
    assert summary["cer"]["measured"] == 0.0


def test_codepoint_patch_and_asr_summary_omit_transcripts():
    from local_translate.asr.transcript_patch import apply_codepoint_patch
    from local_translate.asr_verify import summarize_asr_report

    assert apply_codepoint_patch("abcdef", [[2, 4, "X"]]) == "abXef"
    assert apply_codepoint_patch("abcdef", [[4, 5, ""], [1, 2, "Z"]]) == "aZcdf"
    summary = summarize_asr_report(
        {
            "dataset": "FLEURS ja_jp test",
            "license": "CC BY 4.0",
            "samples": [{"reference": "秘密", "hypothesis": "秘密", "cer": 0.0, "latency_sec": 1.2}],
            "cer": {"measured": 0.0},
        }
    )
    assert summary["count"] == 1
    assert "reference" not in json.dumps(summary, ensure_ascii=False)
    assert "秘密" not in json.dumps(summary, ensure_ascii=False)


def test_daemon_log_keeps_japanese_as_utf8(tmp_path: Path):
    import logging

    from local_translate.daemon import attach_daemon_log

    previous_stderr = sys.stderr
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    path = tmp_path / "daemon.log"
    try:
        attach_daemon_log(path)
        logging.getLogger("local_translate.daemon").info("日本語ログ")
        sys.stderr.flush()
        assert "日本語ログ" in path.read_text(encoding="utf-8")
    finally:
        sys.stderr = previous_stderr
        root.handlers = previous_handlers


def test_windows_detached_daemon_has_no_console_window(tmp_path: Path):
    if sys.platform != "win32":
        pytest.skip("Windows のコンソール窓の確認です")
    import time

    from local_translate.process_spawn import spawn_detached_daemon

    probe = tmp_path / "console_probe.py"
    child_probe = tmp_path / "child_probe.py"
    result_path = tmp_path / "console.json"
    child_probe.write_text(
        "import ctypes\n"
        "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "user32 = ctypes.WinDLL('user32', use_last_error=True)\n"
        "kernel32.GetConsoleWindow.restype = ctypes.c_void_p\n"
        "handle = kernel32.GetConsoleWindow() or 0\n"
        "print(handle, bool(handle and user32.IsWindowVisible(ctypes.c_void_p(handle))))\n",
        encoding="utf-8",
    )
    probe.write_text(
        "import ctypes, json, subprocess, sys\n"
        "from pathlib import Path\n"
        "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "user32 = ctypes.WinDLL('user32', use_last_error=True)\n"
        "kernel32.GetConsoleWindow.restype = ctypes.c_void_p\n"
        f"grandchild = subprocess.run([sys.executable, r'{child_probe}'], capture_output=True, text=True)\n"
        "own = kernel32.GetConsoleWindow() or 0\n"
        f"Path(r'{result_path}').write_text(json.dumps({{\n"
        "    'own_hwnd': own,\n"
        "    'own_visible': bool(own and user32.IsWindowVisible(ctypes.c_void_p(own))),\n"
        "    'child': grandchild.stdout.strip(),\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    spawn_detached_daemon([sys.executable, str(probe)], tmp_path)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not result_path.exists():
        time.sleep(0.1)
    assert result_path.exists(), "コンソール確認のプローブが結果を書きませんでした"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["own_hwnd"] == 0
    assert payload["own_visible"] is False
    assert payload["child"].startswith("0 ")


def test_windows_child_job_kills_the_process_when_closed():
    if sys.platform != "win32":
        pytest.skip("Windows の Job Object です")
    import subprocess

    from local_translate.process_spawn import CREATE_NO_WINDOW, ChildProcessJob

    job = ChildProcessJob()
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=CREATE_NO_WINDOW)
    job.assign(process.pid)
    job.close()
    process.wait(timeout=5)
    assert process.returncode is not None


def test_windows_reports_available_ram_and_resident_memory():
    if sys.platform != "win32":
        pytest.skip("Windows のメモリ取得です")
    from local_translate.gpu_detect import available_ram_mb
    from local_translate.http_api import resident_memory_mb

    ram = available_ram_mb()
    rss = resident_memory_mb()
    assert isinstance(ram, int) and ram > 0
    assert isinstance(rss, int) and rss > 0


def test_data_dir_is_private_after_ensure(tmp_path: Path):
    if sys.platform == "win32":
        pytest.skip("POSIX パーミッションの検証です")
    paths = AppPaths.create(tmp_path / "state", tmp_path / "data")
    paths.ensure()
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "data").stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_whisper_passes_auto_language_to_the_cli(tmp_path: Path, monkeypatch):
    from local_translate.asr.whisper_backend import WhisperCppBackend

    seen: dict[str, tuple] = {}

    class Finished:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*argv, **kwargs):
        del kwargs
        seen["argv"] = argv
        return Finished()

    monkeypatch.setattr("local_translate.asr.whisper_backend.create_hidden_subprocess", fake_exec)
    model = tmp_path / "whisper.bin"
    model.write_bytes(b"ggml")
    text = await WhisperCppBackend(sys.executable, model).transcribe_pcm(b"\x00\x00" * 16, language="auto")
    assert text == ""
    argv = seen["argv"]
    assert argv[argv.index("-l") + 1] == "auto"


@pytest.mark.asyncio
async def test_whisper_work_files_are_removed_even_when_transcription_fails(tmp_path: Path):
    from local_translate.asr.whisper_backend import WhisperCppBackend
    from local_translate.errors import CompanionError

    model = tmp_path / "models" / "whisper-base.ggml"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"ggml")
    if sys.platform == "win32":
        pytest.skip("Unix の bin ラッパーを前提とします")

    async def work_files():
        directory = model.parent / "whisper-work"
        return directory / "utterance.wav", directory / "utterance.json"

    ok = WhisperCppBackend("/bin/true", model)
    text = await ok.transcribe_pcm(b"\x00\x00" * 16)
    assert text == ""
    wav, json_path = await work_files()
    assert not wav.exists() and not json_path.exists()

    broken = WhisperCppBackend("/bin/false", model)
    with pytest.raises(CompanionError):
        await broken.transcribe_pcm(b"\x00\x00" * 16)
    assert not wav.exists() and not json_path.exists()


@pytest.mark.asyncio
async def test_download_verifies_the_published_huggingface_hash(tmp_path: Path):
    import hashlib

    from local_translate.event_bus import EventBus
    from local_translate.model_download import ModelDownloader
    from local_translate.model_registry import ModelRegistry

    registry = ModelRegistry(tmp_path / "models.json", tmp_path / "models")
    events = EventBus()
    downloader = ModelDownloader(registry, events, tmp_path / "models", lambda: None)
    payload = b"gguf-bytes"

    def fake_huggingface(repo: str, filename: str, revision: str | None) -> Path:
        target = tmp_path / "models" / repo.replace("/", "__") / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target

    downloader._huggingface = fake_huggingface
    registry.record_consent("plamo-3-translate-0.8b-GGUF", "plamo-community")
    good = hashlib.sha256(payload).hexdigest()
    downloader._fetch_metadata = lambda repo, filename: (good, "c" * 40)
    await downloader.download("plamo-3-translate-0.8b-GGUF")
    assert registry.download_status("plamo-3-translate-0.8b-GGUF")["status"] == "ready"
    assert [event["type"] for event in events.recent if event["type"].startswith("model.download")] == [
        "model.download.started",
        "model.download.done",
    ]

    downloader._fetch_metadata = lambda repo, filename: ("0" * 64, "b" * 40)
    bad_model = tmp_path / "models" / "pfnet__plamo-3-translate-8b-GGUF" / "plamo-3-translate-8b-Q4_K_M.gguf"
    registry.record_consent("plamo-3-translate-8b-GGUF", "plamo-community")
    with pytest.raises(Exception, match="SHA-256"):
        await downloader.download("plamo-3-translate-8b-GGUF")
    # 照合落ちの実体は消す。残っていると候補パスや ready 表示が無検証のまま採用してしまう。
    assert not bad_model.exists()
    assert registry.resolve_model_path("plamo-3-translate-8b-GGUF") is None
    assert registry.download_status("plamo-3-translate-8b-GGUF")["status"] == "error"
    row = next(item for item in registry.list_models(None, None, None) if item["id"] == "plamo-3-translate-8b-GGUF")
    assert row["status"] != "ready"


def test_resolve_model_path_ignores_unrecorded_candidate_files(tmp_path: Path):
    from local_translate.model_registry import ModelRegistry

    registry = ModelRegistry(tmp_path / "models.json", tmp_path / "models")
    # ダウンロード先の候補パスに実在ファイルを置いても、検証済みレコードが無い限り採用しない。
    candidate = tmp_path / "models" / "pfnet__plamo-3-translate-0.8b-GGUF" / "plamo-3-translate-0.8b-Q4_K_M.gguf"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"gguf")
    assert registry.resolve_model_path("plamo-3-translate-0.8b-GGUF") is None
    row = next(item for item in registry.list_models(None, None, None) if item["id"] == "plamo-3-translate-0.8b-GGUF")
    assert row["status"] != "ready"
    # whisper.cpp の候補パス(ggml-base.bin)もレコードなしでは ready にならない。
    (tmp_path / "models" / "ggml-base.bin").write_bytes(b"ggml")
    row = next(item for item in registry.list_models(None, None, None) if item["id"] == "whisper-base")
    assert row["status"] != "ready"
    # ダウンロードボタン経由でレコードを作れば解決し、ready になる。
    registry.mark_file("plamo-3-translate-0.8b-GGUF", candidate, "0" * 64)
    assert registry.resolve_model_path("plamo-3-translate-0.8b-GGUF") == candidate
    row = next(item for item in registry.list_models(None, None, None) if item["id"] == "plamo-3-translate-0.8b-GGUF")
    assert row["status"] == "ready"


@pytest.mark.asyncio
async def test_metadata_and_download_pin_the_same_revision(tmp_path: Path, monkeypatch):
    import hashlib
    import sys
    import types

    from local_translate.event_bus import EventBus
    from local_translate.model_download import ModelDownloader
    from local_translate.model_registry import ModelRegistry

    commit = "a" * 40
    payload = b"gguf-bytes"
    model_file = tmp_path / "models" / "pfnet__plamo-3-translate-0.8b-GGUF" / "plamo-3-translate-0.8b-Q4_K_M.gguf"
    download_kwargs: dict = {}

    def fake_hf_hub_download(**kwargs):
        download_kwargs.update(kwargs)
        model_file.parent.mkdir(parents=True, exist_ok=True)
        model_file.write_bytes(payload)
        return str(model_file)

    class FakeApi:
        def model_info(self, repo_id: str, files_metadata: bool, timeout: float) -> object:
            del repo_id, files_metadata, timeout
            sibling = types.SimpleNamespace(rfilename=model_file.name, lfs={"sha256": hashlib.sha256(payload).hexdigest()})
            return types.SimpleNamespace(sha=commit, siblings=[sibling])

    fake_hub = types.SimpleNamespace(
        HfApi=FakeApi,
        hf_hub_download=fake_hf_hub_download,
        utils=types.SimpleNamespace(HfHubHTTPError=type("HfHubHTTPError", (Exception,), {})),
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.utils", fake_hub.utils)
    registry = ModelRegistry(tmp_path / "models.json", tmp_path / "models")
    downloader = ModelDownloader(registry, EventBus(), tmp_path / "models", lambda: None)
    model_id = "plamo-3-translate-0.8b-GGUF"
    registry.record_consent(model_id, "plamo-community")
    await downloader.download(model_id)
    # メタ取得の model_info と本体の hf_hub_download が同一 revision を見ることを主張する。
    assert download_kwargs["revision"] == commit
    assert registry.download_status(model_id)["status"] == "ready"


def test_model_info_retries_once_with_a_timeout_on_transient_errors():
    import types

    from local_translate import model_download as model_download_module

    class FlakyApi:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def model_info(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise TimeoutError("一時的なタイムアウト")
            sibling = types.SimpleNamespace(rfilename="model.gguf", lfs={"sha256": "0" * 64})
            return types.SimpleNamespace(sha="a" * 40, siblings=[sibling])

    api = FlakyApi()
    info = model_download_module._model_info_with_retry(api, "pfnet/plamo-3-translate-0.8b-GGUF")
    assert info.sha == "a" * 40
    # タイムアウトが渡り、一時失敗の 1 回目のあとに 2 回目で成功する。合計 2 試行。
    assert api.calls == [
        {"repo_id": "pfnet/plamo-3-translate-0.8b-GGUF", "files_metadata": True, "timeout": 10.0},
        {"repo_id": "pfnet/plamo-3-translate-0.8b-GGUF", "files_metadata": True, "timeout": 10.0},
    ]
