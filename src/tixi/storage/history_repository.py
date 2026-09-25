"""History persistence: synthesis, transcription, voice typing and processing."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

from ..app.logging_config import get_logger
from .database import Database

log = get_logger("tixi.storage.history")

HistoryKind = Literal["tts", "stt", "voice_typing", "diacritization", "normalization"]

KIND_LABELS: dict[str, str] = {
    "tts": "Text to speech",
    "stt": "Transcription",
    "voice_typing": "Voice typing",
    "diacritization": "Diacritization",
    "normalization": "Normalization",
}


@dataclass
class HistoryEntry:
    """One logged operation (synthesis, transcription, dictation ...)."""

    id: int = 0
    kind: str = "tts"
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    language: str = ""
    model_id: str = ""
    text: str = ""
    audio_path: str = ""
    duration_ms: int = 0
    char_count: int = 0
    word_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    pinned: bool = False

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def created_dt(self) -> datetime | None:
        return _parse_dt(self.created_at)

    @property
    def preview(self) -> str:
        text = " ".join((self.text or "").split())
        return text[:160] + ("…" if len(text) > 160 else "")

    @classmethod
    def from_row(cls, row: Any) -> HistoryEntry:
        meta = row["meta"]
        try:
            meta_dict = json.loads(meta) if meta else {}
        except (TypeError, ValueError):
            meta_dict = {}
        return cls(
            id=int(row["id"]),
            kind=row["kind"] or "tts",
            title=row["title"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            language=row["language"] or "",
            model_id=row["model_id"] or "",
            text=row["text"] or "",
            audio_path=row["audio_path"] or "",
            duration_ms=int(row["duration_ms"] or 0),
            char_count=int(row["char_count"] or 0),
            word_count=int(row["word_count"] or 0),
            meta=meta_dict,
            pinned=bool(row["pinned"]),
        )


class HistoryRepository:
    """CRUD + search over the ``history`` table."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # -- writes -------------------------------------------------------------
    def add(
        self,
        kind: str,
        *,
        title: str = "",
        text: str = "",
        language: str = "",
        model_id: str = "",
        audio_path: str | Path | None = None,
        duration_ms: int = 0,
        char_count: int | None = None,
        word_count: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        now = _now()
        words = word_count if word_count is not None else _count_words(text)
        row_id = self.db.execute_write(
            """
            INSERT INTO history
                (kind, title, created_at, updated_at, language, model_id, text,
                 audio_path, duration_ms, char_count, word_count, meta, pinned, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (
                kind,
                title or _default_title(kind, text),
                now,
                now,
                language,
                model_id,
                text,
                str(audio_path) if audio_path else "",
                int(duration_ms),
                len(text) if char_count is None else char_count,
                words,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        return int(row_id)

    def update(self, entry_id: int, **values: Any) -> bool:
        allowed = {
            "title",
            "text",
            "language",
            "model_id",
            "audio_path",
            "duration_ms",
            "meta",
            "pinned",
        }
        patches = {key: value for key, value in values.items() if key in allowed}
        if not patches:
            return False
        if "meta" in patches and not isinstance(patches["meta"], str):
            patches["meta"] = json.dumps(patches["meta"], ensure_ascii=False)
        if "pinned" in patches:
            patches["pinned"] = 1 if patches["pinned"] else 0
        patches["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in patches)
        self.db.execute_write(
            f"UPDATE history SET {assignments} WHERE id=?",
            [*patches.values(), entry_id],
        )
        return True

    def set_pinned(self, entry_id: int, pinned: bool) -> None:
        self.db.execute_write(
            "UPDATE history SET pinned=?, pinned_at=?, updated_at=? WHERE id=?",
            (1 if pinned else 0, _now() if pinned else None, _now(), entry_id),
        )

    def delete(self, entry_id: int, *, hard: bool = False) -> None:
        if hard:
            self.db.execute_write("DELETE FROM history WHERE id=?", (entry_id,))
        else:
            self.db.execute_write(
                "UPDATE history SET deleted=1, updated_at=? WHERE id=?", (_now(), entry_id)
            )

    def delete_many(self, ids: Iterable[int], *, hard: bool = False) -> int:
        id_list = list(ids)
        if not id_list:
            return 0
        placeholders = ",".join("?" for _ in id_list)
        if hard:
            sql = f"DELETE FROM history WHERE id IN ({placeholders})"
        else:
            sql = f"UPDATE history SET deleted=1, updated_at=? WHERE id IN ({placeholders})"
            id_list.insert(0, _now())
        self.db.execute_write(sql, id_list)
        return len(id_list)

    def clear(self, kind: str | None = None) -> int:
        if kind:
            rows = self.db.query("SELECT COUNT(*) AS c FROM history WHERE kind=?", (kind,))
        else:
            rows = self.db.query("SELECT COUNT(*) AS c FROM history")
        count = int(rows[0]["c"]) if rows else 0
        if kind:
            self.db.execute_write("DELETE FROM history WHERE kind=?", (kind,))
        else:
            self.db.execute_write("DELETE FROM history")
        return count

    # -- reads --------------------------------------------------------------
    def get(self, entry_id: int) -> HistoryEntry | None:
        row = self.db.query_one("SELECT * FROM history WHERE id=?", (entry_id,))
        return HistoryEntry.from_row(row) if row else None

    def list(
        self,
        *,
        kind: str | None = None,
        search: str = "",
        limit: int = 200,
        offset: int = 0,
        order: str = "created_at DESC",
    ) -> list[HistoryEntry]:
        clauses = ["deleted=0"]
        params: list[Any] = []
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if search:
            clauses.append("(title LIKE ? OR text LIKE ? OR audio_path LIKE ?)")
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        where = " AND ".join(clauses)
        safe_order = order if order in {
            "created_at DESC",
            "created_at ASC",
            "title COLLATE NOCASE ASC",
            "duration_ms DESC",
        } else "created_at DESC"
        params.extend([int(limit), int(offset)])
        rows = self.db.query(
            f"SELECT * FROM history WHERE {where} ORDER BY pinned DESC, {safe_order} LIMIT ? OFFSET ?",
            params,
        )
        return [HistoryEntry.from_row(row) for row in rows]

    def recent(self, kind: str | None = None, limit: int = 8) -> list[HistoryEntry]:
        return self.list(kind=kind, limit=limit)

    def count(self, kind: str | None = None) -> int:
        if kind:
            return int(self.db.scalar("SELECT COUNT(*) FROM history WHERE deleted=0 AND kind=?", (kind,), 0) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM history WHERE deleted=0", default=0) or 0)

    def find_by_audio_path(self, path: Path | str) -> HistoryEntry | None:
        row = self.db.query_one(
            "SELECT * FROM history WHERE audio_path=? AND deleted=0 ORDER BY id DESC LIMIT 1",
            (str(path),),
        )
        return HistoryEntry.from_row(row) if row else None

    # -- maintenance --------------------------------------------------------
    def apply_retention(self, retention_days: int, *, keep_pinned: bool = True) -> int:
        """Delete entries older than ``retention_days`` (0 = keep forever)."""
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        clause = "created_at < ?"
        params: list[Any] = [cutoff]
        if keep_pinned:
            clause += " AND pinned=0"
        before = int(self.db.scalar("SELECT COUNT(*) FROM history WHERE " + clause, params, 0) or 0)
        self.db.execute_write(f"DELETE FROM history WHERE {clause}", params)
        if before:
            log.info(
                "history retention removed old entries",
                extra={"event": "history_retention", "removed": before, "days": retention_days},
            )
        return before

    def purge_deleted(self) -> int:
        count = int(self.db.scalar("SELECT COUNT(*) FROM history WHERE deleted=1", default=0) or 0)
        if count:
            self.db.execute_write("DELETE FROM history WHERE deleted=1")
        return count

    def statistics(self) -> dict[str, Any]:
        rows = self.db.query(
            "SELECT kind, COUNT(*) AS c, COALESCE(SUM(duration_ms),0) AS d, "
            "COALESCE(SUM(char_count),0) AS chars FROM history WHERE deleted=0 GROUP BY kind"
        )
        by_kind = {row["kind"]: dict(row) for row in rows}
        total_duration = sum(int(row["d"]) for row in rows)
        return {
            "total": self.count(),
            "by_kind": by_kind,
            "duration_ms": total_duration,
            "characters": sum(int(row["chars"]) for row in rows),
            "first_entry": self.db.scalar(
                "SELECT MIN(created_at) FROM history WHERE deleted=0", default=""
            ),
        }


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _parse_dt(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    return None


def _count_words(text: str) -> int:
    return len([chunk for chunk in (text or "").split() if chunk])


def _default_title(kind: str, text: str) -> str:
    snippet = " ".join((text or "").split())[:60]
    if snippet:
        return snippet
    return KIND_LABELS.get(kind, kind) + " — " + time.strftime("%Y-%m-%d %H:%M")
