"""Short-lived native-messaging host. It finds or starts the daemon and returns the loopback token."""

from __future__ import annotations

import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from local_translate.constants import APP_VERSION
from local_translate.nm_framing import read_stdin_message, write_stdout_message
from local_translate.paths import default_app_paths
from local_translate.process_spawn import spawn_detached_daemon
from local_translate.token_store import load_or_create_token

log = logging.getLogger("local_translate.nm_host")


def daemon_healthy(port: int, token: str) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def read_daemon_port(info_path: Path) -> int | None:
    if not info_path.exists():
        return None
    try:
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    port = payload.get("port")
    return int(port) if isinstance(port, int) else None


def ensure_daemon(python: str | None = None) -> tuple[int, str]:
    paths = default_app_paths()
    paths.ensure()
    token = load_or_create_token(paths.token_path)
    port = read_daemon_port(paths.daemon_info_path)
    if port and daemon_healthy(port, token):
        return port, token
    executable = python or sys.executable
    # 切り離したプロセスには標準出力を渡せない。daemon が自分でログを開く。
    spawn_detached_daemon(
        [executable, "-m", "local_translate.daemon"],
        Path(__file__).resolve().parents[2],
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        port = read_daemon_port(paths.daemon_info_path)
        if port and daemon_healthy(port, token):
            return port, token
        time.sleep(0.2)
    raise RuntimeError("daemon が起動しませんでした")


def relay(port: int, token: str, message: dict) -> dict:
    kind = message.get("type")
    if kind in {None, "status", "hello"}:
        url = f"http://127.0.0.1:{port}/v1/status"
        method = "GET"
        body = None
    else:
        url = f"http://127.0.0.1:{port}/v1/control"
        method = "POST"
        body = json.dumps(message).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def handshake(port: int, token: str) -> dict:
    return {
        "port": port,
        "token": token,
        "version": APP_VERSION,
        "capabilities": ["translate", "subtitles", "custom-gguf", "whisper", "site-rules"],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s %(message)s")
    try:
        port, token = ensure_daemon()
    except Exception as exc:
        write_stdout_message({"type": "error", "message": str(exc)})
        raise SystemExit(1) from exc
    while True:
        message = read_stdin_message()
        if message is None:
            return
        try:
            if message.get("type") in {None, "hello"}:
                write_stdout_message(handshake(port, token))
            else:
                write_stdout_message(relay(port, token, message))
        except Exception as exc:
            write_stdout_message({"type": "error", "message": str(exc)})


if __name__ == "__main__":
    main()
