"""Resident loopback daemon. Chrome starts it through the native-messaging host."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time

from aiohttp import web

from local_translate.constants import APP_VERSION
from local_translate.event_bus import EventBus
from local_translate.extension_identity import bundled_extension_id
from local_translate.gpu_detect import detect_gpu
from local_translate.http_api import create_app
from local_translate.json_store import write_json_atomic
from local_translate.llama_server import LlamaServerManager
from local_translate.model_download import ModelDownloader
from local_translate.model_registry import ModelRegistry
from local_translate.paths import AppPaths, default_app_paths
from local_translate.process_lock import ProcessLock
from local_translate.runtime_fetch import RuntimeInstaller
from local_translate.settings_store import SettingsStore
from local_translate.vram_presets import suggest_runtime
from local_translate.speech_runtime import SpeechRuntime
from local_translate.token_store import load_or_create_token
from local_translate.translate_service import TranslateService

log = logging.getLogger("local_translate.daemon")


class AppState:
    def __init__(self, paths: AppPaths) -> None:
        paths.ensure()
        self.paths = paths
        self.token = load_or_create_token(paths.token_path)
        self.settings = SettingsStore(paths.settings_path)
        self.models = ModelRegistry(paths.registry_path, paths.models_dir)
        self.events = EventBus()
        self.gpu = detect_gpu()
        # 設定ファイルがない、または active_translate_model が保存されていない
        # ときだけ推奨モデルを既定にする。既存の利用者の選択は変えない。
        if "active_translate_model" not in self.settings.stored_keys:
            self.settings.update({"active_translate_model": suggest_runtime(self.gpu).model_id})
        self.installer = RuntimeInstaller(paths.runtimes_dir, digests_path=paths.runtime_digests_path)
        self.llama = LlamaServerManager(self.installer, self.events, self.gpu, int(self.settings.data["idle_timeout_sec"]))
        self.llama.log_path = paths.daemon_log_path
        self.speech = SpeechRuntime(self.installer, self.models, self.settings, self.events, self.gpu)
        self.speech.log_path = paths.daemon_log_path
        parallel = int(self.settings.data.get("parallel") or suggest_runtime(self.gpu).parallel)
        self.translator = TranslateService(self.llama, paths, self.settings, parallel=parallel)
        self.downloader = ModelDownloader(self.models, self.events, paths.models_dir, self.speech.ensure_speech_binary)
        self.started_at = time.time()
        self.stop_event = asyncio.Event()
        self.subtitle_sessions = 0
        self.download_tasks: dict = {}
        self.allowed_extension_ids = _allowed_ids(paths)
        self.lock: ProcessLock | None = None


def _allowed_ids(paths: AppPaths) -> set[str]:
    import json
    import os

    ids = {bundled_extension_id()}
    extra = os.environ.get("LOCAL_TRANSLATE_EXTENSION_IDS", "")
    ids.update(item.strip() for item in extra.split(",") if item.strip())
    if paths.extension_ids_path.exists():
        stored = json.loads(paths.extension_ids_path.read_text(encoding="utf-8"))
        if isinstance(stored, list):
            ids.update(str(item) for item in stored)
    return ids


async def serve(port: int = 0) -> None:
    paths = default_app_paths()
    paths.ensure()
    lock = ProcessLock(paths.daemon_lock_path)
    if not lock.acquire():
        log.error("別の daemon がロックを持っています")
        raise SystemExit(1)
    state = AppState(paths)
    state.lock = lock
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, state.stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows やテスト以外のループではシグナルハンドラを登録できない
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    sockets = site._server.sockets if site._server else []
    actual = sockets[0].getsockname()[1] if sockets else port
    write_json_atomic(
        paths.daemon_info_path,
        {"pid": __import__("os").getpid(), "port": actual, "version": APP_VERSION},
    )
    log.info("daemon listening on 127.0.0.1:%s", actual)
    try:
        await state.stop_event.wait()
        log.info("停止要求を受け付けました")
    finally:
        await runner.cleanup()
        lock.release()


def attach_daemon_log(path) -> None:
    """Append daemon logs as UTF-8. A detached Windows process cannot inherit the host's handles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a", encoding="utf-8", errors="replace")
    sys.stderr = stream
    logging.basicConfig(level=logging.INFO, stream=stream, format="%(levelname)s %(name)s %(message)s", force=True)


def configure_daemon_logging(path) -> None:
    """Windows opens its own UTF-8 log. POSIX keeps the stderr the host already redirected."""
    if os.name == "nt":
        attach_daemon_log(path)
        return
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s %(message)s")


def main() -> None:
    from local_translate.paths import default_app_paths

    configure_daemon_logging(default_app_paths().daemon_log_path)
    parser = argparse.ArgumentParser(description="Local translation companion daemon")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.port))
    except KeyboardInterrupt:
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
