"""Image library + X media_id caching."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from x_auto.store.models import MEDIA_ID_TTL_SECONDS, MediaUpload
from x_auto.store.repos import Database


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(tmp_path / "state.db")
    yield d
    d.close()


class TestMediaUploadModel:
    def test_is_still_valid_fresh(self):
        u = MediaUpload(
            local_path="/tmp/a.png",
            filename="a.png",
            x_media_id="12345",
            x_media_id_uploaded_at=datetime.now(),
        )
        assert u.is_uploaded
        assert u.is_still_valid

    def test_is_still_valid_expired(self):
        u = MediaUpload(
            local_path="/tmp/a.png",
            filename="a.png",
            x_media_id="12345",
            x_media_id_uploaded_at=datetime.now() - timedelta(seconds=MEDIA_ID_TTL_SECONDS + 1),
        )
        assert u.is_uploaded
        assert not u.is_still_valid

    def test_not_uploaded_is_not_valid(self):
        u = MediaUpload(
            local_path="/tmp/a.png",
            filename="a.png",
            x_media_id=None,
        )
        assert not u.is_uploaded
        assert not u.is_still_valid


class TestMediaUploadRepo:
    def test_register_then_get(self, db: Database):
        entry = MediaUpload(
            local_path=str(Path("C:/cache/a.png").resolve()),
            filename="a.png",
            mime="image/png",
            size=2048,
        )
        new_id = db.register_media_upload(entry)
        assert new_id > 0
        loaded = db.get_media_upload_by_path(entry.local_path)
        assert loaded is not None
        assert loaded.id == new_id
        assert loaded.filename == "a.png"
        assert loaded.x_media_id is None

    def test_register_preserves_x_media_id_on_re_register(self, db: Database):
        path = str(Path("C:/cache/b.png").resolve())
        db.register_media_upload(MediaUpload(
            local_path=path, filename="b.png", mime="image/png", size=1024,
        ))
        db.update_media_upload_x_id(path, "abc-123", datetime.now())
        # Re-register the same path with a new filename; the x_media_id
        # must survive.
        db.register_media_upload(MediaUpload(
            local_path=path, filename="b-renamed.png", mime="image/png", size=2048,
        ))
        loaded = db.get_media_upload_by_path(path)
        assert loaded is not None
        assert loaded.x_media_id == "abc-123"
        assert loaded.filename == "b-renamed.png"
        assert loaded.size == 2048

    def test_list_orders_by_created_desc(self, db: Database):
        for i in range(3):
            db.register_media_upload(MediaUpload(
                local_path=f"/tmp/c{i}.png",
                filename=f"c{i}.png",
            ))
        rows = db.list_media_uploads(limit=10)
        assert [r.filename for r in rows] == ["c2.png", "c1.png", "c0.png"]
