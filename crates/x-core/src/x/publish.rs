//! Shared "post a draft" workflow.
//!
//! Ported from `src/x_auto/publish.py`. Both the Create view and the
//! Queue view need to: upload attached media, create the main post,
//! create the CTA reply, update the draft row, and log the spend. This
//! module is the single implementation both call.

use chrono::Utc;
use serde::{Deserialize, Serialize};

use crate::config::Settings;
use crate::error::{Error, Result};
use crate::store::models::Draft;
use crate::store::repos::Database;
use crate::utils::files::is_video_path;
use crate::utils::text::validate_post_body;
use crate::x::client::XClient;
use crate::x::costs::estimate_post_cost;
use crate::x::media::{resolve_attachment, upload_media_cached};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PublishResult {
    pub x_tweet_id: String,
    pub x_reply_id: Option<String>,
    pub cost_usd: f64,
}

/// Pre-flight checks plus the main+reply publish.
pub async fn publish_draft(
    settings: &Settings,
    db: &Database,
    x_client: &XClient,
    draft: &mut Draft,
) -> Result<PublishResult> {
    // --- deterministic local rules, before any paid write ---
    let mut errors = validate_post_body(&draft.body, "main", false);
    if let Some(link) = &draft.link_url {
        errors.extend(validate_post_body(link, "reply", true));
    }
    if !errors.is_empty() {
        return Err(Error::Validation(
            errors
                .iter()
                .map(|e| e.message.clone())
                .collect::<Vec<_>>()
                .join("; "),
        ));
    }

    // X rejects API quotes of third-party posts with 403 unless the
    // authenticated user authored or is mentioned in the quoted post.
    // Sources here are monitored third-party accounts, so a legacy quote
    // id is inspiration metadata only and must never reach the write payload.
    if draft.quote_tweet_id.is_some() {
        draft.quote_tweet_id = None;
        db.update_draft(draft)?;
    }

    let video_count = draft.image_paths.iter().filter(|p| is_video_path(p)).count();
    if video_count > 0 && draft.image_paths.len() != 1 {
        return Err(Error::Validation(
            "A video must be the only media attachment.".into(),
        ));
    }

    // --- uploads (free on X) ---
    let mut media_ids: Vec<String> = Vec::new();
    for raw in &draft.image_paths {
        let path = resolve_attachment(&settings.data_dir, raw);
        media_ids.push(upload_media_cached(&path, x_client.tokens(), db).await?);
    }

    // --- main post: no inline URL, that is the whole point ---
    let x_tweet_id = x_client
        .create_post(&draft.body, Some(&media_ids), None)
        .await?;

    // --- reply carries the project link in its own cheap post ---
    let x_reply_id = match &draft.link_url {
        Some(link) if !link.trim().is_empty() => {
            Some(x_client.create_post(link, None, Some(&x_tweet_id)).await?)
        }
        _ => None,
    };

    let cost = estimate_post_cost(
        &draft.body,
        !media_ids.is_empty(),
        x_reply_id.is_some(),
        draft.link_url.as_deref().unwrap_or(""),
    );

    draft.status = "posted".into();
    draft.posted_at = Some(Utc::now());
    draft.x_tweet_id = Some(x_tweet_id.clone());
    draft.x_reply_id = x_reply_id.clone();
    draft.cost_usd = Some(cost.total);
    db.update_draft(draft)?;
    db.log_post(
        draft.id,
        "post_now",
        Some(cost.total),
        "success",
        &format!("x_tweet_id={x_tweet_id} x_reply_id={}", x_reply_id.clone().unwrap_or_default()),
    )?;

    Ok(PublishResult { x_tweet_id, x_reply_id, cost_usd: cost.total })
}

/// Estimate the cost of publishing a draft without spending anything.
/// Used for the pre-flight preview the UI shows before "Post now".
pub fn preview_publish_cost(draft: &Draft) -> crate::x::costs::CostBreakdown {
    estimate_post_cost(
        &draft.body,
        !draft.image_paths.is_empty(),
        draft.link_url.as_deref().is_some_and(|l| !l.trim().is_empty()),
        draft.link_url.as_deref().unwrap_or(""),
    )
}

/// Local pre-flight validation, exposed so the UI can block a bad post
/// before the user commits to a paid round-trip.
pub fn validate_draft(draft: &Draft) -> Vec<crate::utils::text::PostValidationError> {
    let mut errors = validate_post_body(&draft.body, "main", false);
    if let Some(link) = &draft.link_url {
        errors.extend(validate_post_body(link, "reply", true));
    }
    errors
}

#[cfg(test)]
mod tests {
    use super::*;

    fn draft_with(body: &str, link: Option<&str>, images: &[&str]) -> Draft {
        Draft {
            id: Some(1),
            source_tweet_id: None,
            body: body.into(),
            link_url: link.map(String::from),
            quote_tweet_id: None,
            writing_mode: "rephrase".into(),
            image_paths: images.iter().map(|s| s.to_string()).collect(),
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
    fn url_in_main_body_is_blocked() {
        let d = draft_with("look at https://a.com", None, &[]);
        let errs = validate_draft(&d);
        assert!(errs.iter().any(|e| e.code == "url_in_body"));
    }

    #[test]
    fn url_in_reply_is_allowed() {
        let d = draft_with("clean body", Some("get it https://proj.com"), &[]);
        let errs = validate_draft(&d);
        assert!(errs.is_empty(), "{errs:?}");
    }

    #[test]
    fn preview_reports_the_saving() {
        let d = draft_with("clean body", Some("see https://proj.com"), &[]);
        let cost = preview_publish_cost(&d);
        assert!((cost.total - 0.030).abs() < 1e-9);
        assert!(cost.saved > 0.0);
    }

    #[test]
    fn preview_without_reply_is_single_post() {
        let d = draft_with("clean body", None, &[]);
        assert!((preview_publish_cost(&d).total - 0.015).abs() < 1e-9);
    }

    #[test]
    fn multiple_cashtags_block_the_post() {
        let d = draft_with("$BTC and $ETH both moving", None, &[]);
        assert!(validate_draft(&d).iter().any(|e| e.code == "too_many_cashtags"));
    }

    #[test]
    fn overlong_body_is_blocked() {
        let d = draft_with(&"a".repeat(300), None, &[]);
        assert!(validate_draft(&d).iter().any(|e| e.code == "too_long"));
    }

    #[tokio::test]
    async fn publish_refuses_a_url_in_the_body_before_any_write() {
        let settings = crate::config::get_settings("crypto");
        let db = Database::open_in_memory().unwrap();
        let dir = tempfile::tempdir().unwrap();
        let store = crate::x::auth::TokenStore::new(&dir.path().join("t.json"));
        let mgr = std::sync::Arc::new(
            crate::x::auth::TokenManager::with_store(settings.clone(), store).unwrap(),
        );
        let client = XClient::new(settings.clone(), mgr).unwrap();
        let mut d = draft_with("see https://evil.com", None, &[]);

        let err = publish_draft(&settings, &db, &client, &mut d).await.unwrap_err();
        assert!(matches!(err, Error::Validation(_)));
        assert!(err.to_string().contains("$0.200"));
        // Nothing was written or charged.
        assert!(db.list_drafts(None, 10).unwrap().is_empty());
        assert_eq!(db.total_session_cost().unwrap(), 0.0);
    }
}