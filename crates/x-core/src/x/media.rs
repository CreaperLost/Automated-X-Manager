//! Media upload for X posts.
//!
//! Ported from `src/x_auto/x/media.py`.
//!
//! Images use the v2 one-shot endpoint. Videos use X's asynchronous
//! v2 initialize/append/finalize/status flow.
//!
//! Limits (verified Aug 2026):
//!   * up to 4 images per post
//!   * 5 MB per image
//!   * 512 MB per video
//!   * `media_id`s expire in 24 hours
//!
//! Cached uploads: [`upload_media_cached`] consults the `media_uploads`
//! table before hitting X, reusing a `media_id` still inside the 24-hour
//! TTL. That is a latency optimization, not a cost one — uploads are free
//! — but it makes reposting the same image snappy.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use chrono::Utc;

use crate::error::{Error, Result};
use crate::store::models::MediaUpload;
use crate::store::repos::Database;
use crate::utils::files::{
    is_video_path, mime_from_extension, validate_image, validate_video, MAX_IMAGE_BYTES,
};
use crate::x::auth::TokenManager;
use crate::x::client::{API_BASE, USER_AGENT};

pub const VIDEO_CHUNK_BYTES: usize = 4 * 1024 * 1024;
pub const VIDEO_PROCESSING_TIMEOUT_SECONDS: u64 = 5 * 60;

/// An X-compatible image payload ready for multipart upload.
struct PreparedImage {
    filename: String,
    content: Vec<u8>,
    mime: String,
}

/// Read an image and transcode AVIF to JPEG, which X accepts.
fn prepare_image_upload(file_path: &Path) -> Result<PreparedImage> {
    let validation = validate_image(file_path);
    if !validation.ok {
        return Err(Error::Media(validation.reason));
    }

    if validation.mime != "image/avif" {
        let content = std::fs::read(file_path)
            .map_err(|e| Error::Media(format!("read {}: {e}", file_path.display())))?;
        let name = file_path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "image".into());
        return Ok(PreparedImage { filename: name, content, mime: validation.mime });
    }

    // AVIF -> JPEG, compositing any alpha over white.
    let decoded = image::open(file_path).map_err(|e| {
        Error::Media(format!(
            "could not decode AVIF image '{}': {e}",
            file_path.display()
        ))
    })?;
    let rgb = decoded.to_rgb8();
    let mut out: Vec<u8> = Vec::new();
    let mut encoder =
        image::codecs::jpeg::JpegEncoder::new_with_quality(std::io::Cursor::new(&mut out), 90);
    encoder
        .encode(
            rgb.as_raw(),
            rgb.width(),
            rgb.height(),
            image::ExtendedColorType::Rgb8,
        )
        .map_err(|e| Error::Media(format!("AVIF->JPEG encode failed: {e}")))?;

    if out.len() as u64 > MAX_IMAGE_BYTES {
        return Err(Error::Media(format!(
            "converted image too large: {} bytes (max {MAX_IMAGE_BYTES})",
            out.len()
        )));
    }
    let stem = file_path
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "image".into());
    Ok(PreparedImage { filename: format!("{stem}.jpg"), content: out, mime: "image/jpeg".into() })
}

/// Reject more than X's 4-image limit.
pub fn assert_image_count(count: usize) -> Result<()> {
    if count > crate::utils::files::MAX_IMAGES_PER_POST {
        return Err(Error::Media(format!(
            "too many images: {count} (max {})",
            crate::utils::files::MAX_IMAGES_PER_POST
        )));
    }
    Ok(())
}

pub fn assert_image_size(size: u64) -> Result<()> {
    if size > MAX_IMAGE_BYTES {
        return Err(Error::Media(format!(
            "image too large: {size} bytes (max {MAX_IMAGE_BYTES})"
        )));
    }
    Ok(())
}

/// Upload one image to the one-shot endpoint. Returns the `media_id`.
pub async fn upload_image(file_path: &Path, tokens: &Arc<TokenManager>) -> Result<String> {
    let prepared = prepare_image_upload(file_path)?;
    let access_token = tokens.access_token().await?;

    let part = reqwest::multipart::Part::bytes(prepared.content)
        .file_name(prepared.filename.clone())
        .mime_str(&prepared.mime)
        .map_err(|e| Error::Media(format!("build multipart: {e}")))?;
    let category = reqwest::multipart::Part::text("tweet_image")
        .file_name("media_category")
        .mime_str("text/plain")
        .map_err(|e| Error::Media(format!("build multipart: {e}")))?;

    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|e| Error::Io(format!("build http client: {e}")))?;

    let resp = http
        .post(format!("{API_BASE}/media/upload"))
        .bearer_auth(access_token)
        .header("User-Agent", USER_AGENT)
        .multipart(reqwest::multipart::Form::new().part("media", part).part("media_category", category))
        .send()
        .await
        .map_err(|e| Error::Media(format!("media upload request failed: {e}")))?;

    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if status.as_u16() >= 400 {
        let detail: String = body.chars().take(500).collect();
        return Err(Error::Media(format!(
            "media upload failed: HTTP {}: {detail}",
            status.as_u16()
        )));
    }
    let json: serde_json::Value = serde_json::from_str(&body)
        .map_err(|e| Error::Media(format!("upload response invalid JSON: {e}")))?;
    json["data"]["id"]
        .as_str()
        .map(|s| s.to_string())
        .ok_or_else(|| Error::Media(format!("no media id in response: {body}")))
}

