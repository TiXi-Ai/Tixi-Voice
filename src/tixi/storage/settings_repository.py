"""Settings persistence (SQLite-backed with JSON import/export)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..app.config import CONFIG_VERSION, Settings, SettingsStore
from ..app.logging_config import get_logger
from ..utils.atomic import atomic_write_json, read_json
from .database import Database

log = get_logger("tixi.storage.settings")


class SettingsRepository:
    """Stores every settings value in the ``settings`` table.

    A JSON mirror is written next to the database so users can inspect, back up
    or hand-edit their configuration — and so a corrupt database never locks the
    user out of their own preferences.
    """

    def __init__(self, database: Database, mirror: Path | None = None) -> None:
        self.db = database
        self.mirror = mirror

    # -- load ---------------------------------------------------------------
    def load(self) -> SettingsStore:
        payload: dict[str, Any] = {}
        try:
            rows = self.db.query("SELECT section, key, value FROM settings")
            for row in rows:
                section = row["section"]
                key = row["key"]
                value = _decode(row["value"])
                payload.setdefault(section, {})[key] = value
        except Exception:  # pragma: no cover - database may be unreadable
            log.exception("could not read settings from the database")

        if not payload and self.mirror and self.mirror.exists():
            payload = read_json(self.mirror, {}) or {}
            if isinstance(payload, dict):
                log.info("settings restored from JSON mirror", extra={"event": "settings_mirror_load"})

        settings = Settings.from_dict(payload)
        notes = settings.validate()
        store = SettingsStore(settings)
        if notes:
            log.info("settings repaired", extra={"event": "settings_repaired", "notes": "; ".join(notes)})
        return store

    # -- save ---------------------------------------------------------------
    def save(self, settings: Settings) -> None:
        rows: list[tuple[str, str, str, str]] = []
        now = _now()
        payload = settings.to_dict()
        payload["version"] = settings.version or CONFIG_VERSION
        for section, values in payload.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    rows.append((section, key, _encode(value), now))
            else:
                rows.append(("app", section, _encode(values), now))
        try:
            self.db.executemany_write(
                "INSERT INTO settings (section, key, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(section, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                rows,
            )
        except Exception:  # pragma: no cover
            log.exception("could not persist settings to the database")
        if self.mirror:
            try:
                atomic_write_json(self.mirror, payload)
            except OSError:
                log.warning("could not write the settings JSON mirror", exc_info=True)

    def reset(self) -> SettingsStore:
        self.db.execute_write("DELETE FROM settings")
        store = SettingsStore(Settings())
        self.save(store.settings)
        return store

    # -- import / export ----------------------------------------------------
    def export_to(self, path: Path) -> Path:
        payload = self.load().settings.to_dict()
        atomic_write_json(Path(path), payload)
        return Path(path)

    def import_from(self, path: Path) -> SettingsStore:
        payload = read_json(Path(path), None)
        if not isinstance(payload, dict):
            raise ValueError("The selected file is not a valid Tixi Voice settings export.")
        settings = Settings.from_dict(payload)
        settings.validate()
        store = SettingsStore(settings)
        self.save(settings)
        return store

    def usage(self) -> list[dict[str, Any]]:
        """Aggregate counters shown on the dashboard."""
        rows = self.db.query("SELECT key, value, updated_at FROM usage_stats ORDER BY key")
        return [dict(row) for row in rows]

    def bump_usage(self, key: str, amount: int = 1) -> None:
        self.db.execute_write(
            "INSERT INTO usage_stats (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value, updated_at = excluded.updated_at",
            (key, amount, _now()),
        )


def _encode(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _decode(raw: Any) -> Any:
    import json

    if raw is None:
        return None
    if isinstance(raw, (int, float, bool)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
