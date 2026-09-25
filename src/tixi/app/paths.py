"""Filesystem layout for Tixi Voice.

Everything that the user creates (settings, history, models, generated audio,
logs) lives *outside* the installation directory so that upgrading or
uninstalling the application never destroys user data.  The only exception is
"portable mode", which is explicitly opted into by dropping a ``portable.flag``
file next to the executable.

Layout (Windows, default):

    %APPDATA%\\TixiVoice\\settings.json      user settings / window state
    %LOCALAPPDATA%\\TixiVoice\\tixi.db       history + metadata (SQLite)
    %LOCALAPPDATA%\\TixiVoice\\models\\      downloaded AI models
    %LOCALAPPDATA%\\TixiVoice\\engine_packs\\  on-demand Python AI runtimes
    %LOCALAPPDATA%\\TixiVoice\\logs\\        rotating diagnostic logs
    %LOCALAPPDATA%\\TixiVoice\\cache\\       waveforms, temp renders
    %USERPROFILE%\\Music\\Tixi Voice\\       default audio export folder
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

APP_NAME: Final = "Tixi Voice"
APP_SLUG: Final = "TixiVoice"
APP_ID: Final = "tixi-voice"
ORG_NAME: Final = "TiXi-Ai"

IS_WINDOWS: Final = os.name == "nt"
IS_FROZEN: Final = bool(getattr(sys, "frozen", False))

_PORTABLE_FLAG: Final = "portable.flag"


def app_root() -> Path:
    """Directory that contains the application code / frozen executable."""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def asset_dir(*parts: str) -> Path:
    """Resolve a path inside the bundled ``assets`` folder."""
    candidates = [
        app_root() / "assets",
        Path(__file__).resolve().parents[3] / "assets",
        Path(getattr(sys, "_MEIPASS", app_root())) / "assets",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.joinpath(*parts)
    return candidates[0].joinpath(*parts)


def _windows_local_appdata() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if value:
        return Path(value) / APP_SLUG
    return Path.home() / "AppData" / "Local" / APP_SLUG


def _windows_roaming_appdata() -> Path:
    value = os.environ.get("APPDATA")
    if value:
        return Path(value) / APP_SLUG
    return Path.home() / "AppData" / "Roaming" / APP_SLUG


def is_portable() -> bool:
    """Portable mode keeps every user file inside the application folder."""
    flag = app_root() / _PORTABLE_FLAG
    if flag.exists():
        return True
    return os.environ.get("TIXI_PORTABLE", "").strip() in {"1", "true", "yes"}


@dataclass(frozen=True)
class AppPaths:
    """Resolved, absolute locations used by the whole application."""

    root: Path          # local (machine-specific) data
    config_root: Path   # roaming data (settings)
    models: Path
    engine_packs: Path
    logs: Path
    cache: Path
    exports: Path
    recordings: Path
    fonts: Path
    portable: bool

    @property
    def settings_file(self) -> Path:
        return self.config_root / "settings.json"

    @property
    def database_file(self) -> Path:
        return self.root / "tixi.db"

    @property
    def log_file(self) -> Path:
        return self.logs / "tixi-voice.log"

    @property
    def crash_file(self) -> Path:
        return self.logs / "crashes.log"

    @property
    def lock_file(self) -> Path:
        return self.root / "tixi.lock"

    def ensure(self) -> AppPaths:
        """Create every directory (idempotent) and return ``self``."""
        for directory in (
            self.root,
            self.config_root,
            self.models,
            self.engine_packs,
            self.logs,
            self.cache,
            self.exports,
            self.recordings,
        ):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError:  # pragma: no cover - depends on the host filesystem
                # Never crash at import/startup because of a read-only location:
                # fall back to a writable temp directory for that one folder.
                fallback = Path(os.environ.get("TEMP", "/tmp")) / APP_SLUG / directory.name
                fallback.mkdir(parents=True, exist_ok=True)
        return self


def _default_export_dir(portable_root: Path | None) -> Path:
    override = os.environ.get("TIXI_EXPORT_DIR")
    if override:
        return Path(override).expanduser()
    if portable_root is not None:
        return portable_root / "exports"
    if IS_WINDOWS:
        music = os.environ.get("USERPROFILE")
        base = Path(music) / "Music" if music else Path.home() / "Music"
        return base / "Tixi Voice"
    return Path.home() / "Music" / "Tixi Voice"


def resolve_paths() -> AppPaths:
    """Compute the application paths for the current machine."""
    if is_portable():
        root = app_root() / "data"
        paths = AppPaths(
            root=root,
            config_root=root,
            models=root / "models",
            engine_packs=root / "engine_packs",
            logs=root / "logs",
            cache=root / "cache",
            exports=_default_export_dir(root),
            recordings=root / "recordings",
            fonts=asset_dir("fonts"),
            portable=True,
        )
        return paths.ensure()

    if IS_WINDOWS:
        root = _windows_local_appdata()
        config_root = _windows_roaming_appdata()
    else:  # development on Linux / macOS
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        root = base / APP_ID
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID

    paths = AppPaths(
        root=root,
        config_root=config_root,
        models=root / "models",
        engine_packs=root / "engine_packs",
        logs=root / "logs",
        cache=root / "cache",
        exports=_default_export_dir(None),
        recordings=root / "recordings",
        fonts=asset_dir("fonts"),
        portable=False,
    )
    return paths.ensure()


_PATHS: AppPaths | None = None


def paths() -> AppPaths:
    """Return the process-wide :class:`AppPaths` (lazily resolved)."""
    global _PATHS
    if _PATHS is None:
        _PATHS = resolve_paths()
    return _PATHS


def reset_paths_for_tests(custom: AppPaths | None = None) -> None:
    """Testing hook: forget (or override) the cached paths."""
    global _PATHS
    _PATHS = custom
