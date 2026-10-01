//! Repositories: thin, parameterized SQL helpers per table.
//!
//! No ORM. Ported from `src/x_auto/store/repos.py`.
//!
//! `Database` owns one `rusqlite::Connection`. It is not `Sync`, so Tauri
//! commands wrap it in a `Mutex` (see `crate::store::DbHandle`).

use std::path::{Path, PathBuf};

use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};

use super::db::{apply_schema, connect, fmt_ts, parse_ts};
use super::models::{Account, Draft, MediaUpload, PostLogEntry, Project, Tweet};
use crate::error::{Error, Result};

// ---- row mapping helpers ----------------------------------------------------

fn opt_dt(raw: Option<String>) -> Option<chrono::DateTime<Utc>> {
    raw.and_then(|s| parse_ts(&s))
}

fn opt_i64(raw: Option<i64>) -> Option<i64> {
    raw
}

fn row_to_account(row: &rusqlite::Row<'_>) -> rusqlite::Result<Account> {
    Ok(Account {
        handle: row.get("handle")?,
        user_id: row.get("user_id")?,
        display_name: row.get::<_, Option<String>>("display_name")?.unwrap_or_default(),
        added_at: opt_dt(row.get("added_at")?),
        last_fetched_at: opt_dt(row.get("last_fetched_at")?),
    })
}

fn row_to_tweet(row: &rusqlite::Row<'_>) -> rusqlite::Result<Tweet> {
    let pm_raw: Option<String> = row.get("public_metrics")?;
    let public_metrics = pm_raw
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default();

    Ok(Tweet {
        id: row.get("id")?,
        account_handle: row.get("account_handle")?,
        text: row.get("text")?,
        created_at: opt_dt(row.get("created_at")?).unwrap_or_else(Utc::now),
        public_metrics,
        quote_tweet_id: row.get("quote_tweet_id")?,
        quote_tweet_text: row.get("quote_tweet_text")?,
        quote_tweet_author_id: row.get("quote_tweet_author_id")?,
        source_image_url: row.get("source_image_url")?,
        fetched_at: opt_dt(row.get("fetched_at")?),
        status: row.get::<_, Option<String>>("status")?.unwrap_or_else(|| "new".into()),
    })
}

fn row_to_project(row: &rusqlite::Row<'_>) -> rusqlite::Result<Project> {
    let tags_raw: Option<String> = row.get("tags")?;
    Ok(Project {
        name: row.get("name")?,
        url: row.get("url")?,
        description: row.get::<_, Option<String>>("description")?.unwrap_or_default(),
        tags: tags_raw
            .map(|s| s.split(',').filter(|t| !t.is_empty()).map(String::from).collect())
            .unwrap_or_default(),
    })
}

fn row_to_draft(row: &rusqlite::Row<'_>) -> rusqlite::Result<Draft> {
    let image_paths: Vec<String> = row
        .get::<_, Option<String>>("image_paths")?
        .and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
        .unwrap_or_default();

    Ok(Draft {
        id: opt_i64(row.get("id")?),
        source_tweet_id: row.get("source_tweet_id")?,
        body: row.get("body")?,
        link_url: row.get("link_url")?,
        quote_tweet_id: row.get("quote_tweet_id")?,
        writing_mode: row
            .get::<_, Option<String>>("writing_mode")?
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "rephrase".into()),
        image_paths,
        tone: row.get::<_, Option<String>>("tone")?.unwrap_or_default(),
        status: row.get("status")?,
        created_at: opt_dt(row.get("created_at")?),
        finalized_at: opt_dt(row.get("finalized_at")?),
        posted_at: opt_dt(row.get("posted_at")?),
        x_tweet_id: row.get("x_tweet_id")?,
        x_reply_id: row.get("x_reply_id")?,
        cost_usd: row.get("cost_usd")?,
        error: row.get("error")?,
    })
}

fn row_to_media(row: &rusqlite::Row<'_>) -> rusqlite::Result<MediaUpload> {
    Ok(MediaUpload {
        id: opt_i64(row.get("id")?),
        local_path: row.get("local_path")?,
        filename: row.get::<_, Option<String>>("filename")?.unwrap_or_default(),
        x_media_id: row.get("x_media_id")?,
        x_media_id_uploaded_at: opt_dt(row.get("x_media_id_uploaded_at")?),
        mime: row.get("mime")?,
        size: row.get("size")?,
        created_at: opt_dt(row.get("created_at")?),
    })
}

