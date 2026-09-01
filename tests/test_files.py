"""File validation tests."""
from __future__ import annotations

from pathlib import Path

from x_auto.utils.files import (
    MAX_IMAGE_BYTES,
    is_video_path,
    mime_from_extension,
    safe_filename,
    validate_image,
    validate_video,
)


def test_mime_from_extension():
    assert mime_from_extension("photo.jpg") == "image/jpeg"
    assert mime_from_extension("photo.jpeg") == "image/jpeg"
    assert mime_from_extension("photo.png") == "image/png"
    assert mime_from_extension("photo.gif") == "image/gif"
    assert mime_from_extension("photo.webp") == "image/webp"
    assert mime_from_extension("photo.avif") == "image/avif"
    assert mime_from_extension("clip.mp4") == "video/mp4"
    assert mime_from_extension("clip.mov") == "video/quicktime"
    assert mime_from_extension("clip.webm") == "video/webm"
    assert mime_from_extension("photo.txt") == ""


def test_validate_avif_image(tmp_path: Path):
    p = tmp_path / "ok.avif"
    p.write_bytes(b"avif")
    validation = validate_image(p)
    assert validation.ok
    assert validation.mime == "image/avif"


def test_validate_image_happy(tmp_path: Path):
    p = tmp_path / "ok.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    v = validate_image(p)
    assert v.ok
    assert v.mime == "image/png"
    assert v.size > 0


def test_validate_video_happy(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video bytes")
    validation = validate_video(path)
    assert validation.ok
    assert validation.mime == "video/mp4"
    assert is_video_path(path)
    assert not is_video_path("photo.png")


def test_validate_image_too_large(tmp_path: Path):
    p = tmp_path / "big.jpg"
    p.write_bytes(b"\xff\xd8" + b"\x00" * (MAX_IMAGE_BYTES + 1))
    v = validate_image(p)
    assert not v.ok
    assert "too large" in v.reason


def test_validate_image_wrong_mime(tmp_path: Path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4" + b"\x00" * 100)
    v = validate_image(p)
    assert not v.ok
    assert "unsupported" in v.reason


def test_validate_image_empty(tmp_path: Path):
    p = tmp_path / "empty.png"
    p.write_bytes(b"")
    v = validate_image(p)
    assert not v.ok
    assert "empty" in v.reason


def test_validate_image_missing(tmp_path: Path):
    p = tmp_path / "nope.png"
    v = validate_image(p)
    assert not v.ok
    assert "not found" in v.reason


def test_safe_filename_strips_paths():
    assert safe_filename("../../etc/passwd") == "passwd"
    # Path.name strips both / and \ on Windows. After the strip, only
    # the file component is left; the function then escapes ".." in it.
    assert safe_filename("a/b\\c.png") == "c.png"
    assert safe_filename("..secret") == "_secret"
