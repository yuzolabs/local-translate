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
    from local_translate.process_spawn import create_hidden_subprocess

    process = await create_hidden_subprocess(
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
            measured = sum(errors) / weights
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


def latency_stats(values: list[float]) -> dict[str, float | int] | None:
    """Mean and percentiles for a latency list. Empty input has no stats."""
    if not values:
        return None
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
        return round(ordered[index], 3)

    return {
        "count": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 3),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
    }


def subtitle_final_latency_sec(
    send_started: float,
    speech_sent: float,
    finals: list[tuple[float, float | None]],
) -> float | None:
    """Seconds from the utterance end to a translated final caption.

    A final received after the wav has been sent was closed by the added silence.
    If the wav already contained that silence, the utterance end is the caption's
    end_ms on the realtime send clock.
    """
    if not finals:
        return None
    after_speech = [item for item in finals if item[0] >= speech_sent]
    if after_speech:
        return after_speech[0][0] - speech_sent
    received_at, end_ms = finals[-1]
    if end_ms and end_ms > 0:
        return received_at - (send_started + end_ms / 1000)
    return received_at - speech_sent


def summarize_asr_report(report: dict) -> dict:
    """Aggregate CER and latency. The committed report must not contain transcripts."""
    samples = report.get("samples") or []
    latencies = [float(row["latency_sec"]) for row in samples if isinstance(row, dict) and "latency_sec" in row]
    summary = {
        "dataset": report.get("dataset"),
        "license": report.get("license"),
        "count": len(samples),
        "cer": report.get("cer"),
        "latency_sec": latency_stats(latencies),
        "note": "認識全文と正解文は含めない。Haas-ja の公表 CER は jacer 正規化後の値であり、この cer.py とは直接比べられない。",
    }
    return {key: value for key, value in summary.items() if value is not None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Haas-ja speech.cpp の疎通と CER を記録する")
    parser.add_argument("--speech", required=True)
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--detail", type=Path)
    parser.add_argument("--dataset", default="")
    parser.add_argument("--license", default="")
    args = parser.parse_args()
    report = asyncio.run(verify(args.speech, args.samples, args.references, args.detail or args.output))
    if args.dataset:
        report["dataset"] = args.dataset
    if args.license:
        report["license"] = args.license
    payload = summarize_asr_report(report) if args.summary_only else report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
