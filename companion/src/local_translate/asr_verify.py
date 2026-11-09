"""Compare Haas quantizations and write a CER report. The bench subset is optional."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from pathlib import Path

from local_translate.asr.cer import character_error_rate
from local_translate.constants import WINDOWS_HAAS_DECISION, WINDOWS_HAAS_DECISION_REASON

NEMO_CER_STRICT = 0.0612


def read_wav_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise SystemExit(f"{path} は 16-bit モノラル WAV である必要があります")
        return handle.readframes(handle.getnframes()), handle.getframerate()


async def run_asr(speech_bin: str, model: str, wav_path: Path) -> tuple[str, float]:
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        speech_bin,
        "asr",
        model,
        "--vad",
        "silero-vad",
        str(wav_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    elapsed = time.perf_counter() - started
    if process.returncode != 0:
        raise SystemExit(stderr.decode("utf-8", errors="replace"))
    return stdout.decode("utf-8", errors="replace").strip(), elapsed


def load_references(path: Path) -> list[dict[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        rows.append({"wav": payload["wav"], "text": payload["text"]})
    return rows


async def verify(speech_bin: str, samples: Path | None, references: Path | None, output: Path) -> dict:
    report: dict = {
        "nemo_cer_strict": NEMO_CER_STRICT,
        "windows_haas": {"decision": WINDOWS_HAAS_DECISION, "reason": WINDOWS_HAAS_DECISION_REASON},
        "samples": [],
        "cer": None,
        "note": "asr-ja-bench 全体は同梱しない。references JSONL を渡した部分集合だけ CER を計算する。",
    }
    if samples and samples.is_dir():
        for wav_path in sorted(samples.glob("*.wav")):
            row = {"wav": wav_path.name, "quants": {}}
            for model in ("haas-ja-v1.0", "haas-ja-v1.0:f16"):
                text, elapsed = await run_asr(speech_bin, model, wav_path)
                pcm, rate = read_wav_pcm(wav_path)
                seconds = len(pcm) / 2 / rate
                row["quants"][model] = {"text": text, "latency_sec": round(elapsed, 3), "audio_sec": round(seconds, 3)}
            report["samples"].append(row)
    if references:
        errors = []
        weights = 0
        for item in load_references(references):
            hypothesis, elapsed = await run_asr(speech_bin, "haas-ja-v1.0", Path(item["wav"]))
            cer = character_error_rate(item["text"], hypothesis)
            errors.append(cer * len(item["text"]))
            weights += len(item["text"])
            report["samples"].append(
                {
                    "wav": item["wav"],
                    "reference": item["text"],
                    "hypothesis": hypothesis,
                    "cer": cer,
                    "latency_sec": round(elapsed, 3),
                }
            )
        if weights:
            measured = errors / weights
            report["cer"] = {
                "measured": measured,
                "nemo_reference": NEMO_CER_STRICT,
                "delta": measured - NEMO_CER_STRICT,
                "acceptable": measured <= NEMO_CER_STRICT + 0.03,
                "rule": "部分集合 CER が公式 NeMo strict 6.12% から 3 ポイント以内なら許容。超えたら NeMo 経路は使わず、差を記録してモデル更新を待つ。",
            }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Haas-ja speech.cpp の疎通と CER を記録する")
    parser.add_argument("--speech", required=True)
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(verify(args.speech, args.samples, args.references, args.output))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
