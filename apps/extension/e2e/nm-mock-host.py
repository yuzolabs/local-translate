#!/usr/bin/env python3
"""Native-messaging host shim for the E2E tests. Only answers the handshake."""
import json
import os
import struct
import sys


def read_message(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream, message):
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()


def main():
    handshake = {
        "port": int(os.environ.get("LT_MOCK_PORT", "9639")),
        "token": os.environ.get("LT_MOCK_TOKEN", "mock-token"),
        "version": "0.0.0-mock",
        "capabilities": ["mock"],
    }
    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return
        write_message(sys.stdout.buffer, handshake)


if __name__ == "__main__":
    main()
