"""Download the pinned 0.8B GGUF if needed and record one CPU translation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "plamo-3-translate-0.8b-GGUF"


def request(port: int, token: str, method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    env = os.environ.copy()
    state = Path(env.get("LOCAL_TRANSLATE_STATE_DIR", Path.home() / ".local/state/local-translate-extension"))
    state.mkdir(parents=True, exist_ok=True)
    log = open(state / "daemon.log", "ab")
    subprocess.Popen(
        [sys.executable, "-m", "local_translate.daemon", "--port", "8765"],
        cwd=str(ROOT / "companion"),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + 20
    token = ""
    while time.time() < deadline:
        token_path = state / "token"
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
            try:
                request(8765, token, "GET", "/v1/status")
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.3)
    if not token:
        raise SystemExit("daemon が起動しませんでした")
    request(8765, token, "POST", f"/v1/models/{MODEL_ID}/consent", {"accepted": True})
    request(8765, token, "POST", "/v1/settings", {
        "gpu_backend": "cpu",
        "n_gpu_layers": 0,
        "context_size": 512,
        "parallel": 1,
        "active_translate_model": MODEL_ID,
    })
    models = request(8765, token, "GET", "/v1/models")["models"]
    ready = next(item for item in models if item["id"] == MODEL_ID)["status"] == "ready"
    if not ready:
        request(8765, token, "POST", f"/v1/models/{MODEL_ID}/download", {})
        deadline = time.time() + 1800
        while time.time() < deadline:
            row = next(item for item in request(8765, token, "GET", "/v1/models")["models"] if item["id"] == MODEL_ID)
            print(row["status"], row.get("progress"), flush=True)
            if row["status"] == "ready":
                break
            if row["status"] == "error":
                raise SystemExit(row)
            time.sleep(2)
        else:
            raise SystemExit("モデルのダウンロードがタイムアウトしました")
    started = time.perf_counter()
    result = request(8765, token, "POST", "/api/translate", {
        "text": "Hello.",
        "source_lang": "en",
        "target_lang": "ja",
        "stream": False,
    })
    elapsed = time.perf_counter() - started
    print(json.dumps({"text": result.get("text"), "seconds": round(elapsed, 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
