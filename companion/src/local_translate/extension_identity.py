"""Extension id shared with the unpacked manifest key."""

from __future__ import annotations

import json
from pathlib import Path

BUNDLED_EXTENSION_IDENTITY = {
    "hostName": "com.localtranslate.host",
    "extensionId": "jiobdbkfdjchlpmfmgkkmbgmkdmehjfe",
    "manifestKey": (
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAq5sjffKr4FduQXbZiJeHn7jd/BDU8xel"
        "Mkg9lrF+76ypMHRr/WZQJ0KzbndpHTgxTjz8reFutzYuYas0fmoSKJ/ojiDf+IbtUagzhWs3r1hgopjn"
        "YbNQxMy2X5iEEfyrnyo3D4aUSGFGFS6a0Bh9TTPx7cJ2s6OpkY0XksqOirCPnegKp4JwAJr3QVHu9VT0"
        "ms94TSbLQa7tAbD423nOUlZsvdOvi3cGkttsO24wTJm9HrPOXEwkWtY3vYL8APGdGHMQdRZYNRPs+DFk"
        "GkuDykM1iNU45v96+3hI1SEqGA2W8Ru/DfBU3nfGmsxJrtcY2xcmVCe4M5PZ2bWeR+r8wwIDAQAB"
    ),
}


def _identity_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parents[3] / "shared" / "extension-identity.json",
        here.parents[2] / "shared" / "extension-identity.json",
    ]


def load_extension_identity(path: Path | None = None) -> dict[str, str]:
    candidates = [path] if path else _identity_candidates()
    for candidate in candidates:
        if candidate and candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return {
                "hostName": str(data["hostName"]),
                "extensionId": str(data["extensionId"]),
                "manifestKey": str(data["manifestKey"]),
            }
    return dict(BUNDLED_EXTENSION_IDENTITY)


def bundled_extension_id() -> str:
    return load_extension_identity()["extensionId"]