/// A tweet payload coming from the X API, before persistence.
#[derive(Debug, Clone)]
pub struct IncomingTweet {
    pub id: String,
    pub text: String,
    pub created_at: String,
    pub public_metrics: serde_json::Value,
    pub quote_tweet_id: Option<String>,
    pub quote_tweet_text: Option<String>,
    pub quote_tweet_author_id: Option<String>,
    pub source_image_url: Option<String>,
}

/// High-level facade over one SQLite connection.
///
/// The connection is wrapped in a `Mutex` because `rusqlite::Connection`
/// is `Send` but not `Sync`, and Tauri's async commands need `&Database`
/// to be held across network awaits. All methods take `&self`, so
/// callers never see the locking.
pub struct Database {
    pub path: PathBuf,
    conn: std::sync::Mutex<Connection>,
}

impl Database {
    pub fn open(path: &Path) -> Result<Self> {
        let conn = connect(path)?;
        apply_schema(&conn)?;
        Ok(Self { path: path.to_path_buf(), conn: std::sync::Mutex::new(conn) })
    }

    /// In-memory database, for tests.
    pub fn open_in_memory() -> Result<Self> {
        let conn = Connection::open_in_memory()?;
        apply_schema(&conn)?;
        Ok(Self { path: PathBuf::from(":memory:"), conn: std::sync::Mutex::new(conn) })
    }

    /// Direct access, for tests and maintenance tasks.
    pub fn with_conn<T>(&self, f: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
        let conn = self.conn.lock().map_err(|_| Error::Db("database mutex poisoned".into()))?;
        f(&conn)
    }

