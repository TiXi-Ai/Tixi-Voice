"""Audio library, transcription, diacritization and dictionary repositories."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..app.logging_config import get_logger
from .database import Database

log = get_logger("tixi.storage.library")


@dataclass
class AudioAsset:
    """A generated (or imported) audio file tracked by the library."""

    id: int = 0
    history_id: int | None = None
    path: str = ""
    file_name: str = ""
    format: str = ""
    sample_rate: int = 0
    channels: int = 0
    bit_depth: int = 0
    duration_ms: int = 0
    size_bytes: int = 0
    source_text: str = ""
    voice_id: str = ""
    model_id: str = ""
    language: str = ""
    favourite: bool = False
    missing: bool = False
    created_at: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return Path(self.path).exists()

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0 if self.duration_ms else 0.0

    @classmethod
    def from_row(cls, row: Any) -> AudioAsset:
        tags: list[str] = []
        raw_tags = row["tags"] if "tags" in row.keys() else None
        if raw_tags:
            try:
                parsed = json.loads(raw_tags)
                if isinstance(parsed, list):
                    tags = [str(item) for item in parsed]
            except (TypeError, ValueError):
                tags = []
        return cls(
            id=int(row["id"]),
            history_id=row["history_id"],
            path=row["path"] or "",
            file_name=row["file_name"] or "",
            format=row["format"] or "",
            sample_rate=int(row["sample_rate"] or 0),
            channels=int(row["channels"] or 0),
            bit_depth=int(row["bit_depth"] or 0),
            duration_ms=int(row["duration_ms"] or 0),
            size_bytes=int(row["size_bytes"] or 0),
            source_text=row["source_text"] or "",
            voice_id=row["voice_id"] or "",
            model_id=row["model_id"] or "",
            language=row["language"] or "",
            favourite=bool(row["favourite"]),
            missing=bool(row["missing"]),
            created_at=row["created_at"] or "",
            tags=tags,
        )


class AudioLibraryRepository:
    """Metadata for everything in the Audio Library view."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def add(
        self,
        path: Path | str,
        *,
        history_id: int | None = None,
        source_text: str = "",
        voice_id: str = "",
        model_id: str = "",
        language: str = "",
        duration_ms: int = 0,
        sample_rate: int = 0,
        channels: int = 0,
        bit_depth: int = 0,
        tags: list[str] | None = None,
    ) -> int:
        file_path = Path(path)
        size = file_path.stat().st_size if file_path.exists() else 0
        fmt = file_path.suffix.lstrip(".").lower()
        return int(
            self.db.execute_write(
                """
                INSERT INTO audio_assets
                    (history_id, path, file_name, format, sample_rate, channels, bit_depth,
                     duration_ms, size_bytes, source_text, voice_id, model_id, language,
                     favourite, missing, created_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    history_id,
                    str(file_path),
                    file_path.name,
                    fmt,
                    int(sample_rate),
                    int(channels),
                    int(bit_depth),
                    int(duration_ms),
                    size,
                    source_text,
                    voice_id,
                    model_id,
                    language,
                    _now(),
                    json.dumps(tags or [], ensure_ascii=False),
                ),
            )
        )

    def get(self, asset_id: int) -> AudioAsset | None:
        row = self.db.query_one("SELECT * FROM audio_assets WHERE id=?", (asset_id,))
        return AudioAsset.from_row(row) if row else None

    def list(
        self,
        *,
        search: str = "",
        format_filter: str = "",
        date_from: str = "",
        date_to: str = "",
        favourites_only: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[AudioAsset]:
        clauses = ["1=1"]
        params: list[Any] = []
        if search:
            clauses.append("(file_name LIKE ? OR source_text LIKE ?)")
            needle = f"%{search}%"
            params.extend([needle, needle])
        if format_filter:
            clauses.append("format=?")
            params.append(format_filter.lower())
        if date_from:
            clauses.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("created_at <= ?")
            params.append(date_to + " 23:59:59")
        if favourites_only:
            clauses.append("favourite=1")
        params.extend([int(limit), int(offset)])
        rows = self.db.query(
            f"SELECT * FROM audio_assets WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        assets = [AudioAsset.from_row(row) for row in rows]
        # Cheap integrity check: flag entries whose file disappeared.
        changed: list[tuple[int, int]] = []
        for asset in assets:
            missing = 1 if not Path(asset.path).exists() else 0
            if missing != int(asset.missing):
                asset.missing = bool(missing)
                changed.append((missing, asset.id))
        if changed:
            self.db.executemany_write("UPDATE audio_assets SET missing=? WHERE id=?", changed)
        return assets

    def formats(self) -> list[str]:
        rows = self.db.query(
            "SELECT DISTINCT format FROM audio_assets WHERE format IS NOT NULL AND format<>'' ORDER BY format"
        )
        return [row["format"] for row in rows]

    def total_size(self) -> int:
        return int(self.db.scalar("SELECT COALESCE(SUM(size_bytes),0) FROM audio_assets", default=0) or 0)

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM audio_assets", default=0) or 0)

    def rename(self, asset_id: int, new_name: str) -> Path | None:
        """Rename the file on disk (never overwriting) and update metadata."""
        asset = self.get(asset_id)
        if asset is None:
            return None
        source = Path(asset.path)
        if not source.exists():
            raise FileNotFoundError(f"the audio file is missing: {source}")
        safe_name = _sanitise_filename(new_name)
        if not Path(safe_name).suffix:
            safe_name += source.suffix
        target = source.with_name(safe_name)
        if target.exists() and target != source:
            from ..utils.atomic import unique_path

            target = unique_path(target)
        source.rename(target)
        self.db.execute_write(
            "UPDATE audio_assets SET path=?, file_name=? WHERE id=?",
            (str(target), target.name, asset_id),
        )
        self.db.execute_write(
            "UPDATE history SET audio_path=? WHERE audio_path=?", (str(target), str(source))
        )
        return target

    def set_favourite(self, asset_id: int, favourite: bool) -> None:
        self.db.execute_write(
            "UPDATE audio_assets SET favourite=? WHERE id=?", (1 if favourite else 0, asset_id)
        )

    def set_tags(self, asset_id: int, tags: Iterable[str]) -> None:
        self.db.execute_write(
            "UPDATE audio_assets SET tags=? WHERE id=?",
            (json.dumps(list(tags), ensure_ascii=False), asset_id),
        )

    def delete(self, asset_id: int, *, remove_file: bool = False) -> bool:
        asset = self.get(asset_id)
        if asset is None:
            return False
        if remove_file:
            try:
                Path(asset.path).unlink(missing_ok=True)
            except OSError:
                log.warning("could not delete audio file", extra={"event": "delete_failed", "path": asset.path})
        self.db.execute_write("DELETE FROM audio_assets WHERE id=?", (asset_id,))
        return True

    def orphaned(self) -> list[AudioAsset]:
        rows = self.db.query(
            "SELECT * FROM audio_assets WHERE missing=0", ()
        )
        return [asset for asset in (AudioAsset.from_row(row) for row in rows) if not asset.exists]


class TranscriptionRepository:
    """Transcript segments, timestamps and metadata."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def add(
        self,
        *,
        text: str,
        segments: list[dict[str, Any]] | None = None,
        words: list[dict[str, Any]] | None = None,
        language: str = "",
        language_probability: float = 0.0,
        model_id: str = "",
        duration_ms: int = 0,
        source_path: str = "",
        source_name: str = "",
        speaker_count: int = 0,
        history_id: int | None = None,
    ) -> int:
        return int(
            self.db.execute_write(
                """
                INSERT INTO transcriptions
                    (history_id, source_path, source_name, text, language, language_probability,
                     model_id, duration_ms, segments, words, speaker_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    source_path,
                    source_name or Path(source_path).name if source_path else source_name,
                    text,
                    language,
                    float(language_probability),
                    model_id,
                    int(duration_ms),
                    json.dumps(segments or [], ensure_ascii=False),
                    json.dumps(words or [], ensure_ascii=False),
                    int(speaker_count),
                    _now(),
                ),
            )
        )

    def get(self, transcription_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM transcriptions WHERE id=?", (transcription_id,))
        return _decode_transcription(row) if row else None

    def list(self, *, search: str = "", limit: int = 200) -> list[dict[str, Any]]:
        params: list[Any] = []
        clause = ""
        if search:
            clause = "WHERE text LIKE ? OR source_name LIKE ?"
            needle = f"%{search}%"
            params.extend([needle, needle])
        params.append(int(limit))
        rows = self.db.query(
            f"SELECT * FROM transcriptions {clause} ORDER BY created_at DESC LIMIT ?", params
        )
        return [_decode_transcription(row) for row in rows]

    def update_text(self, transcription_id: int, text: str, segments: list[dict[str, Any]] | None = None) -> None:
        if segments is None:
            self.db.execute_write("UPDATE transcriptions SET text=? WHERE id=?", (text, transcription_id))
        else:
            self.db.execute_write(
                "UPDATE transcriptions SET text=?, segments=? WHERE id=?",
                (text, json.dumps(segments, ensure_ascii=False), transcription_id),
            )

    def delete(self, transcription_id: int) -> None:
        self.db.execute_write("DELETE FROM transcriptions WHERE id=?", (transcription_id,))

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM transcriptions", default=0) or 0)

    def total_duration_ms(self) -> int:
        return int(self.db.scalar("SELECT COALESCE(SUM(duration_ms),0) FROM transcriptions", default=0) or 0)


class DiacritizationRepository:
    """History of Persian diacritization runs (useful for manual correction)."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def add(
        self,
        *,
        source_text: str,
        output_text: str,
        mode: str,
        engine_id: str,
        confidence: float = 0.0,
        history_id: int | None = None,
    ) -> int:
        return int(
            self.db.execute_write(
                """
                INSERT INTO diacritizations
                    (history_id, source_text, output_text, mode, engine_id, char_count,
                     confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    source_text,
                    output_text,
                    mode,
                    engine_id,
                    len(output_text),
                    float(confidence),
                    _now(),
                ),
            )
        )

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM diacritizations ORDER BY created_at DESC LIMIT ?", (int(limit),)
        )
        return [dict(row) for row in rows]

    def overwrite_output(self, row_id: int, output_text: str) -> None:
        """Persist a user's manual correction so it can be reused."""
        self.db.execute_write(
            "UPDATE diacritizations SET output_text=? WHERE id=?", (output_text, row_id)
        )


class DictionaryRepository:
    """Custom pronunciation / replacement dictionary entries."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def add(
        self,
        word: str,
        replacement: str,
        *,
        language: str = "fa",
        case_sensitive: bool = False,
        priority: int = 0,
        note: str = "",
    ) -> int:
        return int(
            self.db.execute_write(
                """
                INSERT INTO dictionary_entries
                    (word, replacement, language, case_sensitive, priority, enabled, note, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(word, language) DO UPDATE SET
                    replacement=excluded.replacement, priority=excluded.priority,
                    enabled=1, note=excluded.note
                """,
                (word, replacement, language, 1 if case_sensitive else 0, priority, note, _now()),
            )
        )

    def list(self, language: str | None = None, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if language:
            clauses.append("(language=? OR language IS NULL)")
            params.append(language)
        if enabled_only:
            clauses.append("enabled=1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM dictionary_entries {where} ORDER BY priority DESC, length(word) DESC",
            params,
        )
        return [dict(row) for row in rows]

    def delete(self, entry_id: int) -> None:
        self.db.execute_write("DELETE FROM dictionary_entries WHERE id=?", (entry_id,))

    def set_enabled(self, entry_id: int, enabled: bool) -> None:
        self.db.execute_write(
            "UPDATE dictionary_entries SET enabled=? WHERE id=?", (1 if enabled else 0, entry_id)
        )

    def as_pairs(self, language: str | None = None) -> list[tuple[str, str, bool]]:
        return [
            (row["word"], row["replacement"], bool(row["case_sensitive"]))
            for row in self.list(language)
        ]

    # -- pronunciation overrides -------------------------------------------
    def set_pronunciation(self, word: str, *, ipa: str = "", diacritized: str = "", note: str = "") -> None:
        self.db.execute_write(
            """
            INSERT INTO pronunciation_overrides (word, ipa, diacritized, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(word) DO UPDATE SET ipa=excluded.ipa,
                diacritized=excluded.diacritized, note=excluded.note
            """,
            (word, ipa, diacritized, note, _now()),
        )

    def pronunciations(self) -> dict[str, dict[str, str]]:
        rows = self.db.query("SELECT word, ipa, diacritized, note FROM pronunciation_overrides")
        return {
            row["word"]: {"ipa": row["ipa"] or "", "diacritized": row["diacritized"] or "", "note": row["note"] or ""}
            for row in rows
        }


def _decode_transcription(row: Any) -> dict[str, Any]:
    payload = dict(row)
    for key in ("segments", "words"):
        raw = payload.get(key)
        try:
            payload[key] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            payload[key] = []
    return payload


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _sanitise_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name.strip() if ch not in '<>:"/\\|?*\a\b\f\n\r\t\v')
    cleaned = cleaned.strip(" .")
    return cleaned or f"audio-{int(time.time())}"
