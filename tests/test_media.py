"""Tests for image preparation before X media upload."""

import json
from io import BytesIO

import httpx
import pytest
import respx
from PIL import Image

from x_auto.x.client import API_BASE
from x_auto.x.media import (
    MediaUploadError,
    _prepare_image_upload,
    upload_video_sync,
)


def test_prepare_avif_transcodes_to_jpeg(tmp_path):
    path = tmp_path / "project-image.avif"
    Image.new("RGB", (32, 24), (40, 80, 120)).save(path, format="AVIF")

    prepared = _prepare_image_upload(path)

    assert prepared.filename == "project-image.jpg"
    assert prepared.mime == "image/jpeg"
    assert prepared.content.startswith(b"\xff\xd8")
    with Image.open(BytesIO(prepared.content)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (32, 24)


def test_prepare_avif_flattens_transparency_on_white(tmp_path):
    path = tmp_path / "transparent.avif"
    Image.new("RGBA", (8, 8), (255, 0, 0, 0)).save(path, format="AVIF")

    prepared = _prepare_image_upload(path)

    with Image.open(BytesIO(prepared.content)) as decoded:
        red, green, blue = decoded.convert("RGB").getpixel((0, 0))
    assert red > 240 and green > 240 and blue > 240


def test_prepare_invalid_avif_has_clear_local_error(tmp_path):
    path = tmp_path / "broken.avif"
    path.write_bytes(b"not an avif")

    with pytest.raises(MediaUploadError, match="could not decode AVIF"):
        _prepare_image_upload(path)


def test_video_uses_initialize_append_finalize_and_status(tmp_path, monkeypatch):
    path = tmp_path / "launch.mp4"
    path.write_bytes(b"video-payload")

    class Tokens:
        @staticmethod
        def access_token():
            return "test-token"

    monkeypatch.setattr("x_auto.x.media.time.sleep", lambda _seconds: None)
    with respx.mock(base_url=API_BASE, assert_all_called=True) as mock:
        initialize = mock.post("/media/upload/initialize").mock(
            return_value=httpx.Response(200, json={"data": {"id": "video-123"}})
        )
        append = mock.post("/media/upload/video-123/append").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        finalize = mock.post("/media/upload/video-123/finalize").mock(
            return_value=httpx.Response(200, json={
                "data": {
                    "id": "video-123",
                    "processing_info": {"state": "pending", "check_after_secs": 1},
                }
            })
        )
        status = mock.get("/media/upload").mock(
            return_value=httpx.Response(200, json={
                "data": {"processing_info": {"state": "succeeded"}}
            })
        )

        media_id = upload_video_sync(path, token_manager=Tokens())

    assert media_id == "video-123"
    assert initialize.call_count == append.call_count == finalize.call_count == 1
    assert status.call_count == 1
    init_body = json.loads(initialize.calls[0].request.content)
    assert init_body == {
        "media_category": "tweet_video",
        "media_type": "video/mp4",
        "total_bytes": len(b"video-payload"),
    }
    append_body = json.loads(append.calls[0].request.content)
    assert append_body["segment_index"] == 0
    assert append_body["media"] == "dmlkZW8tcGF5bG9hZA=="
