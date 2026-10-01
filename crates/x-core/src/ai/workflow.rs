//! Linear 4-step draft workflow.
//!
//! Ported from `src/x_auto/ai/workflow.py`.
//!
//! ```text
//! 1. Understand   extract any URL from the source text as a topic hint (no LLM)
//! 2. Rephrase     LLM call #1: a fresh take on the source's idea
//! 3. Match + CTA  LLM call #2: pick the best project, write the CTA reply
//! 4. Fill         deterministic: validate the pick, guarantee the URL,
//!                 strip any URL from the body, assemble the Draft
//! ```
//!
//! Two LLM calls rather than one because rephrase (voice/freshness) and
//! match (topic fit) are different jobs; splitting them keeps each call
//! inspectable and makes step-level retries trivial.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ai::client::AiClient;
use crate::ai::prompts::{
    build_match_user, build_rephrase_user, ProjectRef, DRAFT_SYSTEM, MATCH_SYSTEM,
    ORIGINAL_TAKE_SYSTEM,
};
use crate::error::Result;
use crate::store::models::Draft;
use crate::utils::text::{clean_humanized_text, extract_first_url, format_cta_reply, strip_url};

/// Conservative cap. The X free plan caps posts at 280.
pub const MAIN_HARD_CAP: usize = 280;
pub const CTA_HARD_CAP: usize = 280;

pub const REPHRASE_KEYS: &[&str] = &["main", "topic", "reasoning"];
pub const MATCH_KEYS: &[&str] = &["project_name", "cta_text", "reasoning"];

/// What [`DraftWorkflow::run`] returns.
///
/// `draft` is ready to persist. The rest is surfaced for transparency
/// ("the AI picked Atlas because the source was about DeFi perps").
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowResult {
    pub draft: Draft,
    pub project_name: String,
    pub project_url: String,
    pub cta_text: String,
    pub topic: String,
    pub source_url: Option<String>,
    pub rephrase_reasoning: String,
    pub match_reasoning: String,
    /// True when the AI's pick was invalid and we fell back.
    pub fallback_used: bool,
    pub writing_mode: String,
}

/// Mutable state threaded through the four steps.
struct WorkflowState {
    source_text: String,
    source_author: String,
    source_tweet_id: Option<String>,
    writing_mode: String,
    projects: Vec<ProjectRef>,
    extra_instructions: String,
    image_paths: Vec<String>,
    niche: String,

    source_url: Option<String>,
    topic: String,
    main: String,
    cta_text: String,
    project_name: String,
    project_url: String,
    rephrase_reasoning: String,
    match_reasoning: String,
    fallback_used: bool,
}

pub struct DraftWorkflow<'a> {
    ai: &'a AiClient,
}

