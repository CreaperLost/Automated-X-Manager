"""Pydantic models mirroring the SQLite tables."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

TweetStatus = Literal["new", "selected", "archived"]
DraftStatus = Literal["draft", "final", "scheduled", "posted", "failed"]
PostLogResult = Literal["success", "failed", "rate_limited", "auth_error"]
ScheduleStatus = Literal["pending", "fired", "failed", "cancelled"]


class Account(BaseModel):
    handle: str
    user_id: str
    display_name: str = ""
    added_at: datetime | None = None
    last_fetched_at: datetime | None = None


class Tweet(BaseModel):
    id: str
    account_handle: str
    text: str
    created_at: datetime
    public_metrics: dict[str, int] = Field(default_factory=dict)
    fetched_at: datetime | None = None
    status: TweetStatus = "new"


class Project(BaseModel):
    name: str
    url: HttpUrl
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    id: int | None = None
    source_tweet_id: str | None = None
    body: str
    link_url: str | None = None
    image_paths: list[str] = Field(default_factory=list)
    tone: str = ""
    status: DraftStatus = "draft"
    created_at: datetime | None = None
    finalized_at: datetime | None = None
    scheduled_at: datetime | None = None
    posted_at: datetime | None = None
    x_tweet_id: str | None = None
    x_reply_id: str | None = None
    cost_usd: float | None = None
    error: str | None = None


class PostLogEntry(BaseModel):
    id: int | None = None
    draft_id: int | None = None
    action: str = ""
    cost_usd: float | None = None
    result: PostLogResult | None = None
    detail: str = ""
    created_at: datetime | None = None


class Schedule(BaseModel):
    id: int | None = None
    draft_id: int
    fire_at: datetime
    status: ScheduleStatus = "pending"
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime | None = None


# How long an X media_id stays valid for re-use. X's docs say
# media_ids are good for at most 24 hours; we treat anything older
# as expired and re-upload.
MEDIA_ID_TTL_SECONDS = 24 * 60 * 60


class MediaUpload(BaseModel):
    """A local image cache entry that may or may not be uploaded to X yet.

    `local_path` is the absolute path under data/media_cache/.
    `x_media_id` is None until the first time the image is actually
    posted. After that, it stays valid for ~24h per X's API docs;
    the publish flow re-uses it when still fresh and re-uploads when
    expired.
    """

    id: int | None = None
    local_path: str
    filename: str
    x_media_id: str | None = None
    x_media_id_uploaded_at: datetime | None = None
    mime: str | None = None
    size: int | None = None
    created_at: datetime | None = None

    @property
    def is_uploaded(self) -> bool:
        return bool(self.x_media_id and self.x_media_id_uploaded_at)

    @property
    def is_still_valid(self) -> bool:
        """True if x_media_id is set and was uploaded within the TTL window."""
        if not self.is_uploaded:
            return False
        age = (datetime.now() - self.x_media_id_uploaded_at).total_seconds()  # type: ignore[operator]
        return age < MEDIA_ID_TTL_SECONDS
