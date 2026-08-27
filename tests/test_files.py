"""File validation tests."""
from __future__ import annotations

from pathlib import Path

from x_auto.utils.files import (
    MAX_IMAGE_BYTES,
    mime_from_extension,
    safe_filename,
    validate_image,
)


def test_mime_from_extension():
    assert mime_from_extension("photo.jpg") == "image/jpeg"
    assert mime_from_extension("photo.jpeg") == "image/jpeg"
    assert mime_from_extension("photo.png") == "image/png"
    assert mime_from_extension("photo.gif") == "image/gif"
    assert mime_from_extension("photo.webp") == "image/webp"
    assert mime_from_extension("photo.txt") == ""


def test_validate_image_happy(tmp_path: Path):
    p = tmp_path / "ok.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    v = validate_image(p)
    assert v.ok
    assert v.mime == "image/png"
    assert v.size > 0


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
