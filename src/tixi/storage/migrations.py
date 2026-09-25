"""Ordered database migrations.

Each entry is ``(version, [sql, ...])`` and is applied exactly once, inside a
transaction, in ascending order.  Never edit an existing entry: append a new
one so that already-upgraded installs stay consistent.
"""

from __future__ import annotations

SCHEMA_VERSION = 3

_V1: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        section     TEXT NOT NULL,
        key         TEXT NOT NULL,
        value       TEXT,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (section, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS history (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        kind         TEXT NOT NULL,
        title        TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        language     TEXT,
        model_id     TEXT,
        text         TEXT,
        audio_path   TEXT,
        duration_ms  INTEGER,
        char_count   INTEGER,
        word_count   INTEGER,
        meta         TEXT,
        pinned       INTEGER NOT NULL DEFAULT 0,
        deleted      INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_history_kind ON history(kind, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS audio_assets (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id    INTEGER REFERENCES history(id) ON DELETE SET NULL,
        path          TEXT NOT NULL,
        file_name     TEXT NOT NULL,
        format        TEXT,
        sample_rate   INTEGER,
        channels      INTEGER,
        bit_depth     INTEGER,
        duration_ms   INTEGER,
        size_bytes    INTEGER,
        source_text   TEXT,
        voice_id      TEXT,
        model_id      TEXT,
        language      TEXT,
        favourite     INTEGER NOT NULL DEFAULT 0,
        missing       INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audio_created ON audio_assets(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audio_name ON audio_assets(file_name)",
    """
    CREATE TABLE IF NOT EXISTS transcriptions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id    INTEGER REFERENCES history(id) ON DELETE SET NULL,
        source_path   TEXT,
        source_name   TEXT,
        text          TEXT,
        language      TEXT,
        language_probability REAL,
        model_id      TEXT,
        duration_ms   INTEGER,
        segments      TEXT,
        words         TEXT,
        speaker_count INTEGER,
        created_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transcriptions_created ON transcriptions(created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS diacritizations (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id    INTEGER REFERENCES history(id) ON DELETE SET NULL,
        source_text   TEXT,
        output_text   TEXT,
        mode          TEXT,
        engine_id     TEXT,
        char_count    INTEGER,
        confidence    REAL,
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS models (
        id            TEXT PRIMARY KEY,
        name          TEXT,
        category      TEXT,
        engine        TEXT,
        version       TEXT,
        path          TEXT,
        size_bytes    INTEGER,
        status        TEXT,
        checksum      TEXT,
        languages     TEXT,
        license       TEXT,
        source_url    TEXT,
        installed_at  TEXT,
        last_used_at  TEXT,
        meta          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS downloads (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id      TEXT NOT NULL,
        url           TEXT NOT NULL,
        dest_path     TEXT NOT NULL,
        temp_path     TEXT,
        bytes_total   INTEGER,
        bytes_done    INTEGER NOT NULL DEFAULT 0,
        status        TEXT NOT NULL DEFAULT 'queued',
        expected_sha256 TEXT,
        actual_sha256 TEXT,
        etag          TEXT,
        error         TEXT,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_downloads_model ON downloads(model_id, status)",
    """
    CREATE TABLE IF NOT EXISTS engine_packs (
        id            TEXT PRIMARY KEY,
        name          TEXT,
        version       TEXT,
        path          TEXT,
        size_bytes    INTEGER,
        status        TEXT,
        packages      TEXT,
        installed_at  TEXT,
        error         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_stats (
        key         TEXT PRIMARY KEY,
        value       INTEGER NOT NULL DEFAULT 0,
        updated_at  TEXT NOT NULL
    )
    """,
]

_V2: list[str] = [
    "ALTER TABLE audio_assets ADD COLUMN tags TEXT",
    "ALTER TABLE history ADD COLUMN pinned_at TEXT",
    "CREATE INDEX IF NOT EXISTS idx_history_pinned ON history(pinned, created_at DESC)",
]

_V3: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS dictionary_entries (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        word         TEXT NOT NULL,
        replacement  TEXT NOT NULL,
        language     TEXT,
        case_sensitive INTEGER NOT NULL DEFAULT 0,
        priority     INTEGER NOT NULL DEFAULT 0,
        enabled      INTEGER NOT NULL DEFAULT 1,
        note         TEXT,
        created_at   TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_dictionary_word ON dictionary_entries(word, language)",
    """
    CREATE TABLE IF NOT EXISTS pronunciation_overrides (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        word         TEXT NOT NULL UNIQUE,
        ipa          TEXT,
        diacritized  TEXT,
        note         TEXT,
        created_at   TEXT NOT NULL
    )
    """,
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, _V1),
    (2, _V2),
    (3, _V3),
]
