//! Engagement velocity and opportunity scoring for fetched tweets.
//!
//! Ported from `src/x_auto/utils/virality.py`. The velocity formula
//! rewards posts gaining engagement quickly, which drives the "hot
//! opportunities" highlight in the Sources view.

use std::collections::HashSet;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Metrics fields we read from X's `public_metrics`.
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct Metrics {
    #[serde(default)]
    pub like_count: i64,
    #[serde(default)]
    pub retweet_count: i64,
    #[serde(default)]
    pub reply_count: i64,
    #[serde(default)]
    pub quote_count: i64,
    #[serde(default)]
    pub impression_count: i64,
}

impl Metrics {
    /// Read the subset of `public_metrics` we care about from its JSON form.
    pub fn from_json(value: &serde_json::Value) -> Self {
        let g = |k: &str| value.get(k).and_then(|v| v.as_i64()).unwrap_or(0);
        Self {
            like_count: g("like_count"),
            retweet_count: g("retweet_count"),
            reply_count: g("reply_count"),
            quote_count: g("quote_count"),
            impression_count: g("impression_count"),
        }
    }

    pub fn from_map(map: &std::collections::BTreeMap<String, i64>) -> Self {
        let g = |k: &str| map.get(k).copied().unwrap_or(0);
        Self {
            like_count: g("like_count"),
            retweet_count: g("retweet_count"),
            reply_count: g("reply_count"),
            quote_count: g("quote_count"),
            impression_count: g("impression_count"),
        }
    }
}

/// Minimal view of a tweet needed for scoring.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoredTweet {
    pub id: String,
    #[serde(default)]
    pub public_metrics: Metrics,
    #[serde(default)]
    pub created_at: Option<String>,
}

/// Parse an ISO-8601 string to a UTC datetime, defaulting to now.
pub fn parse_datetime(raw: Option<&str>) -> DateTime<Utc> {
    let Some(s) = raw else {
        return Utc::now();
    };
    crate::store::db::parse_ts(s).unwrap_or_else(Utc::now)
}

/// Time-decayed engagement velocity.
///
/// `raw = likes + 2*retweets + 2.5*quotes + 1.5*replies`,
/// `velocity = raw / hours_elapsed^0.8`.
pub fn compute_engagement_velocity(
    metrics: &Metrics,
    created_at: Option<&str>,
    reference_time: Option<DateTime<Utc>>,
) -> f64 {
    let raw = metrics.like_count as f64
        + metrics.retweet_count as f64 * 2.0
        + metrics.quote_count as f64 * 2.5
        + metrics.reply_count as f64 * 1.5;
    if raw <= 0.0 {
        return 0.0;
    }
    let reference = reference_time.unwrap_or_else(Utc::now);
    let post_time = parse_datetime(created_at);
    // Floor at 60s / 0.25h so a just-posted tweet can't divide by zero.
    let diff_seconds = ((reference - post_time).num_milliseconds() as f64 / 1000.0).max(60.0);
    let hours = (diff_seconds / 3600.0).max(0.25);
    let velocity = raw / hours.powf(0.8);
    (velocity * 100.0).round() / 100.0
}

/// IDs of the highest-velocity tweets, for the "hot" highlight.
pub fn get_hot_opportunity_ids(
    tweets: &[ScoredTweet],
    top_n: usize,
    min_score: f64,
    reference_time: Option<DateTime<Utc>>,
) -> HashSet<String> {
    let mut scored: Vec<(String, f64)> = tweets
        .iter()
        .filter_map(|t| {
            let score = compute_engagement_velocity(
                &t.public_metrics,
                t.created_at.as_deref(),
                reference_time,
            );
            (score >= min_score).then(|| (t.id.clone(), score))
        })
        .collect();
    // Sort by score descending; ties keep insertion order via a stable sort.
    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    scored.into_iter().take(top_n).map(|(id, _)| id).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    fn tweet(id: &str, m: Metrics, hours_ago: i64) -> ScoredTweet {
        ScoredTweet {
            id: id.into(),
            public_metrics: m,
            created_at: Some((Utc::now() - Duration::hours(hours_ago)).to_rfc3339()),
        }
    }

    #[test]
    fn zero_engagement_is_zero_velocity() {
        let m = Metrics::default();
        assert_eq!(compute_engagement_velocity(&m, None, None), 0.0);
    }

    #[test]
    fn recent_high_engagement_outranks_slow_burn() {
        let hot = Metrics { like_count: 100, ..Default::default() };
        let slow = Metrics { like_count: 100, ..Default::default() };
        let ref_time = Utc::now();
        let hot_at = (ref_time - Duration::hours(1)).to_rfc3339();
        let slow_at = (ref_time - Duration::hours(48)).to_rfc3339();
        let v_hot = compute_engagement_velocity(&hot, Some(&hot_at), Some(ref_time));
        let v_slow = compute_engagement_velocity(&slow, Some(&slow_at), Some(ref_time));
        assert!(v_hot > v_slow, "{v_hot} should exceed {v_slow}");
    }

    #[test]
    fn fresh_post_does_not_divide_by_zero() {
        let m = Metrics { like_count: 5, ..Default::default() };
        let v = compute_engagement_velocity(&m, Some(&Utc::now().to_rfc3339()), Some(Utc::now()));
        assert!(v.is_finite());
        assert!(v > 0.0);
    }

    #[test]
    fn future_timestamp_is_floored() {
        let m = Metrics { like_count: 5, ..Default::default() };
        let future = (Utc::now() + Duration::hours(5)).to_rfc3339();
        let v = compute_engagement_velocity(&m, Some(&future), Some(Utc::now()));
        assert!(v.is_finite());
    }

    #[test]
    fn hot_ids_respects_threshold_and_top_n() {
        let tweets = vec![
            tweet("low", Metrics { like_count: 1, ..Default::default() }, 10),
            tweet("mid", Metrics { like_count: 50, ..Default::default() }, 2),
            tweet("high", Metrics { like_count: 500, ..Default::default() }, 1),
        ];
        let ids = get_hot_opportunity_ids(&tweets, 5, 2.0, None);
        assert!(ids.contains("high"));
        assert!(ids.contains("mid"));
        assert!(!ids.contains("low"), "below threshold should be excluded");

        let capped = get_hot_opportunity_ids(&tweets, 1, 2.0, None);
        assert_eq!(capped.len(), 1);
        assert!(capped.contains("high"));
    }

    #[test]
    fn metrics_parse_from_x_json_shape() {
        let v = serde_json::json!({
            "like_count": 10, "retweet_count": 5,
            "reply_count": 2, "quote_count": 1, "impression_count": 900
        });
        let m = Metrics::from_json(&v);
        assert_eq!(m.like_count, 10);
        assert_eq!(m.retweet_count, 5);
        assert_eq!(m.impression_count, 900);
    }

    #[test]
    fn metrics_parse_missing_fields_default_to_zero() {
        let m = Metrics::from_json(&serde_json::json!({"like_count": 3}));
        assert_eq!(m.like_count, 3);
        assert_eq!(m.quote_count, 0);
    }

    #[test]
    fn parse_datetime_handles_both_separators() {
        let a = parse_datetime(Some("2026-08-27T12:00:00Z"));
        assert_eq!(a.format("%H:%M:%S").to_string(), "12:00:00");
        let b = parse_datetime(Some("2026-08-27 12:00:00"));
        assert_eq!(b.format("%H:%M:%S").to_string(), "12:00:00");
        // Garbage falls back to now rather than panicking.
        let fallback = parse_datetime(Some("garbage"));
        assert!((Utc::now() - fallback).num_seconds().abs() < 5);
    }
}