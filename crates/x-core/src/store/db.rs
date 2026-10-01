//! SQLite connection + schema management.
//!
//! Ported from `src/x_auto/store/db.py`.
//!
//! Differences from the Python original, all deliberate:
//!   * The dead `schedules` table is not created (no code references it).
//!   * `media_uploads` and the rest keep identical column definitions, so
//!     existing `state.db` files open unchanged.
//!
//! Timestamps are stored as ISO-8601 strings with a space separator,
//! matching what the Python app wrote (`isoformat(sep=" ")`), so old
//! rows parse without a migration.

use std::path::Path;

use chrono::{DateTime, NaiveDateTime, TimeZone, Utc};
use rusqlite::{Connection, OpenFlags};

use crate::error::Error;

/// Every DDL statement. Idempotent: safe to replay on every open.
pub const SCHEMA: &[&str] = &[
    r#"
    CREATE TABLE IF NOT EXISTS accounts (
        handle           TEXT PRIMARY KEY,
        user_id          TEXT NOT NULL,
        display_name     TEXT,
        added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_fetched_at  TIMESTAMP
    )
    "#,
    r#"
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
    "#,
    "CREATE INDEX IF NOT EXISTS idx_tweets_status ON tweets(status, fetched_at DESC)",
    r#"
    CREATE TABLE IF NOT EXISTS projects (
        name             TEXT PRIMARY KEY,
        url              TEXT NOT NULL,
        description      TEXT,
        tags             TEXT
    )
    "#,
    r#"
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
    "#,
    "CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status)",
    r#"
    CREATE TABLE IF NOT EXISTS post_log (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id         INTEGER REFERENCES drafts(id),
        action           TEXT,
        cost_usd         REAL,
        result           TEXT,
        detail           TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    "#,
    "CREATE INDEX IF NOT EXISTS idx_post_log_draft ON post_log(draft_id, created_at DESC)",
    r#"
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
    "#,
    "CREATE INDEX IF NOT EXISTS idx_media_uploads_created ON media_uploads(created_at DESC)",
];

/// Additive migrations for databases created before quote-post support.
/// SQLite has no `IF NOT EXISTS` form for `ALTER TABLE`, so inspect each
/// table's columns before adding the new nullable ones.
const MIGRATIONS: &[(&str, &[(&str, &str)])] = &[
    (
        "tweets",
        &[
            ("quote_tweet_id", "TEXT"),
            ("quote_tweet_text", "TEXT"),
            ("quote_tweet_author_id", "TEXT"),
            ("source_image_url", "TEXT"),
        ],
    ),
    (
        "drafts",
        &[
            ("quote_tweet_id", "TEXT"),
            ("writing_mode", "TEXT NOT NULL DEFAULT 'rephrase'"),
        ],
    ),
];

/// Open a SQLite connection with WAL + FK enforcement.
pub fn connect(db_path: &Path) -> Result<Connection, Error> {
    if let Some(parent) = db_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| Error::Io(format!("create {}: {e}", parent.display())))?;
    }
    let conn = Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_URI
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| Error::Db(format!("open {}: {e}", db_path.display())))?;

    conn.pragma_update(None, "foreign_keys", "ON")
        .map_err(|e| Error::Db(format!("pragma foreign_keys: {e}")))?;
    // WAL can fail on some network/locked files; NORMAL sync + busy timeout
    // are the important ones, so log-and-continue on WAL.
    let _ = conn.pragma_update(None, "journal_mode", "WAL");
    conn.pragma_update(None, "synchronous", "NORMAL")
        .map_err(|e| Error::Db(format!("pragma synchronous: {e}")))?;
    conn.busy_timeout(std::time::Duration::from_secs(30))
        .map_err(|e| Error::Db(format!("busy_timeout: {e}")))?;
    Ok(conn)
}

/// Apply the full schema plus additive migrations. Idempotent.
pub fn apply_schema(conn: &Connection) -> Result<(), Error> {
    for stmt in SCHEMA {
        conn.execute_batch(stmt)
            .map_err(|e| Error::Db(format!("schema: {e}")))?;
    }

    for (table, columns) in MIGRATIONS {
        let mut stmt = conn
            .prepare(&format!("PRAGMA table_info({table})"))
            .map_err(|e| Error::Db(format!("table_info {table}: {e}")))?;
        let existing: std::collections::HashSet<String> = stmt
            .query_map([], |row| row.get::<_, String>(1))
            .map_err(|e| Error::Db(format!("table_info {table}: {e}")))?
            .filter_map(|r| r.ok())
            .collect();
        drop(stmt);

        for (column, definition) in *columns {
            if !existing.contains(*column) {
                conn.execute_batch(&format!(
                    "ALTER TABLE {table} ADD COLUMN {column} {definition}"
                ))
                .map_err(|e| Error::Db(format!("alter {table}.{column}: {e}")))?;
            }
        }
    }
    Ok(())
}

