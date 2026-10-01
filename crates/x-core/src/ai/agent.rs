//! Agent draft workflow: two options plus media recommendation.
//!
//! Ported from `AgentDraftWorkflow` in `src/x_auto/ai/workflow.py`.
//!
//! Generates Option A (rephrase) and Option B (original take) with the
//! niche's mandatory tone, then matches a project, writes the CTA, and
//! recommends a media file by scoring the catalog against the topic.

use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::ai::client::AiClient;
use crate::ai::prompts::{
    build_match_user, build_rephrase_user, get_agent_original_take_system,
    get_agent_rephrase_system, ProjectRef, MATCH_SYSTEM,
};
use crate::error::Result;
use crate::store::performance::load_winners;
use crate::utils::media_catalog::{catalog_path_for_data_dir, list_project_media_with_descriptions};
use crate::utils::media_matching::match_best_media;
use crate::utils::text::{clean_humanized_text, extract_first_url, format_cta_reply, strip_url};
use crate::ai::workflow::find_project;

use super::workflow::{MAIN_HARD_CAP, MATCH_KEYS, REPHRASE_KEYS};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentWorkflowResult {
    pub source_tweet_id: Option<String>,
    pub source_author: String,
    pub source_text: String,
    pub source_url: Option<String>,
    pub topic: String,
    pub project_name: String,
    pub project_url: String,
    pub cta_text: String,
    pub match_reasoning: String,

    // Option A (Rephrase)
    pub option_a_main: String,
    pub option_a_reasoning: String,
    // Option B (Original Take)
    pub option_b_main: String,
    pub option_b_reasoning: String,

    pub niche: String,
    pub recommended_media_path: Option<String>,
    pub recommended_media_name: Option<String>,
}

pub struct AgentDraftWorkflow<'a> {
    ai: &'a AiClient,
    niche: String,
    data_dir: Option<std::path::PathBuf>,
}

impl<'a> AgentDraftWorkflow<'a> {
    pub fn new(ai: &'a AiClient, niche: &str, data_dir: Option<&Path>) -> Self {
        Self {
            ai,
            niche: niche.trim().to_lowercase(),
            data_dir: data_dir.map(|p| p.to_path_buf()),
        }
    }

    pub async fn run(
        &self,
        source_text: &str,
        source_author: &str,
        source_tweet_id: Option<String>,
        projects: Vec<ProjectRef>,
        extra_instructions: &str,
    ) -> Result<AgentWorkflowResult> {
        // 1. Understand
        let source_url = extract_first_url(source_text).filter(|u| !u.is_empty());

        // Winning exemplars for self-improving few-shot prompting.
        let winning_examples: Vec<String> = match &self.data_dir {
            Some(dir) => load_winners(dir, 3)
                .into_iter()
                .filter_map(|w| {
                    let body = w.body.trim().to_string();
                    (!body.is_empty()).then_some(body)
                })
                .collect(),
            None => Vec::new(),
        };
        let winners_ref = (!winning_examples.is_empty()).then_some(winning_examples.as_slice());

        // 2. Option A: rephrase with the niche tone.
        let user_a = build_rephrase_user(
            source_text,
            source_author,
            source_url.as_deref(),
            "",
            0,
            extra_instructions,
            winners_ref,
        );
        let res_a = self
            .ai
            .generate_draft(
                &get_agent_rephrase_system(&self.niche),
                &user_a,
                2,
                REPHRASE_KEYS,
            )
            .await?;
        let topic = get_str(&res_a, "topic");
        let option_a_main =
            clean_generated(&get_str(&res_a, "main"), &self.niche);
        let option_a_reasoning = get_str(&res_a, "reasoning");

        // 3. Option B: original take with the niche tone.
        let user_b = build_rephrase_user(
            source_text,
            source_author,
            source_url.as_deref(),
            "",
            0,
            extra_instructions,
            winners_ref,
        );
        let res_b = self
            .ai
            .generate_draft(
                &get_agent_original_take_system(&self.niche),
                &user_b,
                2,
                REPHRASE_KEYS,
            )
            .await?;
        let option_b_main =
            clean_generated(&get_str(&res_b, "main"), &self.niche);
        let option_b_reasoning = get_str(&res_b, "reasoning");

        // 4. Match a project and build the CTA reply.
        let (project_name, project_url, cta_text, match_reasoning) = if projects.is_empty() {
            (String::new(), String::new(), String::new(), String::new())
        } else {
            let match_user = build_match_user(
                source_text,
                source_author,
                source_url.as_deref(),
                if topic.is_empty() { "general" } else { &topic },
                &projects,
            );
            let match_res = self
                .ai
                .generate_draft(MATCH_SYSTEM, &match_user, 2, MATCH_KEYS)
                .await?;
            let raw_name = get_str(&match_res, "project_name");
            let raw_cta = get_str(&match_res, "cta_text");
            let reasoning = get_str(&match_res, "reasoning");

            let project = find_project(&raw_name, &projects).unwrap_or_else(|| projects[0].clone());
            let cta = format_cta_reply(&raw_cta, &project.url, &self.niche);
            (project.name, project.url, cta, reasoning)
        };

        // 5. Recommend media by scoring the catalog against the topic.
        let (recommended_media_path, recommended_media_name) = match (&self.data_dir, &project_name) {
            (Some(dir), name) if !name.is_empty() => {
                let catalog_path = catalog_path_for_data_dir(dir);
                let media_files = list_project_media_with_descriptions(
                    &dir.join("media_cache"),
                    &catalog_path,
                    name,
                );
                match match_best_media(&media_files, source_text, &topic) {
                    Some(m) => (Some(m.path), Some(m.filename)),
                    None => (None, None),
                }
            }
            _ => (None, None),
        };

        Ok(AgentWorkflowResult {
            source_tweet_id,
            source_author: source_author.to_string(),
            source_text: source_text.to_string(),
            source_url,
            topic,
            project_name,
            project_url,
            cta_text,
            match_reasoning,
            option_a_main,
            option_a_reasoning,
            option_b_main,
            option_b_reasoning,
            niche: self.niche.clone(),
            recommended_media_path,
            recommended_media_name,
        })
    }
}

