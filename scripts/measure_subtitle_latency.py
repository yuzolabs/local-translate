"""Stream prepared WAV clips to the daemon subtitle socket and record latency.

The committed summary must not contain transcripts. Pass --detail for the local full log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion" / "src"))
from local_translate.asr_verify import latency_stats

import aiohttp


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != 16000:
            raise SystemExit(f"{path} は 16 kHz 16-bit モノラル WAV である必要があります")
        return handle.readframes(handle.getnframes())


def daemon_endpoint(state: Path) -> tuple[int, str]:
    info = json.loads((state / "daemon.json").read_text(encoding="utf-8"))
    token = (state / "token").read_text(encoding="utf-8").strip()
    return int(info["port"]), token


async def measure_one(session: aiohttp.ClientSession, url: str, token: str, pcm: bytes) -> dict:
    speech_end = 0.0
    started = time.perf_counter()
    async with session.ws_connect(url) as socket:
        await socket.send_json({"type": "auth", "token": token})
        chunk = 16000 * 2 // 10
        silence = b"\x00\x00" * int(16000 * 0.8)

        async def send() -> None:
            nonlocal speech_end
            for offset in range(0, len(pcm), chunk):
                await socket.send_bytes(pcm[offset : offset + chunk])
                await asyncio.sleep(0.1)
            speech_end = time.perf_counter()
            for offset in range(0, len(silence), chunk):
                await socket.send_bytes(silence[offset : offset + chunk])
                await asyncio.sleep(0.1)

        sender = asyncio.create_task(send())
        final = None
        finished = None
        deadline = time.perf_counter() + 120 + len(pcm) / (16000 * 2)
        while time.perf_counter() < deadline:
            message = await asyncio.wait_for(socket.receive(), timeout=max(1, deadline - time.perf_counter()))
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                    break
                continue
            payload = json.loads(message.data)
            # 途中の無音で出る確定は、発話を送り終える前に届く。終端は送信完了のあと。
            if payload.get("final") and speech_end:
                final = payload
                finished = time.perf_counter()
                break
        await sender
        if final is None or finished is None:
            raise SystemExit("確定字幕が返りませんでした")
        return {
            "audio_sec": round(len(pcm) / (16000 * 2), 3),
            "latency_sec": round(finished - speech_end, 3),
            "wall_sec": round(finished - started, 3),
            "has_translation": bool(final.get("translation")),
            "text": final.get("text") or "",
            "translation": final.get("translation") or "",
        }


async def run(references: Path, state: Path) -> list[dict]:
    port, token = daemon_endpoint(state)
    url = f"http://127.0.0.1:{port}/api/subtitles"
    rows = [json.loads(line) for line in references.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    async with aiohttp.ClientSession() as session:
        for row in rows:
            measured = await measure_one(session, url, token, read_wav(Path(row["wav"])))
            measured["id"] = row["id"]
            results.append(measured)
            print(row["id"], measured["latency_sec"], flush=True)
    return results


def summary(results: list[dict], manifest: dict) -> dict:
    return {
        "dataset": manifest.get("dataset"),
        "license": manifest.get("license"),
        "count": len(results),
        "latency_sec": latency_stats([row["latency_sec"] for row in results]),
        "translated": sum(1 for row in results if row["has_translation"]),
        "note": "認識全文と訳文は含めない。レイテンシは発話の送信完了から、翻訳を含む確定字幕まで。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="字幕ソケットの確定レイテンシを測る")
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--detail", type=Path)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    state = args.state
    if state is None:
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
            state = base / "local-translate-extension"
        else:
            state = Path.home() / ".local" / "state" / "local-translate-extension"
    results = asyncio.run(run(args.references, state))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    payload = summary(results, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.detail:
        args.detail.parent.mkdir(parents=True, exist_ok=True)
        args.detail.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
