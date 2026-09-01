"""Repositories: thin, parameterized SQL helpers per table.

No ORM. Each method takes a sqlite3.Connection; pass the same
connection through to share a transaction.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .db import apply_schema, connect
from .models import Account, Draft, MediaUpload, Project, Schedule, Tweet

# ---- helpers ----------------------------------------------------------------

def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        handle=row["handle"],
        user_id=row["user_id"],
        display_name=row["display_name"] or "",
        added_at=_parse_dt(row["added_at"]),
        last_fetched_at=_parse_dt(row["last_fetched_at"]),
    )


def _row_to_tweet(row: sqlite3.Row) -> Tweet:
    pm_raw = row["public_metrics"] or "{}"
    try:
        pm = json.loads(pm_raw)
    except json.JSONDecodeError:
        pm = {}
    return Tweet(
        id=row["id"],
        account_handle=row["account_handle"],
        text=row["text"],
        created_at=_parse_dt(row["created_at"]) or datetime.min,
        public_metrics=pm,
        quote_tweet_id=row["quote_tweet_id"] if "quote_tweet_id" in row.keys() else None,
        quote_tweet_text=row["quote_tweet_text"] if "quote_tweet_text" in row.keys() else None,
        quote_tweet_author_id=(
            row["quote_tweet_author_id"]
            if "quote_tweet_author_id" in row.keys()
            else None
        ),
        source_image_url=(
            row["source_image_url"] if "source_image_url" in row.keys() else None
        ),
        fetched_at=_parse_dt(row["fetched_at"]),
        status=row["status"],
    )


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        name=row["name"],
        url=row["url"],
        description=row["description"] or "",
        tags=[t for t in (row["tags"] or "").split(",") if t],
    )


def _row_to_draft(row: sqlite3.Row) -> Draft:
    image_paths: list[str] = []
    raw_paths = row["image_paths"]
    if raw_paths:
        try:
            image_paths = list(json.loads(raw_paths))
        except json.JSONDecodeError:
            image_paths = []
    return Draft(
        id=row["id"],
        source_tweet_id=row["source_tweet_id"],
        body=row["body"],
        link_url=row["link_url"],
        quote_tweet_id=(
            row["quote_tweet_id"] if "quote_tweet_id" in row.keys() else None
        ),
        writing_mode=(
            row["writing_mode"]
            if "writing_mode" in row.keys() and row["writing_mode"]
            else "rephrase"
        ),
        image_paths=image_paths,
        tone=row["tone"] or "",
        status=row["status"],
        created_at=_parse_dt(row["created_at"]),
        finalized_at=_parse_dt(row["finalized_at"]),
        scheduled_at=_parse_dt(row["scheduled_at"]),
        posted_at=_parse_dt(row["posted_at"]),
        x_tweet_id=row["x_tweet_id"],
        x_reply_id=row["x_reply_id"],
        cost_usd=row["cost_usd"],
        error=row["error"],
    )


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    return Schedule(
        id=row["id"],
        draft_id=row["draft_id"],
        fire_at=_parse_dt(row["fire_at"]) or datetime.min,
        status=row["status"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        created_at=_parse_dt(row["created_at"]),
    )


def _parse_dt(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # SQLite stores ISO-ish strings; tolerate the "T" separator and
        # a trailing "Z".
        s = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


# ---- bootstrap --------------------------------------------------------------

class Database:
    """High-level facade. Wraps a single sqlite3.Connection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = connect(path)
        apply_schema(self._conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    # ---- accounts ----
    def upsert_account(self, handle: str, user_id: str, display_name: str = "") -> None:
        self._conn.execute(
            """
            INSERT INTO accounts(handle, user_id, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                user_id=excluded.user_id,
                display_name=COALESCE(NULLIF(excluded.display_name, ''), accounts.display_name)
            """,
            (handle, user_id, display_name),
        )

    def mark_account_fetched(self, handle: str, at: datetime | None = None) -> None:
        at = at or datetime.now()
        self._conn.execute(
            "UPDATE accounts SET last_fetched_at = ? WHERE handle = ?",
            (at.isoformat(sep=" "), handle),
        )

    def list_accounts(self) -> list[Account]:
        rows = self._conn.execute(
            "SELECT * FROM accounts ORDER BY handle"
        ).fetchall()
        return [_row_to_account(r) for r in rows]

    # ---- tweets ----
    def upsert_tweets(
        self,
        account_handle: str,
        tweets: Iterable[dict],
    ) -> int:
        """Insert tweets if they don't exist. Returns the count of NEW rows."""
        new_count = 0
        for t in tweets:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO tweets
                    (id, account_handle, text, created_at, public_metrics,
                     quote_tweet_id, quote_tweet_text, quote_tweet_author_id,
                     source_image_url, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
                """,
                (
                    t["id"],
                    account_handle,
                    t["text"],
                    t["created_at"],
                    json.dumps(t.get("public_metrics", {})),
                    t.get("quote_tweet_id"),
                    t.get("quote_tweet_text"),
                    t.get("quote_tweet_author_id"),
                    t.get("source_image_url"),
                ),
            )
            if cur.rowcount > 0:
                new_count += 1
            else:
                # Refresh metadata without changing the user's review state.
                self._conn.execute(
                    """
                    UPDATE tweets SET text=?, created_at=?, public_metrics=?,
                        quote_tweet_id=?, quote_tweet_text=?,
                        quote_tweet_author_id=?, source_image_url=?
                    WHERE id=?
                    """,
                    (
                        t["text"], t["created_at"],
                        json.dumps(t.get("public_metrics", {})),
                        t.get("quote_tweet_id"), t.get("quote_tweet_text"),
                        t.get("quote_tweet_author_id"), t.get("source_image_url"),
                        t["id"],
                    ),
                )
        return new_count

    def list_tweets(self, status: str | None = None, limit: int = 200) -> list[Tweet]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM tweets WHERE status = ? ORDER BY fetched_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tweets ORDER BY fetched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_tweet(r) for r in rows]

    def get_tweet(self, tweet_id: str) -> Tweet | None:
        row = self._conn.execute(
            "SELECT * FROM tweets WHERE id = ?", (tweet_id,)
        ).fetchone()
        return _row_to_tweet(row) if row else None

    def set_tweet_status(self, tweet_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE tweets SET status = ? WHERE id = ?", (status, tweet_id)
        )

    def set_tweet_statuses(self, tweet_ids: Iterable[str], status: str) -> int:
        ids = list(tweet_ids)
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE tweets SET status = ? WHERE id IN ({placeholders})",
            (status, *ids),
        )
        return cur.rowcount

    # ---- projects ----
    def replace_projects(self, projects: Iterable[dict]) -> None:
        rows = [
            (
                p["name"],
                p["url"],
                p.get("description", ""),
                ",".join(p.get("tags") or []),
            )
            for p in projects
        ]
        with self._conn:  # implicit transaction
            self._conn.execute("DELETE FROM projects")
            self._conn.executemany(
                "INSERT INTO projects(name, url, description, tags) VALUES (?, ?, ?, ?)",
                rows,
            )

    def list_projects(self) -> list[Project]:
        rows = self._conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [_row_to_project(r) for r in rows]

    # ---- drafts ----
    def create_draft(self, draft: Draft) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO drafts
                (source_tweet_id, body, link_url, quote_tweet_id, writing_mode,
                 image_paths, tone, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft.source_tweet_id,
                draft.body,
                draft.link_url,
                draft.quote_tweet_id,
                draft.writing_mode,
                json.dumps(draft.image_paths),
                draft.tone,
                draft.status,
            ),
        )
        return int(cur.lastrowid)

    def get_draft(self, draft_id: int) -> Draft | None:
        row = self._conn.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        return _row_to_draft(row) if row else None

    def list_drafts(self, status: str | None = None, limit: int = 50) -> list[Draft]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM drafts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_draft(r) for r in rows]

    def update_draft(self, draft: Draft) -> None:
        if draft.id is None:
            raise ValueError("cannot update a draft without an id")
        self._conn.execute(
            """
            UPDATE drafts SET
                body=?, link_url=?, quote_tweet_id=?, writing_mode=?,
                image_paths=?, tone=?, status=?,
                finalized_at=?, scheduled_at=?, posted_at=?,
                x_tweet_id=?, x_reply_id=?, cost_usd=?, error=?
            WHERE id=?
            """,
            (
                draft.body,
                draft.link_url,
                draft.quote_tweet_id,
                draft.writing_mode,
                json.dumps(draft.image_paths),
                draft.tone,
                draft.status,
                draft.finalized_at.isoformat(sep=" ") if draft.finalized_at else None,
                draft.scheduled_at.isoformat(sep=" ") if draft.scheduled_at else None,
                draft.posted_at.isoformat(sep=" ") if draft.posted_at else None,
                draft.x_tweet_id,
                draft.x_reply_id,
                draft.cost_usd,
                draft.error,
                draft.id,
            ),
        )

    def delete_draft(self, draft_id: int) -> None:
        self._conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))

    # ---- post_log ----
    def log_post(self, draft_id: int | None, action: str, cost: float | None,
                 result: str, detail: str) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO post_log(draft_id, action, cost_usd, result, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (draft_id, action, cost, result, detail),
        )
        return int(cur.lastrowid)

    def recent_log(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM post_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def total_session_cost(self) -> float:
        """Sum of post_log.cost_usd for the current session/process.

        The Streamlit sidebar shows both this value and the in-memory
        SessionMeter; they should reconcile.
        """
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS s FROM post_log"
        ).fetchone()
        return float(row["s"] or 0.0)

    # ---- schedules ----
    def create_schedule(self, draft_id: int, fire_at: datetime) -> int:
        cur = self._conn.execute(
            "INSERT INTO schedules(draft_id, fire_at) VALUES (?, ?)",
            (draft_id, fire_at.isoformat(sep=" ")),
        )
        return int(cur.lastrowid)

    def list_schedules(
        self, status: str | None = None, limit: int = 50
    ) -> list[Schedule]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM schedules WHERE status = ? ORDER BY fire_at LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM schedules ORDER BY fire_at LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_schedule(r) for r in rows]

    def next_pending_schedule(self) -> Schedule | None:
        row = self._conn.execute(
            "SELECT * FROM schedules WHERE status='pending' "
            "ORDER BY fire_at LIMIT 1"
        ).fetchone()
        return _row_to_schedule(row) if row else None

    def mark_schedule_fired(self, schedule_id: int) -> None:
        self._conn.execute(
            "UPDATE schedules SET status='fired' WHERE id=?", (schedule_id,)
        )

    def mark_schedule_failed(self, schedule_id: int, error: str) -> None:
        self._conn.execute(
            "UPDATE schedules SET status='failed', attempts=attempts+1, "
            "last_error=? WHERE id=?",
            (error, schedule_id),
        )

    def mark_schedule_pending_late(self, schedule_id: int) -> None:
        self._conn.execute(
            "UPDATE schedules SET attempts=attempts+1, last_error='fired late' "
            "WHERE id=?",
            (schedule_id,),
        )

    def reschedule(self, schedule_id: int, fire_at: datetime) -> None:
        self._conn.execute(
            "UPDATE schedules SET fire_at=?, status='pending', last_error=NULL "
            "WHERE id=?",
            (fire_at.isoformat(sep=" "), schedule_id),
        )

    def cancel_schedule(self, schedule_id: int) -> None:
        self._conn.execute(
            "UPDATE schedules SET status='cancelled' WHERE id=?", (schedule_id,)
        )

    # ---- media_uploads ----
    def register_media_upload(self, entry: MediaUpload) -> int:
        """Insert or update by local_path. Returns the row id.

        If a row with the same `local_path` already exists, refresh
        `filename`, `mime`, `size` (but NOT the cached `x_media_id` —
        that field is only updated on a real upload).
        """
        if entry.created_at is None:
            entry.created_at = datetime.now()
        existing = self.get_media_upload_by_path(entry.local_path)
        if existing is None:
            cur = self._conn.execute(
                """
                INSERT INTO media_uploads
                    (local_path, filename, x_media_id, x_media_id_uploaded_at,
                     mime, size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.local_path,
                    entry.filename,
                    entry.x_media_id,
                    entry.x_media_id_uploaded_at.isoformat(sep=" ")
                    if entry.x_media_id_uploaded_at
                    else None,
                    entry.mime,
                    entry.size,
                    entry.created_at.isoformat(sep=" "),
                ),
            )
            return int(cur.lastrowid)
        # Refresh metadata; preserve any existing x_media_id.
        self._conn.execute(
            """
            UPDATE media_uploads SET
                filename = ?,
                mime = ?,
                size = ?
            WHERE local_path = ?
            """,
            (entry.filename, entry.mime, entry.size, entry.local_path),
        )
        return existing.id or 0

    def update_media_upload_x_id(
        self, local_path: str, x_media_id: str, uploaded_at: datetime
    ) -> None:
        self._conn.execute(
            """
            UPDATE media_uploads SET
                x_media_id = ?,
                x_media_id_uploaded_at = ?
            WHERE local_path = ?
            """,
            (x_media_id, uploaded_at.isoformat(sep=" "), local_path),
        )

    def get_media_upload_by_path(self, local_path: str) -> MediaUpload | None:
        row = self._conn.execute(
            "SELECT * FROM media_uploads WHERE local_path = ?", (local_path,)
        ).fetchone()
        if row is None:
            return None
        return MediaUpload(
            id=row["id"],
            local_path=row["local_path"],
            filename=row["filename"] or "",
            x_media_id=row["x_media_id"],
            x_media_id_uploaded_at=_parse_dt(row["x_media_id_uploaded_at"]),
            mime=row["mime"],
            size=row["size"],
            created_at=_parse_dt(row["created_at"]),
        )

    def list_media_uploads(self, limit: int = 50) -> list[MediaUpload]:
        rows = self._conn.execute(
            "SELECT * FROM media_uploads ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            MediaUpload(
                id=r["id"],
                local_path=r["local_path"],
                filename=r["filename"] or "",
                x_media_id=r["x_media_id"],
                x_media_id_uploaded_at=_parse_dt(r["x_media_id_uploaded_at"]),
                mime=r["mime"],
                size=r["size"],
                created_at=_parse_dt(r["created_at"]),
            )
            for r in rows
        ]