/// Strip any leaked URL, clean the voice, then cap the length.
fn clean_generated(raw: &str, niche: &str) -> String {
    let cleaned = clean_humanized_text(&strip_url(raw), niche);
    if cleaned.chars().count() > MAIN_HARD_CAP {
        let head: String = cleaned.chars().take(MAIN_HARD_CAP).collect();
        match head.rfind(' ') {
            Some(i) => head[..i].to_string(),
            None => head,
        }
    } else {
        cleaned
    }
}

fn get_str(map: &serde_json::Map<String, serde_json::Value>, key: &str) -> String {
    map.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Re-exported for the agent workflow's project lookup.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::performance::{is_high_performer, save_winner};
    use crate::utils::virality::Metrics;

    #[test]
    fn clean_generated_strips_urls_and_caps_length() {
        assert_eq!(clean_generated("go to https://a.com now", "crypto"), "go to now");

        let long = "word ".repeat(100);
        let out = clean_generated(&long, "crypto");
        assert!(out.chars().count() <= MAIN_HARD_CAP);
    }

    #[test]
    fn clean_generated_applies_niche_cleanup() {
        // The AI niche strips a trailing $AI ticker's dollar sign.
        assert_eq!(clean_generated("the market is moving $AI", "ai"), "the market is moving");
    }

    #[test]
    fn winners_roundtrip_and_rank_by_score() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_winners(dir.path(), 3).is_empty());

        save_winner(
            dir.path(),
            "1",
            "low performer",
            Metrics { like_count: 1, ..Default::default() },
            "",
        );
        save_winner(
            dir.path(),
            "2",
            "high performer",
            Metrics { like_count: 50, retweet_count: 10, ..Default::default() },
            "cta",
        );

        let top = load_winners(dir.path(), 3);
        assert_eq!(top.len(), 2);
        assert_eq!(top[0].body, "high performer", "highest score first");
    }

    #[test]
    fn winner_update_replaces_the_same_draft() {
        let dir = tempfile::tempdir().unwrap();
        save_winner(dir.path(), "7", "first", Metrics { like_count: 1, ..Default::default() }, "");
        save_winner(dir.path(), "7", "second", Metrics { like_count: 9, ..Default::default() }, "");
        let all = load_winners(dir.path(), 10);
        assert_eq!(all.len(), 1, "same draft_id must update, not append");
        assert_eq!(all[0].body, "second");
    }

    #[test]
    fn high_performer_threshold_matches_python() {
        let m = |l: i64, r: i64, rp: i64| Metrics { like_count: l, retweet_count: r, reply_count: rp, ..Default::default() };
        assert!(is_high_performer(&m(3, 0, 0), 3));
        assert!(!is_high_performer(&m(2, 0, 0), 3));
        assert!(is_high_performer(&m(0, 1, 0), 3), "one retweet qualifies");
        assert!(is_high_performer(&m(0, 0, 2), 3), "two replies qualify");
        assert!(!is_high_performer(&Metrics::default(), 3));
    }
}