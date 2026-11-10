"""Prepare a 30-clip FLEURS subset with asr-ja-bench transcript patches.

Audio stays outside git. The references file is for local CER and must not be committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion" / "src"))
from local_translate.asr.transcript_patch import apply_codepoint_patch

FLEURS_TSV_URL = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/ja_jp/test.tsv"
FLEURS_TAR_URL = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/ja_jp/audio/test.tar.gz"
BENCH_URL = "https://huggingface.co/datasets/third-intelligence/asr-ja-bench/resolve/main/data/fleurs/samples.jsonl"
DATASET = "FLEURS ja_jp test, asr-ja-bench v1.1 fleurs"
LICENSE = "CC BY 4.0"


def download(url: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size > 0:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as handle:
        while True:
            block = response.read(1024 * 256)
            if not block:
                break
            handle.write(block)
    temporary.replace(destination)


def select_rows(rows: list[dict], count: int) -> list[dict]:
    """Keep every patched row, then fill from the benchmark order."""
    patched = [row for row in rows if row.get("patch")]
    plain = [row for row in rows if not row.get("patch")]
    return (patched + plain)[:count]


def transcript_for(row: dict, by_id: dict[str, str]) -> str:
    text = by_id[row["id"]]
    expected = row["upstream_sha256"]
    actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if actual != expected:
        raise SystemExit(f"{row['id']} の正解文ハッシュが asr-ja-bench と一致しません")
    patched = apply_codepoint_patch(text, row.get("patch"))
    patched_hash = hashlib.sha256(patched.encode("utf-8")).hexdigest()
    if patched_hash != row["text_sha256"]:
        raise SystemExit(f"{row['id']} の修正後ハッシュが asr-ja-bench と一致しません")
    return patched


def convert_wav(source: Path, destination: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg が PATH にありません")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(destination)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def load_tsv(path: Path) -> dict[str, str]:
    by_id = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        columns = line.split("\t")
        if len(columns) < 4:
            continue
        file_id = Path(columns[1]).stem
        by_id[file_id] = columns[3]
    return by_id


def main() -> None:
    parser = argparse.ArgumentParser(description="FLEURS ja_jp test から asr-ja-bench 修正済みの部分集合を作る")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    tsv = args.cache / "test.tsv"
    bench = args.cache / "samples.jsonl"
    archive = args.cache / "test.tar.gz"
    download(FLEURS_TSV_URL, tsv)
    download(BENCH_URL, bench)
    download(FLEURS_TAR_URL, archive)
    rows = [json.loads(line) for line in bench.read_text(encoding="utf-8").splitlines() if line.strip()]
    chosen = select_rows(rows, args.count)
    texts = load_tsv(tsv)
    args.output.mkdir(parents=True, exist_ok=True)
    references = []
    with tarfile.open(archive) as package:
        for row in chosen:
            member = f"test/{row['id']}.wav"
            extracted = args.cache / "raw" / f"{row['id']}.wav"
            extracted.parent.mkdir(parents=True, exist_ok=True)
            source = package.extractfile(member)
            if source is None:
                raise SystemExit(f"{member} がアーカイブにありません")
            extracted.write_bytes(source.read())
            wav_path = args.output / f"{row['id']}.wav"
            convert_wav(extracted, wav_path)
            references.append({"id": row["id"], "wav": str(wav_path), "text": transcript_for(row, texts)})
    (args.output / "references.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in references) + "\n",
        encoding="utf-8",
    )
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "dataset": DATASET,
                "license": LICENSE,
                "count": len(references),
                "selection": "修正パッチがあるサンプルを先に取り、残りは asr-ja-bench の順で埋める",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(references)} clips to {args.output}")


if __name__ == "__main__":
    main()
