"""Small JSON helpers used by metrics and daemon info."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def replace_file_despite_open_readers(source: Path, destination: Path, attempts: int = 20, delay_sec: float = 0.02) -> None:
    """Replace destination with source, retrying while a reader still has it open.

    Windows raises PermissionError when another handle has the destination open.
    Linux replace succeeds on the first try, so the retry does not change that path.
    """
    last: PermissionError | None = None
    for attempt in range(attempts):
        try:
            source.replace(destination)
            return
        except PermissionError as exc:
            last = exc
            if attempt + 1 == attempts:
                raise
            time.sleep(delay_sec)
    if last is not None:
        raise last


def read_text_allowing_replace(path: Path) -> str:
    """Read a text file. On Windows, share it for delete so a writer can replace it."""
    if os.name != "nt":
        return path.read_text(encoding="utf-8")
    return _read_text_sharing_delete(path)


def _read_text_sharing_delete(path: Path) -> str:
    import ctypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    generic_read = 0x80000000
    share_read_write_delete = 0x1 | 0x2 | 0x4
    open_existing = 3
    file_attribute_normal = 0x80
    handle = kernel32.CreateFileW(
        str(path),
        generic_read,
        share_read_write_delete,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        error = ctypes.get_last_error()
        raise PermissionError(error, os.strerror(error), str(path))
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    with os.fdopen(descriptor, "r", encoding="utf-8") as opened:
        return opened.read()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    replace_file_despite_open_readers(temporary, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
