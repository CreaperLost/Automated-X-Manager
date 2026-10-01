//! File validation: image size, MIME, and path safety.
//!
//! Ported from `src/x_auto/utils/files.py`.
//!
//! Like the original, this trusts the file extension rather than sniffing
//! magic bytes: inputs are local files the user picked, not untrusted
//! uploads.

use std::path::Path;

pub const MAX_IMAGE_BYTES: u64 = 5 * 1024 * 1024;
pub const MAX_VIDEO_BYTES: u64 = 512 * 1024 * 1024;
pub const MAX_IMAGES_PER_POST: usize = 4;

pub const ALLOWED_IMAGE_MIMES: &[&str] =
    &["image/avif", "image/jpeg", "image/png", "image/gif", "image/webp"];
pub const ALLOWED_VIDEO_MIMES: &[&str] = &["video/mp4", "video/quicktime", "video/webm"];

#[derive(Debug, Clone, PartialEq)]
pub struct FileValidation {
    pub ok: bool,
    pub reason: String,
    pub mime: String,
    pub size: u64,
}

impl FileValidation {
    fn fail(reason: String, mime: String, size: u64) -> Self {
        Self { ok: false, reason, mime, size }
    }
    fn ok(mime: String, size: u64) -> Self {
        Self { ok: true, reason: String::new(), mime, size }
    }
}

/// MIME from extension. Returns `""` for unknown extensions.
pub fn mime_from_extension(filename: &str) -> String {
    let ext = Path::new(filename)
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    match ext.as_str() {
        "jpg" | "jpeg" => "image/jpeg",
        "png" => "image/png",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "avif" => "image/avif",
        "mp4" => "video/mp4",
        "mov" => "video/quicktime",
        "webm" => "video/webm",
        _ => "",
    }
    .to_string()
}

/// Validate a local image accepted by the app.
pub fn validate_image(path: &Path) -> FileValidation {
    if !path.exists() || !path.is_file() {
        return FileValidation::fail(
            format!("file not found: {}", path.display()),
            String::new(),
            0,
        );
    }
    let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    if size == 0 {
        return FileValidation::fail(format!("file is empty: {}", path.display()), String::new(), 0);
    }
    if size > MAX_IMAGE_BYTES {
        return FileValidation::fail(
            format!("image too large: {size} bytes (max {MAX_IMAGE_BYTES})"),
            String::new(),
            size,
        );
    }
    let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let mime = mime_from_extension(&name);
    if !ALLOWED_IMAGE_MIMES.contains(&mime.as_str()) {
        let mut allowed: Vec<&str> = ALLOWED_IMAGE_MIMES.to_vec();
        allowed.sort_unstable();
        return FileValidation::fail(
            format!("unsupported image type '{mime}'; allowed: {allowed:?}"),
            mime,
            size,
        );
    }
    FileValidation::ok(mime, size)
}

/// Validate a local video for X's chunked upload flow.
pub fn validate_video(path: &Path) -> FileValidation {
    if !path.exists() || !path.is_file() {
        return FileValidation::fail(
            format!("file not found: {}", path.display()),
            String::new(),
            0,
        );
    }
    let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    if size == 0 {
        return FileValidation::fail(format!("file is empty: {}", path.display()), String::new(), 0);
    }
    if size > MAX_VIDEO_BYTES {
        return FileValidation::fail(
            format!("video too large: {size} bytes (max {MAX_VIDEO_BYTES})"),
            String::new(),
            size,
        );
    }
    let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let mime = mime_from_extension(&name);
    if !ALLOWED_VIDEO_MIMES.contains(&mime.as_str()) {
        let mut allowed: Vec<&str> = ALLOWED_VIDEO_MIMES.to_vec();
        allowed.sort_unstable();
        return FileValidation::fail(
            format!("unsupported video type '{mime}'; allowed: {allowed:?}"),
            mime,
            size,
        );
    }
    FileValidation::ok(mime, size)
}

/// Whether a path has a supported video extension.
pub fn is_video_path(path: impl AsRef<Path>) -> bool {
    let name = path.as_ref().file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    ALLOWED_VIDEO_MIMES.contains(&mime_from_extension(&name).as_str())
}

/// Path-traversal-safe filename: keep only the final component and
/// neutralize `..`. Both separators are treated as separators on every OS.
pub fn safe_filename(name: &str) -> String {
    let normalized = name.replace('\\', "/");
    let base = normalized.rsplit('/').next().unwrap_or("");
    base.replace("..", "_").replace('/', "_")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mime_mapping_covers_supported_types() {
        assert_eq!(mime_from_extension("a.JPG"), "image/jpeg");
        assert_eq!(mime_from_extension("a.jpeg"), "image/jpeg");
        assert_eq!(mime_from_extension("a.png"), "image/png");
        assert_eq!(mime_from_extension("a.avif"), "image/avif");
        assert_eq!(mime_from_extension("a.mov"), "video/quicktime");
        assert_eq!(mime_from_extension("a.exe"), "");
        assert_eq!(mime_from_extension("noext"), "");
    }

    #[test]
    fn rejects_missing_file() {
        let v = validate_image(Path::new("/definitely/not/here.png"));
        assert!(!v.ok);
        assert!(v.reason.contains("file not found"));
    }

    #[test]
    fn rejects_empty_file() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("empty.png");
        std::fs::write(&p, b"").unwrap();
        let v = validate_image(&p);
        assert!(!v.ok);
        assert!(v.reason.contains("empty"));
    }

    #[test]
    fn rejects_unsupported_type() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("doc.txt");
        std::fs::write(&p, b"hello").unwrap();
        let v = validate_image(&p);
        assert!(!v.ok);
        assert!(v.reason.contains("unsupported image type"));
    }

    #[test]
    fn accepts_valid_image() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("ok.png");
        std::fs::write(&p, b"12345").unwrap();
        let v = validate_image(&p);
        assert!(v.ok, "{}", v.reason);
        assert_eq!(v.mime, "image/png");
        assert_eq!(v.size, 5);
    }

    #[test]
    fn rejects_oversized_image() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("big.png");
        let f = std::fs::File::create(&p).unwrap();
        f.set_len(MAX_IMAGE_BYTES + 1).unwrap();
        drop(f);
        let v = validate_image(&p);
        assert!(!v.ok);
        assert!(v.reason.contains("too large"));
    }

    #[test]
    fn is_video_path_detects_by_extension() {
        assert!(is_video_path("a.mp4"));
        assert!(is_video_path(Path::new("/x/y/a.webm")));
        assert!(!is_video_path("a.png"));
    }

    #[test]
    fn safe_filename_strips_traversal() {
        assert_eq!(safe_filename("../../etc/passwd"), "passwd");
        assert_eq!(safe_filename("C:\\temp\\evil.png"), "evil.png");
        // "....png": the two ".." pairs each become "_"; no dot survives.
        assert_eq!(safe_filename("....png"), "__png");
        assert_eq!(safe_filename("normal.png"), "normal.png");
    }
}