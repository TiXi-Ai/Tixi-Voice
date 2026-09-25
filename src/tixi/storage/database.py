"""SQLite persistence layer.

A single :class:`Database` object owns the connection factory; repositories
(history, settings, models) borrow short-lived connections.  SQLite is used in
WAL mode with ``check_same_thread=False`` so background worker threads can read
and write without marshalling through the GUI thread.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from ..app.logging_config import get_logger
from .migrations import MIGRATIONS, SCHEMA_VERSION

log = get_logger("tixi.storage")


class Database:
    """Thread-safe SQLite wrapper with migrations and helpers."""

    def __init__(self, path: Path, *, timeout: float = 15.0) -> None:
        self.path = Path(path)
        self._timeout = timeout
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._initialised = False

    # -- connections ---------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        """A per-thread connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self.path,
                timeout=self._timeout,
                isolation_level=None,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        return conn

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a write transaction; rolls back on error."""
        conn = self.connection
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            self._local.conn = None

    # -- setup ---------------------------------------------------------------
    def initialise(self) -> None:
        """Create or upgrade the schema (safe to call more than once)."""
        if self._initialised:
            return
        conn = self.connection
        with self._write_lock:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
            )
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = int(row["v"]) if row and row["v"] is not None else 0
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                log.info("applying database migration", extra={"event": "db_migrate", "version": version})
                with self.transaction() as tx:
                    for statement in statements:
                        tx.execute(statement)
                    tx.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (version, _now()),
                    )
            self._initialised = True
        if current != SCHEMA_VERSION:
            log.info(
                "database ready",
                extra={"event": "db_ready", "from": current, "to": SCHEMA_VERSION},
            )

    # -- helpers -------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def execute_write(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as tx:
            cursor = tx.execute(sql, params)
            return cursor.lastrowid or cursor.rowcount

    def executemany_write(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        if not rows:
            return 0
        with self.transaction() as tx:
            tx.executemany(sql, rows)
            return len(rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    def table_exists(self, name: str) -> bool:
        row = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return row is not None

    def vacuum(self) -> None:
        with self._write_lock:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.connection.execute("VACUUM")

    def stats(self) -> dict[str, Any]:
        size = self.path.stat().st_size if self.path.exists() else 0
        wal = self.path.with_suffix(self.path.suffix + "-wal")
        return {
            "path": str(self.path),
            "size_bytes": size + (wal.stat().st_size if wal.exists() else 0),
            "schema_version": int(
                self.scalar("SELECT MAX(version) FROM schema_version", default=0) or 0
            ),
        }

    def backup_to(self, target: Path) -> Path:
        """Consistent online backup (used before schema upgrades/exit)."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(target)) as destination:
            self.connection.backup(destination)
        return target


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
