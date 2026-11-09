"""Chrome native messaging frames: 4-byte little-endian length plus UTF-8 JSON."""

from __future__ import annotations

import json
import struct
import sys
from typing import BinaryIO


def read_native_message(stream: BinaryIO) -> dict | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        raise ValueError("native message header was truncated")
    length = struct.unpack("<I", header)[0]
    if length > 1_000_000:
        raise ValueError("native message exceeds 1 MB")
    payload = stream.read(length)
    if len(payload) < length:
        raise ValueError("native message body was truncated")
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("native message must be a JSON object")
    return message


def write_native_message(stream: BinaryIO, message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > 1_000_000:
        raise ValueError("native message exceeds 1 MB")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()


def read_stdin_message() -> dict | None:
    return read_native_message(sys.stdin.buffer)


def write_stdout_message(message: dict) -> None:
    write_native_message(sys.stdout.buffer, message)
