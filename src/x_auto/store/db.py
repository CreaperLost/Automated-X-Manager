"""SQLite connection + schema migrations.

Single source of truth for the schema. Migrations are applied on
startup; they are idempotent and additive. WAL mode keeps local
reads and writes responsive.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS accounts (
        handle           TEXT PRIMARY KEY,
        user_id          TEXT NOT NULL,
        display_name     TEXT,
        added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_fetched_at  TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tweets (
        id               TEXT PRIMARY KEY,
        account_handle   TEXT NOT NULL REFERENCES accounts(handle),
        text             TEXT NOT NULL,
        created_at       TIMESTAMP NOT NULL,
        public_metrics   TEXT,
        quote_tweet_id   TEXT,
        quote_tweet_text TEXT,
        quote_tweet_author_id TEXT,
        source_image_url TEXT,
        fetched_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status           TEXT NOT NULL DEFAULT 'new'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tweets_status ON tweets(status, fetched_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS projects (
        name             TEXT PRIMARY KEY,
        url              TEXT NOT NULL,
        description      TEXT,
        tags             TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS drafts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        source_tweet_id  TEXT REFERENCES tweets(id),
        body             TEXT NOT NULL,
        link_url         TEXT,
        quote_tweet_id   TEXT,
        writing_mode     TEXT NOT NULL DEFAULT 'rephrase',
        image_paths      TEXT,
        tone             TEXT,
        status           TEXT NOT NULL DEFAULT 'draft',
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finalized_at     TIMESTAMP,
        posted_at        TIMESTAMP,
        x_tweet_id       TEXT,
        x_reply_id       TEXT,
        cost_usd         REAL,
        error            TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status)",
    """
    CREATE TABLE IF NOT EXISTS post_log (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id         INTEGER REFERENCES drafts(id),
        action           TEXT,
        cost_usd         REAL,
        result           TEXT,
        detail           TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_post_log_draft ON post_log(draft_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS media_uploads (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        local_path               TEXT NOT NULL UNIQUE,
        filename                 TEXT NOT NULL,
        x_media_id               TEXT,
        x_media_id_uploaded_at   TIMESTAMP,
        mime                     TEXT,
        size                     INTEGER,
        created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_media_uploads_created ON media_uploads(created_at DESC)",
]


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL + FK enforcement.

    We deliberately do NOT use `PARSE_DECLTYPES`: SQLite's default
    timestamp converter expects space-separated strings, but our
    timestamps are stored in ISO 8601 (T-separated). The repos
    layer parses datetimes manually via `_parse_dt`.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        timeout=30.0,
        isolation_level=None,  # autocommit; we manage txns explicitly
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the full schema. Idempotent."""
    for stmt in SCHEMA:
        conn.execute(stmt)

    # Additive migrations for databases created before quote-post support.
    # SQLite has no IF NOT EXISTS form for ALTER TABLE, so inspect each
    # table before adding the new nullable columns.
    migrations = {
        "tweets": {
            "quote_tweet_id": "TEXT",
            "quote_tweet_text": "TEXT",
            "quote_tweet_author_id": "TEXT",
            "source_image_url": "TEXT",
        },
        "drafts": {
            "quote_tweet_id": "TEXT",
            "writing_mode": "TEXT NOT NULL DEFAULT 'rephrase'",
        },
    }
    for table, columns in migrations.items():
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