/// Upload one video via X's chunked flow and wait for processing.
pub async fn upload_video(file_path: &Path, tokens: &Arc<TokenManager>) -> Result<String> {
    let validation = validate_video(file_path);
    if !validation.ok {
        return Err(Error::Media(validation.reason));
    }
    let access_token = tokens.access_token().await?;

    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| Error::Io(format!("build http client: {e}")))?;
    let auth = |req: reqwest::RequestBuilder| {
        req.bearer_auth(&access_token).header("User-Agent", USER_AGENT)
    };

    // 1. initialize
    let initialized = auth(http.post(format!("{API_BASE}/media/upload/initialize")))
        .json(&serde_json::json!({
            "media_category": "tweet_video",
            "media_type": validation.mime,
            "total_bytes": validation.size,
        }))
        .send()
        .await
        .map_err(|e| Error::Media(format!("video init request failed: {e}")))?;
    let init_body = checked_json(initialized, "initialization").await?;
    let media_id = init_body["data"]["id"]
        .as_str()
        .map(|s| s.to_string())
        .ok_or_else(|| Error::Media(format!("video init returned no media id: {init_body}")))?;

    // 2. append in 4 MB segments
    let bytes = std::fs::read(file_path)
        .map_err(|e| Error::Media(format!("read {}: {e}", file_path.display())))?;
    let mut segment_index = 0u32;
    for chunk in bytes.chunks(VIDEO_CHUNK_BYTES) {
        let encoded = BASE64.encode(chunk);
        let resp = auth(http.post(format!("{API_BASE}/media/upload/{media_id}/append")))
            .json(&serde_json::json!({
                "media": encoded,
                "segment_index": segment_index,
            }))
            .send()
            .await
            .map_err(|e| Error::Media(format!("video append request failed: {e}")))?;
        checked_json(resp, &format!("segment {segment_index}")).await?;
        segment_index += 1;
    }

    // 3. finalize
    let finalized = auth(http.post(format!("{API_BASE}/media/upload/{media_id}/finalize")))
        .send()
        .await
        .map_err(|e| Error::Media(format!("video finalize request failed: {e}")))?;
    let finalize_body = checked_json(finalized, "finalization").await?;

    // 4. poll processing status
    let mut processing = finalize_body["data"]["processing_info"].clone();
    let deadline = std::time::Instant::now()
        + std::time::Duration::from_secs(VIDEO_PROCESSING_TIMEOUT_SECONDS);
    while !processing.is_null() {
        let state = processing["state"].as_str().unwrap_or("").to_lowercase();
        if state == "succeeded" {
            break;
        }
        if state == "failed" {
            let detail = processing["error"].to_string();
            return Err(Error::Media(format!("X could not process video: {detail}")));
        }
        if std::time::Instant::now() >= deadline {
            return Err(Error::Media(
                "timed out waiting for X to process the video".into(),
            ));
        }
        let delay = processing["check_after_secs"].as_i64().unwrap_or(1).clamp(1, 10);
        tokio::time::sleep(std::time::Duration::from_secs(delay as u64)).await;

        let status = auth(
            http.get(format!("{API_BASE}/media/upload"))
                .query(&[("command", "STATUS"), ("media_id", media_id.as_str())]),
        )
        .send()
        .await
        .map_err(|e| Error::Media(format!("video status request failed: {e}")))?;
        let body = checked_json(status, "status check").await?;
        processing = body["data"]["processing_info"].clone();
    }

    Ok(media_id)
}

async fn checked_json(resp: reqwest::Response, phase: &str) -> Result<serde_json::Value> {
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if status.as_u16() >= 400 {
        let detail: String = body.chars().take(500).collect();
        return Err(Error::Media(format!(
            "video {phase} failed: HTTP {}: {detail}",
            status.as_u16()
        )));
    }
    serde_json::from_str(&body)
        .map_err(|_| Error::Media(format!("video {phase} returned invalid JSON")))
}

/// Upload or reuse an image, returning X's `media_id`.
///
/// Reuses a cached `media_id` only while it is inside the 24-hour TTL;
/// uploads are free, so this is purely about latency.
pub async fn upload_image_cached(
    file_path: &Path,
    tokens: &Arc<TokenManager>,
    db: &Database,
) -> Result<String> {
    let validation = validate_image(file_path);
    if !validation.ok {
        return Err(Error::Media(validation.reason));
    }
    let abs_path = canonical_key(file_path);
    if let Some(existing) = db.get_media_upload_by_path(&abs_path)? {
        if existing.is_still_valid() {
            if let Some(id) = existing.x_media_id {
                return Ok(id);
            }
        }
    }

    // Upload; on failure don't write a half-populated cache row.
    let media_id = upload_image(file_path, tokens).await?;
    persist(db, &abs_path, file_path, &media_id, &validation.mime, validation.size)?;
    Ok(media_id)
}

