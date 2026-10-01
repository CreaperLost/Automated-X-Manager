//! Models mirroring the SQLite tables.
//!
//! Ported from `src/x_auto/store/models.py` (Pydantic -> serde).
//! Field names and JSON shapes are kept identical so the existing
//! `state.db` files read without migration.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

pub const TWEET_STATUSES: [&str; 3] = ["new", "selected", "archived"];
pub const WRITING_MODES: [&str; 2] = ["rephrase", "original_take"];
pub const DRAFT_STATUSES: [&str; 4] = ["draft", "final", "posted", "failed"];
pub const POST_LOG_RESULTS: [&str; 4] = ["success", "failed", "rate_limited", "auth_error"];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Account {
    pub handle: String,
    pub user_id: String,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub added_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub last_fetched_at: Option<DateTime<Utc>>,
}

/// X's `public_metrics` is a flat string->int map. Preserved verbatim.
pub type PublicMetrics = std::collections::BTreeMap<String, i64>;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Tweet {
    pub id: String,
    pub account_handle: String,
    pub text: String,
    pub created_at: DateTime<Utc>,
    #[serde(default)]
    pub public_metrics: PublicMetrics,
    #[serde(default)]
    pub quote_tweet_id: Option<String>,
    #[serde(default)]
    pub quote_tweet_text: Option<String>,
    #[serde(default)]
    pub quote_tweet_author_id: Option<String>,
    #[serde(default)]
    pub source_image_url: Option<String>,
    #[serde(default)]
    pub fetched_at: Option<DateTime<Utc>>,
    #[serde(default = "default_tweet_status")]
    pub status: String,
}

fn default_tweet_status() -> String {
    "new".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Project {
    pub name: String,
    pub url: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Draft {
    #[serde(default)]
    pub id: Option<i64>,
    #[serde(default)]
    pub source_tweet_id: Option<String>,
    pub body: String,
    #[serde(default)]
    pub link_url: Option<String>,
    #[serde(default)]
    pub quote_tweet_id: Option<String>,
    #[serde(default = "default_writing_mode")]
    pub writing_mode: String,
    #[serde(default)]
    pub image_paths: Vec<String>,
    #[serde(default)]
    pub tone: String,
    #[serde(default = "default_draft_status")]
    pub status: String,
    #[serde(default)]
    pub created_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub finalized_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub posted_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub x_tweet_id: Option<String>,
    #[serde(default)]
    pub x_reply_id: Option<String>,
    #[serde(default)]
    pub cost_usd: Option<f64>,
    #[serde(default)]
    pub error: Option<String>,
}

fn default_writing_mode() -> String {
    "rephrase".to_string()
}
fn default_draft_status() -> String {
    "draft".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PostLogEntry {
    #[serde(default)]
    pub id: Option<i64>,
    #[serde(default)]
    pub draft_id: Option<i64>,
    #[serde(default)]
    pub action: String,
    #[serde(default)]
    pub cost_usd: Option<f64>,
    #[serde(default)]
    pub result: Option<String>,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub created_at: Option<DateTime<Utc>>,
}

/// How long an X `media_id` stays valid for re-use. X's docs say
/// `media_id`s are good for at most 24 hours; anything older is
/// treated as expired and re-uploaded.
pub const MEDIA_ID_TTL_SECONDS: i64 = 24 * 60 * 60;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MediaUpload {
    #[serde(default)]
    pub id: Option<i64>,
    pub local_path: String,
    pub filename: String,
    #[serde(default)]
    pub x_media_id: Option<String>,
    #[serde(default)]
    pub x_media_id_uploaded_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub mime: Option<String>,
    #[serde(default)]
    pub size: Option<i64>,
    #[serde(default)]
    pub created_at: Option<DateTime<Utc>>,
}

impl MediaUpload {
    pub fn is_uploaded(&self) -> bool {
        self.x_media_id.is_some() && self.x_media_id_uploaded_at.is_some()
    }

    /// True if `x_media_id` is set and was uploaded within the TTL window.
    pub fn is_still_valid(&self) -> bool {
        if !self.is_uploaded() {
            return false;
        }
        let uploaded = match self.x_media_id_uploaded_at {
            Some(t) => t,
            None => return false,
        };
        let age = (Utc::now() - uploaded).num_seconds();
        age < MEDIA_ID_TTL_SECONDS
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn upload_with_age(seconds: i64) -> MediaUpload {
        MediaUpload {
            id: Some(1),
            local_path: "/tmp/a.png".into(),
            filename: "a.png".into(),
            x_media_id: Some("123".into()),
            x_media_id_uploaded_at: Some(Utc::now() - chrono::Duration::seconds(seconds)),
            mime: Some("image/png".into()),
            size: Some(10),
            created_at: None,
        }
    }

    #[test]
    fn media_ttl_boundary() {
        assert!(upload_with_age(0).is_still_valid());
        assert!(upload_with_age(MEDIA_ID_TTL_SECONDS - 60).is_still_valid());
        assert!(!upload_with_age(MEDIA_ID_TTL_SECONDS + 60).is_still_valid());
    }

    #[test]
    fn media_without_id_is_never_valid() {
        let mut m = upload_with_age(0);
        m.x_media_id = None;
        assert!(!m.is_uploaded());
        assert!(!m.is_still_valid());
    }

    #[test]
    fn draft_defaults_match_python_models() {
        let d: Draft = serde_json::from_str(r#"{"body":"hi"}"#).unwrap();
        assert_eq!(d.writing_mode, "rephrase");
        assert_eq!(d.status, "draft");
        assert!(d.image_paths.is_empty());
    }
}