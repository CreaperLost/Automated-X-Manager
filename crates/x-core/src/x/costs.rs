//! Cost estimation for X API operations.
//!
//! Ported from `src/x_auto/costs.py`.
//!
//! Pricing (verified Aug 2026):
//! ```text
//! Third-party post read ........... $0.005 per post
//! User profile read ............... $0.010 per profile
//! Create plain post ............... $0.015 per post
//! Create post with URL inline ..... $0.200 per post
//! Delete post ..................... $0.010 per post
//! Media upload .................... (free)
//! ```
//!
//! The $0.200 URL surcharge is the reason the whole app splits a post
//! into a body + a CTA reply. Do not "simplify" that away.

use serde::{Deserialize, Serialize};

use crate::utils::text::contains_url;

pub const COST_READ_POST: f64 = 0.005;
pub const COST_READ_PROFILE: f64 = 0.010;
pub const COST_OWNED_READ: f64 = 0.001;

pub const COST_POST_PLAIN: f64 = 0.015;
pub const COST_POST_WITH_URL: f64 = 0.200;
pub const COST_POST_DELETED: f64 = 0.010;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CostBreakdown {
    pub main: f64,
    pub reply: f64,
    pub total: f64,
    pub reason: String,
    #[serde(default)]
    pub inline_alternative: Option<f64>,
    #[serde(default)]
    pub saved: f64,
}

/// Estimate the cost of publishing a two-post thread.
///
/// With `link_in_reply`, the URL rides in the reply and the thread costs
/// $0.030. With it inline, the single post costs $0.200.
pub fn estimate_post_cost(
    main_text: &str,
    has_image: bool,
    link_in_reply: bool,
    reply_text: &str,
) -> CostBreakdown {
    let _ = has_image; // media upload is free; kept for call-site parity
    if link_in_reply {
        let main_cost = COST_POST_PLAIN;
        let reply_cost = if reply_text.is_empty() { 0.0 } else { COST_POST_PLAIN };
        let inline = if contains_url(main_text) || contains_url(reply_text) {
            Some(COST_POST_WITH_URL)
        } else {
            None
        };
        let saved = inline.map(|i| i - main_cost - reply_cost).unwrap_or(0.0);
        return CostBreakdown {
            main: main_cost,
            reply: reply_cost,
            total: main_cost + reply_cost,
            reason: if saved > 0.0 {
                "link-in-reply (saves $0.170 vs inline URL)".into()
            } else {
                "link-in-reply".into()
            },
            inline_alternative: inline,
            saved,
        };
    }
    if contains_url(main_text) {
        return CostBreakdown {
            main: COST_POST_WITH_URL,
            reply: 0.0,
            total: COST_POST_WITH_URL,
            reason: "URL inline ($0.200 surcharge)".into(),
            inline_alternative: None,
            saved: 0.0,
        };
    }
    CostBreakdown {
        main: COST_POST_PLAIN,
        reply: 0.0,
        total: COST_POST_PLAIN,
        reason: "plain post".into(),
        inline_alternative: None,
        saved: 0.0,
    }
}

/// Estimate the cost of reading tweets plus handle lookups.
pub fn estimate_read_cost(num_posts: i64, num_profiles: i64) -> f64 {
    num_posts as f64 * COST_READ_POST + num_profiles as f64 * COST_READ_PROFILE
}

/// Running session-spend counter. Reconciled with `post_log.cost_usd`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SessionMeter {
    reads_posts: i64,
    reads_profiles: i64,
    writes: f64,
}

impl SessionMeter {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add_read_post(&mut self, n: i64) {
        self.reads_posts += n;
    }
    pub fn add_read_profile(&mut self, n: i64) {
        self.reads_profiles += n;
    }
    pub fn add_write(&mut self, cost_usd: f64) {
        self.writes += cost_usd;
    }

    pub fn reads_cost(&self) -> f64 {
        self.reads_posts as f64 * COST_READ_POST + self.reads_profiles as f64 * COST_READ_PROFILE
    }
    pub fn total(&self) -> f64 {
        self.reads_cost() + self.writes
    }
    pub fn writes(&self) -> f64 {
        self.writes
    }
    pub fn reads_posts(&self) -> i64 {
        self.reads_posts
    }
    pub fn reads_profiles(&self) -> i64 {
        self.reads_profiles
    }

    pub fn summary(&self) -> MeterSummary {
        MeterSummary {
            posts_read: self.reads_posts,
            profiles_read: self.reads_profiles,
            reads_cost_usd: round4(self.reads_cost()),
            writes_cost_usd: round4(self.writes),
            total_cost_usd: round4(self.total()),
        }
    }

    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MeterSummary {
    pub posts_read: i64,
    pub profiles_read: i64,
    pub reads_cost_usd: f64,
    pub writes_cost_usd: f64,
    pub total_cost_usd: f64,
}

/// Round to 4 decimal places, matching Python's `round(x, 4)`.
fn round4(v: f64) -> f64 {
    (v * 10_000.0).round() / 10_000.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn link_in_reply_costs_thirty_cents() {
        let c = estimate_post_cost("plain body", false, true, "https://proj.com");
        assert!((c.total - 0.030).abs() < 1e-9, "{c:?}");
        assert_eq!(c.main, 0.015);
        assert_eq!(c.reply, 0.015);
    }

    #[test]
    fn empty_reply_costs_only_the_main_post() {
        let c = estimate_post_cost("plain body", false, true, "");
        assert!((c.total - 0.015).abs() < 1e-9);
        assert_eq!(c.reply, 0.0);
    }

    #[test]
    fn inline_url_shows_the_savings() {
        let c = estimate_post_cost("see https://a.com", false, true, "https://proj.com");
        assert!((c.saved - 0.170).abs() < 1e-9, "saved was {}", c.saved);
        assert_eq!(c.inline_alternative, Some(0.200));
        assert!(c.reason.contains("saves"));
    }

    #[test]
    fn inline_single_post_costs_twenty_cents() {
        let c = estimate_post_cost("see https://a.com", false, false, "");
        assert!((c.total - 0.200).abs() < 1e-9);
        assert!(c.reason.contains("URL inline"));
    }

    #[test]
    fn plain_single_post_costs_one_and_a_half_cents() {
        let c = estimate_post_cost("no link here", false, false, "");
        assert!((c.total - 0.015).abs() < 1e-9);
        assert_eq!(c.reason, "plain post");
    }

    #[test]
    fn read_cost_combines_posts_and_profiles() {
        let v = estimate_read_cost(10, 2);
        assert!((v - (10.0 * 0.005 + 2.0 * 0.010)).abs() < 1e-9);
    }

    #[test]
    fn meter_accumulates_reads_and_writes() {
        let mut m = SessionMeter::new();
        m.add_read_post(4);
        m.add_read_profile(2);
        m.add_write(0.015);
        assert!((m.reads_cost() - (4.0 * 0.005 + 2.0 * 0.010)).abs() < 1e-9);
        assert!((m.total() - 0.055).abs() < 1e-9, "{}", m.total());

        let s = m.summary();
        assert_eq!(s.posts_read, 4);
        assert_eq!(s.profiles_read, 2);
        assert!((s.total_cost_usd - 0.055).abs() < 1e-9);

        m.reset();
        assert_eq!(m.total(), 0.0);
        assert_eq!(m.summary().posts_read, 0);
    }
}