/// Upload or reuse an image or video from the project media library.
pub async fn upload_media_cached(
    file_path: &Path,
    tokens: &Arc<TokenManager>,
    db: &Database,
) -> Result<String> {
    if !is_video_path(file_path) {
        return upload_image_cached(file_path, tokens, db).await;
    }

    let validation = validate_video(file_path);
    if !validation.ok {
        return Err(Error::Media(validation.reason));
    }
    let abs_path = canonical_key(file_path);
    if let Some(existing) = db.get_media_upload_by_path(&abs_path)? {
        if existing.is_still_valid() {
            if let Some(id) = existing.x_media_id {
                return Ok(id);
            }
        }
    }

    let media_id = upload_video(file_path, tokens).await?;
    let mime = mime_from_extension(
        &file_path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
    );
    persist(db, &abs_path, file_path, &media_id, &mime, validation.size)?;
    Ok(media_id)
}

fn persist(
    db: &Database,
    abs_path: &str,
    file_path: &Path,
    media_id: &str,
    mime: &str,
    size: u64,
) -> Result<()> {
    let now = Utc::now();
    let filename = file_path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    db.register_media_upload(&MediaUpload {
        id: None,
        local_path: abs_path.to_string(),
        filename,
        x_media_id: Some(media_id.to_string()),
        x_media_id_uploaded_at: Some(now),
        mime: Some(mime.to_string()),
        size: Some(size as i64),
        created_at: None,
    })?;
    db.update_media_upload_x_id(abs_path, media_id, now)
}

/// Python's `Path.resolve()` as a string: absolute, symlinks resolved.
fn canonical_key(path: &Path) -> String {
    let p: PathBuf = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    p.to_string_lossy().to_string()
}

/// Resolve a possibly-relative attachment path against the data dir.
pub fn resolve_attachment(data_dir: &Path, raw: &str) -> PathBuf {
    let p = Path::new(raw);
    if p.exists() {
        return p.to_path_buf();
    }
    let alt = data_dir.join(raw);
    if alt.exists() {
        alt
    } else {
        p.to_path_buf()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::utils::files::MAX_IMAGES_PER_POST;
    use crate::x::auth::TokenStore;

    #[test]
    fn image_count_limit_matches_x() {
        assert!(assert_image_count(4).is_ok());
        let err = assert_image_count(5).unwrap_err();
        assert!(err.to_string().contains("too many images"));
        assert_eq!(MAX_IMAGES_PER_POST, 4);
    }

    #[test]
    fn image_size_limit_enforced() {
        assert!(assert_image_size(1024).is_ok());
        assert!(assert_image_size(MAX_IMAGE_BYTES + 1).is_err());
    }

    #[tokio::test]
    async fn missing_media_file_fails_validation_before_any_request() {
        let dir = tempfile::tempdir().unwrap();
        let store = TokenStore::new(&dir.path().join("t.json"));
        let mgr = Arc::new(
            TokenManager::with_store(crate::config::get_settings("crypto"), store).unwrap(),
        );
        // No such file: validation fails first, so no HTTP call is made.
        let err = upload_image(Path::new("/nope/missing.avif"), &mgr).await.unwrap_err();
        assert!(err.to_string().contains("file not found"), "{err}");
    }

    #[test]
    fn resolve_attachment_prefers_existing_absolute_path() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("a.png");
        std::fs::write(&f, b"x").unwrap();
        assert_eq!(resolve_attachment(dir.path(), f.to_string_lossy().as_ref()), f);

        // Falls back to the data dir for a bare relative name.
        let rel = f.file_name().unwrap().to_string_lossy().to_string();
        let expected = dir.path().join("a.png");
        assert_eq!(resolve_attachment(dir.path(), &rel), expected);

        // Neither exists: returns the input path unchanged.
        assert_eq!(resolve_attachment(dir.path(), "missing.png"), PathBuf::from("missing.png"));
    }

    #[test]
    fn cached_lookup_reuses_fresh_media_id_without_network() {
        let db = Database::open_in_memory().unwrap();
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("a.png");
        std::fs::write(&f, b"x").unwrap();
        let abs = canonical_key(&f);

        db.register_media_upload(&MediaUpload {
            id: None,
            local_path: abs.clone(),
            filename: "a.png".into(),
            x_media_id: Some("cached_id".into()),
            x_media_id_uploaded_at: Some(Utc::now()),
            mime: Some("image/png".into()),
            size: Some(1),
            created_at: None,
        })
        .unwrap();

        let entry = db.get_media_upload_by_path(&abs).unwrap().unwrap();
        assert!(entry.is_still_valid());
        assert_eq!(entry.x_media_id.as_deref(), Some("cached_id"));
    }
}