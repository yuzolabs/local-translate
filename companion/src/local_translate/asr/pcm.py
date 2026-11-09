"""PCM helpers: resampling for speech serve's 24 kHz realtime socket."""

from __future__ import annotations

import array
import sys


def pcm16_samples(pcm: bytes) -> array.array:
    samples = array.array("h")
    usable = pcm[: len(pcm) - (len(pcm) % 2)]
    samples.frombytes(usable)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def samples_to_pcm16(samples: array.array) -> bytes:
    if sys.byteorder != "little":
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not pcm:
        return pcm
    source = pcm16_samples(pcm)
    if not source:
        return b""
    out_len = max(1, int(len(source) * dst_rate / src_rate))
    output = array.array("h")
    last = len(source) - 1
    for index in range(out_len):
        position = index * src_rate / dst_rate
        left = int(position)
        frac = position - left
        right = min(last, left + 1)
        value = source[left] + (source[right] - source[left]) * frac
        output.append(int(max(-32768, min(32767, round(value)))))
    return samples_to_pcm16(output)
