//! Performance tracking and the winning-exemplar store.
//!
//! Ported from `src/x_auto/store/performance.py`. Logs high-performing
//! published posts to `data/<niche>/winners.json` so they can be injected
//! as few-shot exemplars into future generations — the self-improving
//! feedback loop.

use std::path::{Path, PathBuf};

use chrono::Utc;
use serde::{Deserialize, Serialize};

use crate::utils::virality::Metrics;

/// Path to the niche's `winners.json`.
pub fn winners_path(data_dir: &Path) -> PathBuf {
    data_dir.join("winners.json")
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Winner {
    pub draft_id: String,
    pub body: String,
    #[serde(default)]
    pub cta_text: String,
    #[serde(default)]
    pub metrics: Metrics,
    #[serde(default)]
    pub saved_at: String,
}

fn load_all(data_dir: &Path) -> Vec<Winner> {
    let path = winners_path(data_dir);
    if !path.is_file() {
        return Vec::new();
    }
    let Ok(raw) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    serde_json::from_str(raw.trim()).unwrap_or_default()
}

/// `likes + 2*retweets + 1.5*replies + 2*quotes`.
fn score(item: &Winner) -> f64 {
    item.metrics.like_count as f64
        + item.metrics.retweet_count as f64 * 2.0
        + item.metrics.reply_count as f64 * 1.5
        + item.metrics.quote_count as f64 * 2.0
}

/// Winning posts sorted by performance, best first.
pub fn load_winners(data_dir: &Path, limit: usize) -> Vec<Winner> {
    let mut all = load_all(data_dir);
    if all.is_empty() {
        return all;
    }
    all.sort_by(|a, b| score(b).partial_cmp(&score(a)).unwrap_or(std::cmp::Ordering::Equal));
    all.truncate(limit);
    all
}

/// Save or update a winning post, written atomically.
pub fn save_winner(
    data_dir: &Path,
    draft_id: &str,
    body: &str,
    metrics: Metrics,
    cta_text: &str,
) -> Winner {
    let path = winners_path(data_dir);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut existing = load_all(data_dir);

    let entry = Winner {
        draft_id: draft_id.to_string(),
        body: body.trim().to_string(),
        cta_text: cta_text.trim().to_string(),
        metrics,
        saved_at: Utc::now().to_rfc3339(),
    };

    match existing.iter_mut().find(|w| w.draft_id == draft_id) {
        Some(slot) => *slot = entry.clone(),
        None => existing.push(entry.clone()),
    }

    if let Ok(body) = serde_json::to_string_pretty(&existing) {
        let tmp = path.with_extension("tmp");
        if std::fs::write(&tmp, format!("{body}\n")).is_ok() {
            let _ = std::fs::rename(&tmp, &path);
        }
    }
    entry
}

/// Whether public metrics clear the winner threshold.
pub fn is_high_performer(metrics: &Metrics, like_threshold: i64) -> bool {
    metrics.like_count >= like_threshold || metrics.retweet_count >= 1 || metrics.reply_count >= 2
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_file_yields_no_winners() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_winners(dir.path(), 3).is_empty());
    }

    #[test]
    fn malformed_file_yields_no_winners() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(winners_path(dir.path()), "{broken").unwrap();
        assert!(load_winners(dir.path(), 3).is_empty());
    }

    #[test]
    fn ranks_by_weighted_score() {
        let dir = tempfile::tempdir().unwrap();
        // 10 likes = 10.0; 1 retweet = 2.0. The retweet-only post loses.
        save_winner(dir.path(), "a", "likes post", Metrics { like_count: 10, ..Default::default() }, "");
        save_winner(dir.path(), "b", "retweet post", Metrics { retweet_count: 1, ..Default::default() }, "");
        let top = load_winners(dir.path(), 2);
        assert_eq!(top[0].body, "likes post");
    }

    #[test]
    fn respects_the_limit() {
        let dir = tempfile::tempdir().unwrap();
        for i in 0..5 {
            save_winner(
                dir.path(),
                &format!("d{i}"),
                &format!("post {i}"),
                Metrics { like_count: i, ..Default::default() },
                "",
            );
        }
        assert_eq!(load_winners(dir.path(), 3).len(), 3);
    }

    #[test]
    fn updates_instead_of_duplicating() {
        let dir = tempfile::tempdir().unwrap();
        save_winner(dir.path(), "d1", "old", Metrics { like_count: 1, ..Default::default() }, "");
        save_winner(dir.path(), "d1", "new", Metrics { like_count: 99, ..Default::default() }, "cta");
        let all = load_winners(dir.path(), 10);
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].body, "new");
        assert_eq!(all[0].cta_text, "cta");
    }

    #[test]
    fn trims_whitespace_on_save() {
        let dir = tempfile::tempdir().unwrap();
        let w = save_winner(dir.path(), "d", "  padded  ", Metrics::default(), "  cta  ");
        assert_eq!(w.body, "padded");
        assert_eq!(w.cta_text, "cta");
    }
}