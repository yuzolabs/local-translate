"""Download the pinned 0.8B GGUF if needed and record one translation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion" / "src"))
from local_translate.process_spawn import spawn_detached_daemon

MODEL_ID = "plamo-3-translate-0.8b-GGUF"


def request(port: int, token: str, method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def daemon_healthy(port: int, token: str) -> bool:
    try:
        request(port, token, "GET", "/v1/status", timeout=2)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def read_daemon_endpoint(state: Path) -> tuple[int, str] | None:
    info_path = state / "daemon.json"
    token_path = state / "token"
    if not info_path.exists() or not token_path.exists():
        return None
    try:
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    port = payload.get("port")
    token = token_path.read_text(encoding="utf-8").strip()
    if not isinstance(port, int) or not token:
        return None
    return port, token


def lock_is_free(lock_path: Path) -> bool:
    if sys.platform == "win32":
        return True
    import fcntl

    handle = None
    try:
        handle = open(lock_path, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
    finally:
        if handle:
            handle.close()


def ensure_daemon(state: Path) -> tuple[int, str]:
    endpoint = read_daemon_endpoint(state)
    if endpoint and daemon_healthy(*endpoint):
        print(f"既存の daemon (port {endpoint[0]}) を再利用します", flush=True)
        return endpoint
    if not lock_is_free(state / "daemon.lock"):
        raise SystemExit("既存の daemon がロックを保持していますが応答しません。プロセスを確認してください。")
    os.environ["LOCAL_TRANSLATE_STATE_DIR"] = str(state)
    spawn_detached_daemon([sys.executable, "-m", "local_translate.daemon"], ROOT / "companion")
    deadline = time.time() + 20
    while time.time() < deadline:
        endpoint = read_daemon_endpoint(state)
        if endpoint and daemon_healthy(*endpoint):
            return endpoint
        time.sleep(0.3)
    raise SystemExit("daemon が起動しませんでした。state/daemon.log を確認してください。")


def default_state_dir() -> Path:
    override = os.environ.get("LOCAL_TRANSLATE_STATE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "local-translate-extension"
    return Path.home() / ".local" / "state" / "local-translate-extension"


def main() -> None:
    parser = argparse.ArgumentParser(description="0.8B モデルで翻訳の疎通を記録する")
    parser.add_argument("--backend", choices=("auto", "cuda", "vulkan", "cpu"), default="cpu")
    args = parser.parse_args()
    state = default_state_dir()
    state.mkdir(parents=True, exist_ok=True)
    port, token = ensure_daemon(state)
    request(port, token, "POST", f"/v1/models/{MODEL_ID}/consent", {"accepted": True})
    settings = {
        "gpu_backend": args.backend,
        "context_size": 512,
        "parallel": 1,
        "active_translate_model": MODEL_ID,
    }
    if args.backend == "cpu":
        settings["n_gpu_layers"] = 0
    request(port, token, "POST", "/v1/settings", settings)
    models = request(port, token, "GET", "/v1/models")["models"]
    ready = next(item for item in models if item["id"] == MODEL_ID)["status"] == "ready"
    if not ready:
        request(port, token, "POST", f"/v1/models/{MODEL_ID}/download", {})
        deadline = time.time() + 1800
        while time.time() < deadline:
            row = next(item for item in request(port, token, "GET", "/v1/models")["models"] if item["id"] == MODEL_ID)
            print(row["status"], row.get("progress"), flush=True)
            if row["status"] == "ready":
                break
            if row["status"] == "error":
                raise SystemExit(row)
            time.sleep(2)
        else:
            raise SystemExit("モデルのダウンロードがタイムアウトしました")
    started = time.perf_counter()
    result = request(port, token, "POST", "/api/translate", {
        "text": "Hello.",
        "source_lang": "en",
        "target_lang": "ja",
        "stream": False,
    })
    elapsed = time.perf_counter() - started
    print(json.dumps({"text": result.get("text"), "seconds": round(elapsed, 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