/// Format a timestamp the way the Python app did: `isoformat(sep=" ")`.
pub fn fmt_ts(dt: &DateTime<Utc>) -> String {
    dt.format("%Y-%m-%d %H:%M:%S%.6f").to_string()
}

/// Parse a stored timestamp, tolerating the shapes SQLite and the Python
/// app both produce: `YYYY-MM-DD HH:MM:SS[.ffffff]`, and ISO-8601 with a
/// `T` separator and/or a trailing `Z`/offset.
pub fn parse_ts(raw: &str) -> Option<DateTime<Utc>> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    let normalized = s.replace('T', " ").replace('Z', "+00:00");
    if let Ok(dt) = DateTime::parse_from_rfc3339(&normalized) {
        return Some(dt.with_timezone(&Utc));
    }
    for fmt in [
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(&normalized, fmt) {
            return Some(Utc.from_utc_datetime(&naive));
        }
        if fmt == "%Y-%m-%d" {
            if let Ok(date) = chrono::NaiveDate::parse_from_str(&normalized, fmt) {
                return Some(Utc.from_utc_datetime(&date.and_hms_opt(0, 0, 0)?));
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamp_roundtrip() {
        let now = Utc::now();
        let s = fmt_ts(&now);
        let back = parse_ts(&s).expect("roundtrip");
        assert!((back - now).num_milliseconds().abs() < 1000);
    }

    #[test]
    fn parses_python_isoformat_with_space() {
        let v = parse_ts("2026-08-27 12:34:56.123456").unwrap();
        assert_eq!(v.format("%Y-%m-%d").to_string(), "2026-08-27");
    }

    #[test]
    fn parses_rfc3339_with_z() {
        let v = parse_ts("2026-08-27T12:34:56Z").unwrap();
        assert_eq!(v.format("%H:%M:%S").to_string(), "12:34:56");
    }

    #[test]
    fn parses_space_separated_from_sqlite_default() {
        let v = parse_ts("2026-08-27 12:34:56").unwrap();
        assert_eq!(v.format("%H:%M:%S").to_string(), "12:34:56");
    }

    #[test]
    fn empty_and_garbage_are_none() {
        assert!(parse_ts("").is_none());
        assert!(parse_ts("   ").is_none());
        assert!(parse_ts("not-a-date").is_none());
    }

    #[test]
    fn schema_is_idempotent_and_omits_dead_schedules_table() {
        let dir = tempfile::tempdir().unwrap();
        let conn = connect(&dir.path().join("t.db")).unwrap();
        apply_schema(&conn).unwrap();
        // Replay: still fine.
        apply_schema(&conn).unwrap();

        let tables: Vec<String> = conn
            .prepare("SELECT name FROM sqlite_master WHERE type='table'")
            .unwrap()
            .query_map([], |r| r.get::<_, String>(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect();
        for expected in [
            "accounts",
            "tweets",
            "projects",
            "drafts",
            "post_log",
            "media_uploads",
        ] {
            assert!(tables.contains(&expected.to_string()), "missing {expected}");
        }
        assert!(!tables.contains(&"schedules".to_string()), "dead table created");
    }

    #[test]
    fn migration_adds_missing_columns_to_legacy_db() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("legacy.db");
        {
            let conn = Connection::open(&path).unwrap();
            // A pre-quote-support tweets table, missing the 4 new columns.
            conn.execute_batch(
                r#"CREATE TABLE tweets (
                    id TEXT PRIMARY KEY, account_handle TEXT NOT NULL,
                    text TEXT NOT NULL, created_at TIMESTAMP NOT NULL,
                    public_metrics TEXT, fetched_at TIMESTAMP, status TEXT NOT NULL DEFAULT 'new'
                );
                CREATE TABLE drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_tweet_id TEXT,
                    body TEXT NOT NULL, link_url TEXT, image_paths TEXT, tone TEXT,
                    status TEXT NOT NULL DEFAULT 'draft', created_at TIMESTAMP,
                    finalized_at TIMESTAMP, posted_at TIMESTAMP, x_tweet_id TEXT,
                    x_reply_id TEXT, cost_usd REAL, error TEXT
                );"#,
            )
            .unwrap();
        }
        let conn = connect(&path).unwrap();
        apply_schema(&conn).unwrap();
        apply_schema(&conn).unwrap();

        for (table, col) in [
            ("tweets", "quote_tweet_id"),
            ("tweets", "source_image_url"),
            ("drafts", "quote_tweet_id"),
            ("drafts", "writing_mode"),
        ] {
            let found: i64 = conn
                .query_row(
                    &format!("SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name='{col}'"),
                    [],
                    |r| r.get(0),
                )
                .unwrap();
            assert_eq!(found, 1, "{table}.{col} missing");
        }
    }
}