    pub fn conn(&self) -> Result<std::sync::MutexGuard<'_, Connection>> {
        self.conn.lock().map_err(|_| Error::Db("database mutex poisoned".into()))
    }

    // ---- accounts ----

    pub fn upsert_account(&self, handle: &str, user_id: &str, display_name: &str) -> Result<()> {
        let conn = self.conn()?;
        conn.execute(
            r#"INSERT INTO accounts(handle, user_id, display_name)
               VALUES (?1, ?2, ?3)
               ON CONFLICT(handle) DO UPDATE SET
                   user_id=excluded.user_id,
                   display_name=COALESCE(NULLIF(excluded.display_name, ''), accounts.display_name)"#,
            params![handle, user_id, display_name],
        )?;
        Ok(())
    }

    pub fn mark_account_fetched(&self, handle: &str, at: Option<chrono::DateTime<Utc>>) -> Result<()> {
        let at = at.unwrap_or_else(Utc::now);
        let conn = self.conn()?;
        conn.execute(
            "UPDATE accounts SET last_fetched_at = ?1 WHERE handle = ?2",
            params![fmt_ts(&at), handle],
        )?;
        Ok(())
    }

    pub fn list_accounts(&self) -> Result<Vec<Account>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT * FROM accounts ORDER BY handle")?;
        let rows = stmt.query_map([], row_to_account)?;
        Ok(rows.filter_map(std::result::Result::ok).collect())
    }

    // ---- tweets ----

    /// Insert tweets if absent; refresh metadata otherwise.
    /// Returns the count of NEW rows.
    pub fn upsert_tweets(&self, account_handle: &str, tweets: &[IncomingTweet]) -> Result<usize> {
        let conn = self.conn()?;
        let mut new_count = 0usize;
        for t in tweets {
            let metrics_json = serde_json::to_string(&t.public_metrics)?;
            let affected = conn.execute(
                r#"INSERT OR IGNORE INTO tweets
                    (id, account_handle, text, created_at, public_metrics,
                     quote_tweet_id, quote_tweet_text, quote_tweet_author_id,
                     source_image_url, status)
                   VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 'new')"#,
                params![
                    t.id,
                    account_handle,
                    t.text,
                    t.created_at,
                    metrics_json,
                    t.quote_tweet_id,
                    t.quote_tweet_text,
                    t.quote_tweet_author_id,
                    t.source_image_url,
                ],
            )?;
            if affected > 0 {
                new_count += 1;
            } else {
                // Refresh metadata without changing the user's review state.
                conn.execute(
                    r#"UPDATE tweets SET text=?1, created_at=?2, public_metrics=?3,
                        quote_tweet_id=?4, quote_tweet_text=?5,
                        quote_tweet_author_id=?6, source_image_url=?7
                       WHERE id=?8"#,
                    params![
                        t.text,
                        t.created_at,
                        metrics_json,
                        t.quote_tweet_id,
                        t.quote_tweet_text,
                        t.quote_tweet_author_id,
                        t.source_image_url,
                        t.id,
                    ],
                )?;
            }
        }
        Ok(new_count)
    }

    pub fn list_tweets(&self, status: Option<&str>, limit: usize) -> Result<Vec<Tweet>> {
        let mut out = Vec::new();
        match status {
            Some(s) if !s.is_empty() => {
                let conn = self.conn()?;
                let mut stmt = conn.prepare(
                    "SELECT * FROM tweets WHERE status = ?1 ORDER BY fetched_at DESC LIMIT ?2",
                )?;
                let rows = stmt.query_map(params![s, limit as i64], row_to_tweet)?;
                out.extend(rows.filter_map(std::result::Result::ok));
            }
            _ => {
                let conn = self.conn()?;
                let mut stmt = conn.prepare("SELECT * FROM tweets ORDER BY fetched_at DESC LIMIT ?1")?;
                let rows = stmt.query_map(params![limit as i64], row_to_tweet)?;
                out.extend(rows.filter_map(std::result::Result::ok));
            }
        }
        Ok(out)
    }

    pub fn get_tweet(&self, tweet_id: &str) -> Result<Option<Tweet>> {
        let conn = self.conn()?;
        let row = conn
            .query_row("SELECT * FROM tweets WHERE id = ?1", params![tweet_id], row_to_tweet)
            .optional()?;
        Ok(row)
    }

    pub fn set_tweet_status(&self, tweet_id: &str, status: &str) -> Result<()> {
        let conn = self.conn()?;
        conn.execute(
            "UPDATE tweets SET status = ?1 WHERE id = ?2",
            params![status, tweet_id],
        )?;
        Ok(())
    }

    pub fn set_tweet_statuses(&self, tweet_ids: &[String], status: &str) -> Result<usize> {
        if tweet_ids.is_empty() {
            return Ok(0);
        }
        let placeholders = tweet_ids
            .iter()
            .map(|_| "?")
            .collect::<Vec<_>>()
            .join(",");
        let sql = format!("UPDATE tweets SET status = ? WHERE id IN ({placeholders})");
        let conn = self.conn()?;
        let mut stmt = conn.prepare(&sql)?;
        let mut values: Vec<Box<dyn rusqlite::ToSql>> = Vec::with_capacity(tweet_ids.len() + 1);
        values.push(Box::new(status.to_string()));
        for id in tweet_ids {
            values.push(Box::new(id.clone()));
        }
        let refs: Vec<&dyn rusqlite::ToSql> = values.iter().map(|b| b.as_ref()).collect();
        let changed = stmt.execute(refs.as_slice())?;
        Ok(changed)
    }

    // ---- projects ----

    pub fn replace_projects(&self, projects: &[Project]) -> Result<()> {
        let conn = self.conn()?;
        let tx = conn.unchecked_transaction()?;
        tx.execute("DELETE FROM projects", [])?;
        {
            let mut stmt = tx.prepare(
                "INSERT INTO projects(name, url, description, tags) VALUES (?1, ?2, ?3, ?4)",
            )?;
            for p in projects {
                stmt.execute(params![p.name, p.url, p.description, p.tags.join(",")])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn list_projects(&self) -> Result<Vec<Project>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT * FROM projects ORDER BY name")?;
        let rows = stmt.query_map([], row_to_project)?;
        Ok(rows.filter_map(std::result::Result::ok).collect())
    }

    // ---- drafts ----

    pub fn create_draft(&self, draft: &Draft) -> Result<i64> {
        let image_paths = serde_json::to_string(&draft.image_paths)?;
        let conn = self.conn()?;
        conn.execute(
            r#"INSERT INTO drafts
                (source_tweet_id, body, link_url, quote_tweet_id, writing_mode,
                 image_paths, tone, status)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)"#,
            params![
                draft.source_tweet_id,
                draft.body,
                draft.link_url,
                draft.quote_tweet_id,
                draft.writing_mode,
                image_paths,
                draft.tone,
                draft.status,
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn get_draft(&self, draft_id: i64) -> Result<Option<Draft>> {
        let conn = self.conn()?;
        let row = conn
            .query_row("SELECT * FROM drafts WHERE id = ?1", params![draft_id], row_to_draft)
            .optional()?;
        Ok(row)
    }

    pub fn list_drafts(&self, status: Option<&str>, limit: usize) -> Result<Vec<Draft>> {
        let mut out = Vec::new();
        match status {
            Some(s) if !s.is_empty() => {
                let conn = self.conn()?;
                let mut stmt = conn.prepare(
                    "SELECT * FROM drafts WHERE status = ?1 ORDER BY created_at DESC LIMIT ?2",
                )?;
                let rows = stmt.query_map(params![s, limit as i64], row_to_draft)?;
                out.extend(rows.filter_map(std::result::Result::ok));
            }
            _ => {
                let conn = self.conn()?;
                let mut stmt = conn.prepare("SELECT * FROM drafts ORDER BY created_at DESC LIMIT ?1")?;
                let rows = stmt.query_map(params![limit as i64], row_to_draft)?;
                out.extend(rows.filter_map(std::result::Result::ok));
            }
        }
        Ok(out)
    }

    pub fn update_draft(&self, draft: &Draft) -> Result<()> {
        let Some(id) = draft.id else {
            return Err(Error::Validation("cannot update a draft without an id".into()));
        };
        let image_paths = serde_json::to_string(&draft.image_paths)?;
        let conn = self.conn()?;
        conn.execute(
            r#"UPDATE drafts SET
                body=?1, link_url=?2, quote_tweet_id=?3, writing_mode=?4,
                image_paths=?5, tone=?6, status=?7,
                finalized_at=?8, posted_at=?9,
                x_tweet_id=?10, x_reply_id=?11, cost_usd=?12, error=?13
               WHERE id=?14"#,
            params![
                draft.body,
                draft.link_url,
                draft.quote_tweet_id,
                draft.writing_mode,
                image_paths,
                draft.tone,
                draft.status,
                draft.finalized_at.as_ref().map(fmt_ts),
                draft.posted_at.as_ref().map(fmt_ts),
                draft.x_tweet_id,
                draft.x_reply_id,
                draft.cost_usd,
                draft.error,
                id,
            ],
        )?;
        Ok(())
    }

    pub fn delete_draft(&self, draft_id: i64) -> Result<()> {
        let conn = self.conn()?;
        conn.execute("DELETE FROM drafts WHERE id = ?1", params![draft_id])?;
        Ok(())
    }

    // ---- post_log ----

    pub fn log_post(
        &self,
        draft_id: Option<i64>,
        action: &str,
        cost: Option<f64>,
        result: &str,
        detail: &str,
    ) -> Result<i64> {
        let conn = self.conn()?;
        conn.execute(
            r#"INSERT INTO post_log(draft_id, action, cost_usd, result, detail)
               VALUES (?1, ?2, ?3, ?4, ?5)"#,
            params![draft_id, action, cost, result, detail],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn recent_log(&self, limit: usize) -> Result<Vec<PostLogEntry>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT * FROM post_log ORDER BY created_at DESC LIMIT ?1")?;
        let rows = stmt.query_map(params![limit as i64], |row| {
            Ok(PostLogEntry {
                id: opt_i64(row.get("id")?),
                draft_id: opt_i64(row.get("draft_id")?),
                action: row.get::<_, Option<String>>("action")?.unwrap_or_default(),
                cost_usd: row.get("cost_usd")?,
                result: row.get::<_, Option<String>>("result")?,
                detail: row.get::<_, Option<String>>("detail")?.unwrap_or_default(),
                created_at: opt_dt(row.get("created_at")?),
            })
        })?;
        Ok(rows.filter_map(std::result::Result::ok).collect())
    }

    /// Sum of `post_log.cost_usd`. Reconciles with the in-memory meter.
    pub fn total_session_cost(&self) -> Result<f64> {
        let conn = self.conn()?;
        let row: f64 = conn.query_row(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM post_log",
            [],
            |r| r.get(0),
        )?;
        Ok(row)
    }

    // ---- media_uploads ----

    /// Insert or update by `local_path`. Returns the row id.
    ///
    /// An existing row's cached `x_media_id` is preserved: that field is
    /// only written by `update_media_upload_x_id` after a real upload.
    ///
    /// The lookup and the write share one lock. Calling
    /// `get_media_upload_by_path` here would deadlock, because the
    /// connection mutex is not reentrant.
    pub fn register_media_upload(&self, entry: &MediaUpload) -> Result<i64> {
        let created_at = entry.created_at.unwrap_or_else(Utc::now);
        let conn = self.conn()?;

        let existing = conn
            .query_row(
                "SELECT * FROM media_uploads WHERE local_path = ?1",
                params![entry.local_path],
                row_to_media,
            )
            .optional()?;

        if let Some(existing) = existing {
            conn.execute(
                r#"UPDATE media_uploads SET filename=?1, mime=?2, size=?3 WHERE local_path=?4"#,
                params![entry.filename, entry.mime, entry.size, entry.local_path],
            )?;
            return Ok(existing.id.unwrap_or(0));
        }

        conn.execute(
            r#"INSERT INTO media_uploads
                (local_path, filename, x_media_id, x_media_id_uploaded_at,
                 mime, size, created_at)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)"#,
            params![
                entry.local_path,
                entry.filename,
                entry.x_media_id,
                entry.x_media_id_uploaded_at.as_ref().map(fmt_ts),
                entry.mime,
                entry.size,
                fmt_ts(&created_at),
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn update_media_upload_x_id(
        &self,
        local_path: &str,
        x_media_id: &str,
        uploaded_at: chrono::DateTime<Utc>,
    ) -> Result<()> {
        let conn = self.conn()?;
        conn.execute(
            r#"UPDATE media_uploads SET x_media_id=?1, x_media_id_uploaded_at=?2 WHERE local_path=?3"#,
            params![x_media_id, fmt_ts(&uploaded_at), local_path],
        )?;
        Ok(())
    }

    pub fn get_media_upload_by_path(&self, local_path: &str) -> Result<Option<MediaUpload>> {
        let conn = self.conn()?;
        let row = conn
            .query_row(
                "SELECT * FROM media_uploads WHERE local_path = ?1",
                params![local_path],
                row_to_media,
            )
            .optional()?;
        Ok(row)
    }

    pub fn list_media_uploads(&self, limit: usize) -> Result<Vec<MediaUpload>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT * FROM media_uploads ORDER BY created_at DESC LIMIT ?1")?;
        let rows = stmt.query_map(params![limit as i64], row_to_media)?;
        Ok(rows.filter_map(std::result::Result::ok).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    fn incoming(id: &str) -> IncomingTweet {
        IncomingTweet {
            id: id.into(),
            text: format!("body of {id}"),
            created_at: "2026-08-01T10:00:00Z".into(),
            public_metrics: serde_json::json!({"like_count": 3}),
            quote_tweet_id: None,
            quote_tweet_text: None,
            quote_tweet_author_id: None,
            source_image_url: None,
        }
    }

    fn sample_draft() -> Draft {
        Draft {
            id: None,
            source_tweet_id: None,
            body: "hello".into(),
            link_url: None,
            quote_tweet_id: None,
            writing_mode: "rephrase".into(),
            image_paths: vec![],
            tone: String::new(),
            status: "draft".into(),
            created_at: None,
            finalized_at: None,
            posted_at: None,
            x_tweet_id: None,
            x_reply_id: None,
            cost_usd: None,
            error: None,
        }
    }

    #[test]
    fn upsert_tweets_counts_only_new_rows() {
        let db = Database::open_in_memory().unwrap();
        db.upsert_account("alice", "1", "Alice").unwrap();

        let n1 = db.upsert_tweets("alice", &[incoming("t1"), incoming("t2")]).unwrap();
        assert_eq!(n1, 2);

        // Second pass: all existing, metadata refreshed, count 0.
        let mut updated = incoming("t1");
        updated.text = "edited".into();
        let n2 = db.upsert_tweets("alice", &[updated]).unwrap();
        assert_eq!(n2, 0);

        assert_eq!(db.get_tweet("t1").unwrap().unwrap().text, "edited");
        assert_eq!(db.list_tweets(Some("new"), 10).unwrap().len(), 2);
    }

    #[test]
    fn set_tweet_statuses_bulk_updates() {
        let db = Database::open_in_memory().unwrap();
        db.upsert_account("alice", "1", "Alice").unwrap();
        db.upsert_tweets("alice", &[incoming("a"), incoming("b"), incoming("c")])
            .unwrap();

        let ids = vec!["a".to_string(), "b".to_string()];
        assert_eq!(db.set_tweet_statuses(&ids, "archived").unwrap(), 2);
        assert_eq!(db.list_tweets(Some("archived"), 10).unwrap().len(), 2);
        assert_eq!(db.list_tweets(Some("new"), 10).unwrap().len(), 1);
        assert_eq!(db.set_tweet_statuses(&[], "archived").unwrap(), 0);
    }

    #[test]
    fn draft_crud_roundtrip() {
        let db = Database::open_in_memory().unwrap();
        let id = db.create_draft(&sample_draft()).unwrap();

        let mut d = db.get_draft(id).unwrap().unwrap();
        assert_eq!(d.body, "hello");
        assert_eq!(d.writing_mode, "rephrase");

        d.body = "updated".into();
        d.image_paths = vec!["/a.png".into(), "/b.png".into()];
        d.status = "final".into();
        d.finalized_at = Some(Utc::now());
        d.cost_usd = Some(0.03);
        db.update_draft(&d).unwrap();

        let back = db.get_draft(id).unwrap().unwrap();
        assert_eq!(back.body, "updated");
        assert_eq!(back.image_paths.len(), 2);
        assert_eq!(back.status, "final");
        assert!((back.cost_usd.unwrap() - 0.03).abs() < 1e-9);
        assert!(back.finalized_at.is_some());

        db.delete_draft(id).unwrap();
        assert!(db.get_draft(id).unwrap().is_none());
    }

    #[test]
    fn update_draft_without_id_is_rejected() {
        let db = Database::open_in_memory().unwrap();
        let err = db.update_draft(&sample_draft()).unwrap_err();
        assert!(matches!(err, Error::Validation(_)));
    }

    #[test]
    fn replace_projects_is_a_full_swap() {
        let db = Database::open_in_memory().unwrap();
        db.replace_projects(&[
            Project { name: "A".into(), url: "https://a.com".into(), description: String::new(), tags: vec!["x".into()] },
            Project { name: "B".into(), url: "https://b.com".into(), description: String::new(), tags: vec![] },
        ])
        .unwrap();
        assert_eq!(db.list_projects().unwrap().len(), 2);

        db.replace_projects(&[]).unwrap();
        assert_eq!(db.list_projects().unwrap().len(), 0);
    }

    #[test]
    fn account_upsert_keeps_display_name_when_blank() {
        let db = Database::open_in_memory().unwrap();
        db.upsert_account("alice", "1", "Alice").unwrap();
        db.upsert_account("alice", "2", "").unwrap();
        let acc = &db.list_accounts().unwrap()[0];
        assert_eq!(acc.user_id, "2");
        assert_eq!(acc.display_name, "Alice");

        db.mark_account_fetched("alice", None).unwrap();
        assert!(db.list_accounts().unwrap()[0].last_fetched_at.is_some());
    }

    #[test]
    fn post_log_sums_cost() {
        let db = Database::open_in_memory().unwrap();
        assert_eq!(db.total_session_cost().unwrap(), 0.0);
        db.log_post(None, "post_now", Some(0.015), "success", "x").unwrap();
        db.log_post(None, "post_now", Some(0.015), "success", "y").unwrap();
        assert!((db.total_session_cost().unwrap() - 0.03).abs() < 1e-9);
        assert_eq!(db.recent_log(10).unwrap().len(), 2);
    }

    #[test]
    fn media_upload_preserves_cached_x_id_on_re_register() {
        let db = Database::open_in_memory().unwrap();
        let path = "/data/media_cache/x.png".to_string();
        let now = Utc::now();

        db.register_media_upload(&MediaUpload {
            id: None,
            local_path: path.clone(),
            filename: "x.png".into(),
            x_media_id: Some("m123".into()),
            x_media_id_uploaded_at: Some(now),
            mime: Some("image/png".into()),
            size: Some(100),
            created_at: None,
        })
        .unwrap();

        // Re-register with fresh metadata but no x_media_id: the cached
        // id must survive.
        db.register_media_upload(&MediaUpload {
            id: None,
            local_path: path.clone(),
            filename: "x.png".into(),
            x_media_id: None,
            x_media_id_uploaded_at: None,
            mime: Some("image/png".into()),
            size: Some(999),
            created_at: None,
        })
        .unwrap();

        let row = db.get_media_upload_by_path(&path).unwrap().unwrap();
        assert_eq!(row.x_media_id.as_deref(), Some("m123"));
        assert_eq!(row.size, Some(999));
    }

    #[test]
    fn media_ttl_expiry_is_driven_by_stored_timestamp() {
        let db = Database::open_in_memory().unwrap();
        let path = "/data/old.png".to_string();
        db.register_media_upload(&MediaUpload {
            id: None,
            local_path: path.clone(),
            filename: "old.png".into(),
            x_media_id: Some("m1".into()),
            x_media_id_uploaded_at: Some(Utc::now() - Duration::hours(25)),
            mime: None,
            size: None,
            created_at: None,
        })
        .unwrap();
        assert!(!db.get_media_upload_by_path(&path).unwrap().unwrap().is_still_valid());
    }
}