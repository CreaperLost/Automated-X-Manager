//! Semantic media auto-matching.
//!
//! Ported from `src/x_auto/utils/media_matching.py`. Scores project media
//! descriptions against the tweet's topic and source text, then returns
//! the best candidate so the UI can recommend an attachment.

use std::collections::BTreeSet;

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

/// One catalog entry considered for matching.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MediaCandidate {
    pub filename: String,
    pub path: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub added_at: String,
    #[serde(default)]
    pub is_video: bool,
    /// Populated only on the returned best match.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub match_score: Option<f64>,
}

const STOP_WORDS: &[&str] = &[
    "the", "and", "for", "with", "from", "that", "this", "have", "what", "when", "your", "will",
    "more", "about", "there", "their", "here", "just", "into", "some", "like", "make", "them",
    "then", "than", "they", "been", "also", "only", "other", "very", "much", "were", "where",
    "which", "whose", "over", "after", "before", "under", "again", "such", "down", "same", "does",
    "done",
];

fn token_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"[a-z0-9]+").expect("valid regex"))
}

/// Lowercase alphanumeric tokens of length >= 3, excluding stopwords.
fn tokenize(text: &str) -> BTreeSet<String> {
    if text.is_empty() {
        return BTreeSet::new();
    }
    token_re()
        .find_iter(&text.to_lowercase())
        .map(|m| m.as_str().to_string())
        .filter(|t| t.chars().count() >= 3 && !STOP_WORDS.contains(&t.as_str()))
        .collect()
}

/// Best-matching media for a tweet's text and topic.
///
/// Returns `None` when there is no media, no meaningful tokens, or no
/// positive score — never a spurious "best" with a zero score.
pub fn match_best_media(
    project_media: &[MediaCandidate],
    text: &str,
    topic: &str,
) -> Option<MediaCandidate> {
    if project_media.is_empty() {
        return None;
    }

    let topic_tokens = tokenize(topic);
    let text_tokens = tokenize(text);
    if topic_tokens.is_empty() && text_tokens.is_empty() {
        return None;
    }
    let clean_topic = topic.trim().to_lowercase();

    let mut best: Option<(MediaCandidate, f64)> = None;

    for item in project_media {
        let desc = item.description.trim();
        let filename = item.filename.trim();
        let stem = match filename.rfind('.') {
            Some(idx) if idx > 0 => &filename[..idx],
            _ => filename,
        };

        let desc_tokens = tokenize(desc);
        let fn_tokens = tokenize(stem);

        let mut score = 0.0f64;

        // Topic matches weigh 2.0x; source-text matches 1.0x.
        if !topic_tokens.is_empty() {
            score += desc_tokens.intersection(&topic_tokens).count() as f64 * 2.0;
            score += fn_tokens.intersection(&topic_tokens).count() as f64 * 2.0;
        }
        if !text_tokens.is_empty() {
            score += desc_tokens.intersection(&text_tokens).count() as f64 * 1.0;
            score += fn_tokens.intersection(&text_tokens).count() as f64 * 1.0;
        }

        // Phrase bonus.
        let clean_desc = desc.to_lowercase();
        if !clean_topic.is_empty()
            && clean_topic.chars().count() >= 5
            && (clean_desc.contains(&clean_topic) || clean_topic.contains(&clean_desc))
        {
            score += 3.0;
        }

        if score > best.as_ref().map(|(_, s)| *s).unwrap_or(0.0) {
            best = Some((item.clone(), score));
        }
    }

    let (mut item, score) = best?;
    if score <= 0.0 {
        return None;
    }
    item.match_score = Some((score * 100.0).round() / 100.0);
    Some(item)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cand(filename: &str, description: &str) -> MediaCandidate {
        MediaCandidate {
            filename: filename.into(),
            path: format!("/cache/{filename}"),
            description: description.into(),
            added_at: String::new(),
            is_video: false,
            match_score: None,
        }
    }

    #[test]
    fn no_media_returns_none() {
        assert!(match_best_media(&[], "defi perps are hot", "defi").is_none());
    }

    #[test]
    fn no_meaningful_tokens_returns_none() {
        let items = vec![cand("a.png", "some image")];
        assert!(match_best_media(&items, "the and for", "").is_none());
    }

    #[test]
    fn zero_score_returns_none() {
        let items = vec![cand("chart.png", "unrelated content")];
        assert!(match_best_media(&items, "quantum computing", "quantum").is_none());
    }

    #[test]
    fn topic_match_outscores_text_match() {
        let items = vec![
            cand("btc_chart.png", "bitcoin price chart"),
            cand("perp_table.png", "defi perpetual futures table"),
        ];
        let best = match_best_media(&items, "some perp market data", "perpetual futures")
            .expect("expected a match");
        assert_eq!(best.filename, "perp_table.png");
        assert!(best.match_score.unwrap() > 0.0);
    }

    #[test]
    fn phrase_bonus_applies() {
        let items = vec![cand("cover.png", "defi perpetual markets explained")];
        let best = match_best_media(&items, "unrelated words here", "defi perpetual").unwrap();
        assert_eq!(best.filename, "cover.png");
    }

    #[test]
    fn filename_stem_contributes_tokens() {
        let items = vec![cand("hyperliquid-chart.png", "")];
        let best = match_best_media(&items, "random text", "hyperliquid").unwrap();
        assert_eq!(best.filename, "hyperliquid-chart.png");
    }
}