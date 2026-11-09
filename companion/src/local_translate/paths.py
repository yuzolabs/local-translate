"""State, data, and native-messaging paths. No user-specific paths are hardcoded."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from local_translate.constants import NM_HOST_NAME, PRODUCT_NAME


def resolve_state_dir(platform: str, environ: dict[str, str], home: Path) -> Path:
    override = environ.get("LOCAL_TRANSLATE_STATE_DIR")
    if override:
        return Path(override)
    if platform == "win32":
        base = Path(environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        return base / PRODUCT_NAME
    return home / ".local" / "state" / PRODUCT_NAME


def resolve_data_dir(platform: str, environ: dict[str, str], home: Path) -> Path:
    override = environ.get("LOCAL_TRANSLATE_DATA_DIR")
    if override:
        return Path(override)
    if platform == "win32":
        base = Path(environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        return base / PRODUCT_NAME / "data"
    return home / ".local" / "share" / PRODUCT_NAME


def chrome_native_messaging_dirs(platform: str, environ: dict[str, str], home: Path) -> list[Path]:
    if platform == "win32":
        return []
    config = Path(environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    return [
        config / "google-chrome" / "NativeMessagingHosts",
        config / "chromium" / "NativeMessagingHosts",
        config / "microsoft-edge" / "NativeMessagingHosts",
    ]


@dataclass(frozen=True)
class AppPaths:
    state_dir: Path
    data_dir: Path

    @classmethod
    def from_env(cls) -> AppPaths:
        return default_app_paths()

    @classmethod
    def create(cls, state_dir: Path, data_dir: Path) -> AppPaths:
        return cls(state_dir=state_dir, data_dir=data_dir)

    def ensure(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "models").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "runtimes").mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass

    @property
    def token_path(self) -> Path:
        return self.state_dir / "token"

    @property
    def daemon_lock_path(self) -> Path:
        return self.state_dir / "daemon.lock"

    @property
    def daemon_info_path(self) -> Path:
        return self.state_dir / "daemon.json"

    @property
    def settings_path(self) -> Path:
        return self.state_dir / "settings.json"

    @property
    def registry_path(self) -> Path:
        return self.state_dir / "models.json"

    @property
    def metrics_path(self) -> Path:
        return self.state_dir / "metrics.jsonl"

    @property
    def daemon_log_path(self) -> Path:
        return self.state_dir / "daemon.log"

    @property
    def runtime_digests_path(self) -> Path:
        return self.state_dir / "runtimes.sha256.json"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def runtimes_dir(self) -> Path:
        return self.data_dir / "runtimes"

    @property
    def extension_ids_path(self) -> Path:
        return self.state_dir / "allowed-extension-ids.json"


def default_app_paths() -> AppPaths:
    environ = dict(os.environ)
    home = Path.home()
    return AppPaths(
        state_dir=resolve_state_dir(sys.platform, environ, home),
        data_dir=resolve_data_dir(sys.platform, environ, home),
    )


def native_host_manifest(host_path: Path, extension_ids: list[str]) -> dict:
    return {
        "name": NM_HOST_NAME,
        "description": "Local Translate companion host",
        "path": str(host_path),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/" for extension_id in extension_ids],
    }