impl<'a> DraftWorkflow<'a> {
    pub fn new(ai: &'a AiClient) -> Self {
        Self { ai }
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn run(
        &self,
        source_text: &str,
        source_author: &str,
        source_tweet_id: Option<String>,
        projects: Vec<ProjectRef>,
        writing_mode: &str,
        extra_instructions: &str,
        image_paths: Vec<String>,
        niche: &str,
    ) -> Result<WorkflowResult> {
        let mut state = WorkflowState {
            source_text: source_text.to_string(),
            source_author: source_author.to_string(),
            source_tweet_id,
            writing_mode: writing_mode.to_string(),
            projects,
            extra_instructions: extra_instructions.to_string(),
            image_paths,
            niche: niche.to_string(),
            source_url: None,
            topic: String::new(),
            main: String::new(),
            cta_text: String::new(),
            project_name: String::new(),
            project_url: String::new(),
            rephrase_reasoning: String::new(),
            match_reasoning: String::new(),
            fallback_used: false,
        };

        step_understand(&mut state);
        self.step_rephrase(&mut state).await?;
        self.step_match_and_cta(&mut state).await?;
        step_fill(&mut state);

        Ok(to_result(&state))
    }

    /// LLM call #1: write the main post.
    async fn step_rephrase(&self, state: &mut WorkflowState) -> Result<()> {
        let user_msg = build_rephrase_user(
            &state.source_text,
            &state.source_author,
            state.source_url.as_deref(),
            "",
            state.image_paths.len(),
            &state.extra_instructions,
            None,
        );
        let system = if state.writing_mode == "original_take" {
            ORIGINAL_TAKE_SYSTEM
        } else {
            DRAFT_SYSTEM
        };
        let result = self.ai.generate_draft(system, &user_msg, 2, REPHRASE_KEYS).await?;

        state.main = get_str(&result, "main");
        state.topic = get_str(&result, "topic");
        state.rephrase_reasoning = get_str(&result, "reasoning");

        // Defensive: enforce the no-URL-in-main rule even if the model
        // leaked one in. This is the cost invariant; we cannot rely on
        // the prompt alone.
        state.main = clean_humanized_text(&strip_url(&state.main), &state.niche);
        if state.main.chars().count() > MAIN_HARD_CAP {
            state.main = truncate_to_last_space(&state.main, MAIN_HARD_CAP);
        }
        Ok(())
    }

    /// LLM call #2: pick the best project and write the CTA.
    async fn step_match_and_cta(&self, state: &mut WorkflowState) -> Result<()> {
        if state.projects.is_empty() {
            // Nothing to match against; the fill step leaves the reply empty.
            return Ok(());
        }
        let user_msg = build_match_user(
            &state.source_text,
            &state.source_author,
            state.source_url.as_deref(),
            &state.topic,
            &state.projects,
        );
        let result = self
            .ai
            .generate_draft(MATCH_SYSTEM, &user_msg, 2, MATCH_KEYS)
            .await?;
        state.project_name = get_str(&result, "project_name");
        state.cta_text = get_str(&result, "cta_text");
        state.match_reasoning = get_str(&result, "reasoning");
        Ok(())
    }
}

/// Step 1: extract the source's URL as a topic hint.
///
/// If the source promotes a DeFi perp protocol, the user's own related
/// project is likely the right CTA. We don't copy the URL, just pass it
/// forward as context.
fn step_understand(state: &mut WorkflowState) {
    state.source_url = extract_first_url(&state.source_text).filter(|u| !u.is_empty());
}

/// Step 4: validate the pick, guarantee the URL, build the Draft.
fn step_fill(state: &mut WorkflowState) {
    if state.projects.is_empty() {
        return; // nothing to point at; cta_text stays empty
    }
    let project = match find_project(&state.project_name, &state.projects) {
        Some(p) => p,
        None => {
            // The model returned an unknown or empty name. Fall back to the
            // first project so the user always has a concrete link.
            state.fallback_used = true;
            state.projects[0].clone()
        }
    };
    state.project_name = project.name.clone();
    state.project_url = project.url.clone();
    state.cta_text = format_cta_reply(&state.cta_text, &state.project_url, &state.niche);
}

fn to_result(state: &WorkflowState) -> WorkflowResult {
    WorkflowResult {
        draft: Draft {
            id: None,
            source_tweet_id: state.source_tweet_id.clone(),
            body: state.main.clone(),
            link_url: (!state.cta_text.is_empty()).then(|| state.cta_text.clone()),
            quote_tweet_id: None,
            writing_mode: state.writing_mode.clone(),
            image_paths: state.image_paths.clone(),
            tone: String::new(),
            status: "draft".into(),
            created_at: None,
            finalized_at: None,
            posted_at: None,
            x_tweet_id: None,
            x_reply_id: None,
            cost_usd: None,
            error: None,
        },
        project_name: state.project_name.clone(),
        project_url: state.project_url.clone(),
        cta_text: state.cta_text.clone(),
        topic: state.topic.clone(),
        source_url: state.source_url.clone(),
        rephrase_reasoning: state.rephrase_reasoning.clone(),
        match_reasoning: state.match_reasoning.clone(),
        fallback_used: state.fallback_used,
        writing_mode: state.writing_mode.clone(),
    }
}

fn get_str(map: &serde_json::Map<String, Value>, key: &str) -> String {
    map.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Case-insensitive project lookup by exact name.
pub fn find_project(name: &str, projects: &[ProjectRef]) -> Option<ProjectRef> {
    let needle = name.trim().to_lowercase();
    if needle.is_empty() {
        return None;
    }
    projects.iter().find(|p| p.name.trim().to_lowercase() == needle).cloned()
}

fn truncate_to_last_space(s: &str, limit: usize) -> String {
    let head: String = s.chars().take(limit).collect();
    match head.rfind(' ') {
        Some(i) => head[..i].to_string(),
        None => head,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn projects() -> Vec<ProjectRef> {
        vec![
            ProjectRef::new("Atlas", "https://atlas.com"),
            ProjectRef::new("Beacon", "https://beacon.com"),
        ]
    }

    #[test]
    fn understand_extracts_the_source_url() {
        let mut s = WorkflowState {
            source_text: "check https://src.com out".into(),
            source_author: "a".into(),
            source_tweet_id: None,
            writing_mode: "rephrase".into(),
            projects: vec![],
            extra_instructions: String::new(),
            image_paths: vec![],
            niche: "crypto".into(),
            source_url: None,
            topic: String::new(),
            main: String::new(),
            cta_text: String::new(),
            project_name: String::new(),
            project_url: String::new(),
            rephrase_reasoning: String::new(),
            match_reasoning: String::new(),
            fallback_used: false,
        };
        step_understand(&mut s);
        assert_eq!(s.source_url.as_deref(), Some("https://src.com"));

        s.source_text = "no link here".into();
        step_understand(&mut s);
        assert!(s.source_url.is_none());
    }

    #[test]
    fn find_project_is_case_insensitive_and_trims() {
        let p = projects();
        assert_eq!(find_project("atlas", &p).unwrap().name, "Atlas");
        assert_eq!(find_project("  BEACON ", &p).unwrap().name, "Beacon");
        assert!(find_project("", &p).is_none());
        assert!(find_project("Unknown", &p).is_none());
    }

    #[test]
    fn fill_falls_back_to_the_first_project_on_a_bad_pick() {
        let mut s = base_state();
        s.projects = projects();
        s.project_name = "Nonexistent".into();
        s.cta_text = "Have a look".into();
        step_fill(&mut s);

        assert!(s.fallback_used, "should flag the fallback");
        assert_eq!(s.project_name, "Atlas");
        assert_eq!(s.project_url, "https://atlas.com");
        assert!(s.cta_text.ends_with("https://atlas.com"));
    }

    #[test]
    fn fill_uses_the_ai_pick_when_valid() {
        let mut s = base_state();
        s.projects = projects();
        s.project_name = "Beacon".into();
        s.cta_text = "Worth a look".into();
        step_fill(&mut s);

        assert!(!s.fallback_used);
        assert_eq!(s.project_name, "Beacon");
        assert!(s.cta_text.contains("https://beacon.com"));
    }

    #[test]
    fn fill_without_projects_returns_early() {
        // With no projects the AI never wrote a CTA, and the step is a
        // no-op: it neither picks a project nor clears anything.
        let mut s = base_state();
        s.projects = vec![];
        s.cta_text = String::new();
        step_fill(&mut s);
        assert!(s.cta_text.is_empty());
        assert!(s.project_url.is_empty());
        assert!(!s.fallback_used);
    }

    #[test]
    fn result_maps_cta_into_link_url() {
        let mut s = base_state();
        s.projects = projects();
        s.project_name = "Atlas".into();
        s.cta_text = "Take a look".into();
        s.main = "my post".into();
        step_fill(&mut s);
        let r = to_result(&s);
        assert_eq!(r.draft.body, "my post");
        assert_eq!(r.draft.link_url.as_deref(), Some(r.cta_text.as_str()));
        assert_eq!(r.writing_mode, "rephrase");
        assert_eq!(r.draft.status, "draft");
    }

    #[test]
    fn result_leaves_link_url_none_when_no_cta() {
        let s = base_state();
        let r = to_result(&s);
        assert!(r.draft.link_url.is_none());
    }

    #[test]
    fn truncation_cuts_at_the_last_space() {
        assert_eq!(truncate_to_last_space("hello brave world", 12), "hello brave");
        assert_eq!(truncate_to_last_space("short", 10), "short");
    }

    fn base_state() -> WorkflowState {
        WorkflowState {
            source_text: "some source".into(),
            source_author: "alice".into(),
            source_tweet_id: Some("123".into()),
            writing_mode: "rephrase".into(),
            projects: vec![],
            extra_instructions: String::new(),
            image_paths: vec![],
            niche: "crypto".into(),
            source_url: None,
            topic: String::new(),
            main: String::new(),
            cta_text: String::new(),
            project_name: String::new(),
            project_url: String::new(),
            rephrase_reasoning: String::new(),
            match_reasoning: String::new(),
            fallback_used: false,
        }
    }
}