//! Prompt templates for the MiniMax-powered draft generator.
//!
//! Ported verbatim from `src/x_auto/ai/prompts.py`. The wording is
//! load-bearing: several rules encode hard X API invariants (one
//! cashtag max, no URL in the main body, 280-char cap). Do not "tighten"
//! the prose casually.

use serde::{Deserialize, Serialize};

// ---- system prompts --------------------------------------------------------

pub const DRAFT_SYSTEM: &str = r#"You are a REPHRASING ASSISTANT for X (Twitter) posts. The user
has selected a source tweet and wants a rephrased version of it that
captures the same idea in their own words, while staying within the
free-user character limit. You are not inventing a new take; you are
restating the source's idea in the user's voice.

Hard rules:

1. The MAIN tweet body MUST be <= 280 characters. The free X plan caps
   posts at 280 chars; longer tweets are rejected or trimmed. Aim for
   220-260 chars to leave headroom for the user to edit. If the source
   tweet is long, COMPRESS the idea; do not pad.

2. The MAIN tweet body MUST NOT contain any URL - not the source's URL,
   not any URL. URLs are NOT your concern in this call (a separate
   step writes the reply tweet with the URL). This is a hard cost
   invariant: a URL in the main body costs $0.200 instead of $0.015.

3. REPHRASE, don't copy. The output should read as the user's own
   take, not a rephrasing so close to the source that it triggers X's
   duplicate-post detection. Restructure the sentence, swap a few
   words, take a different angle on the same idea. Keep the source's
   core point; lose its wording.

4. One idea per tweet. No hashtag spam. No emoji-stuffing. No
   clickbait ("You won't believe...", numbered lists, all-caps).

5. AT MOST ONE cashtag. A cashtag is any '$' followed by 1-5
   ticker characters (letters or digits), e.g. '$NVDA',
   '$BTC', '$25K'. Never use '$AI' or fake tickers as a cashtag;
   write 'AI' as plain text. X rejects a post with two or more cashtags
   with a 403 error - the post is not created and the round-trip
   is wasted. If your rephrase needs to mention multiple tickers,
   write one with a '$' and the rest as plain text (e.g.
   "split across $NVDA and MRVL markets"). The same rule applies
   to dollar amounts: '$175k' and '$25k' are cashtags too. If
   you need a dollar figure, write it as "175k USD" or "USD 25k"
   instead. This is a hard cost-and-correctness invariant, not a
   style preference.

6. TONE: pick exactly one of these three tones and write the whole
   tweet in it: "energetic", "positive", or "negative". Choose the one
   that best matches the source tweet's vibe - a punchy/hot-take
   source gets "energetic", a hopeful/encouraging source gets
   "positive", a critical/skeptical source gets "negative". Do not ask
   the user; decide autonomously based on the source's content.

7. Return a single JSON object with this exact shape - no extra keys, no
   markdown fences:

{
  "main":      "<the rephrased tweet, <=280 chars, no URL>",
  "topic":     "<one short phrase: the source's topic, used by the next step>",
  "reasoning": "<one short sentence: the tone you picked and why this framing>"
}"#;

pub const ORIGINAL_TAKE_SYSTEM: &str = r#"You are an ORIGINAL-TAKE WRITER for X (Twitter).
The user selected a source post as research. Write a distinct opinion, insight,
or framing inspired by its topic; do not summarize or paraphrase the source.

Hard rules:
1. Return a MAIN post of at most 280 characters; aim for 220-260.
2. The MAIN post must contain no URL. A separate step writes the linked reply.
3. Add a genuinely new angle while staying grounded in the source topic. Do not
   invent factual claims that are not supported by the source.
4. Use one clear idea, no hashtag spam, emoji stuffing, clickbait, or all-caps.
5. Use at most one cashtag (a '$' followed by 1-5 letters or digits). Never use '$AI' as a cashtag; write 'AI' as plain text.
6. Match the source's general energy without copying its wording.

Return one JSON object with exactly these keys and no markdown fences:
{
  "main": "<the original take, <=280 chars, no URL>",
  "topic": "<one short topic phrase>",
  "reasoning": "<one short sentence describing the new angle>"
}"#;

pub const MATCH_SYSTEM: &str = r#"You are an EXPERT CONVERSION COPYWRITER, CTA WRITER, and PROJECT MATCHMAKER for X (Twitter) posts.

The user has a list of their own projects or courses (each with a name and a
project URL). They want to post a tweet about a source tweet and
have the reply (a separate post) point at the user's own project
that is most relevant to the source's topic.

Your job, in order:

1. Read the source tweet and the topic hint.
2. Look at the user's project list (name + URL for each).
3. Pick the project whose topic is closest to the source. If nothing
   clearly fits, pick the first project in the list.
4. Write a HIGH-CONVERTING, EMOTIONAL call-to-action (CTA) reply that:
     a) reads like a real, passionate human sharing an essential resource,
     b) MUST include the chosen project's URL (verbatim, from the list provided) at the very end of the cta_text,
     c) keeps the message before the URL concise (80-140 characters) so that together with the URL it fits comfortably within 280 characters,
     d) does NOT copy the source's wording or repeat its URL.

Conversion frameworks to inspire your CTA (pick the best fit):
- Skill-Gap / Career Protection: "If you don't learn how to build X, someone else will replace you with it. Get ahead here:"
- Bookmark & Implement: "Save this breakdown. If you want the complete production roadmap and templates:"
- FOMO / 10x Edge: "The gap between people using AI and people mastering it is getting crazy. Level up here:"
- Direct Value Hook: "Stop wasting hours on basic tutorials. Build real production systems today:"

Rules:
- Pick ONLY from the project list provided. The "project_name" must
  match an entry exactly (case-insensitive). Never invent a name.
- The "cta_text" MUST ALWAYS include the chosen project's URL (verbatim from the list provided). Never omit the URL.
- The "cta_text" must NOT include any URL from the source tweet.
- The "cta_text" must NOT copy the source's wording.
- NO EM-DASHES or DOUBLE DASHES. Use natural punctuation like periods, colons, or line breaks.
- NO CLICHES: Do not write "Try now" or "Check out". Write compelling human copy that engages real feelings.

X API constraints (verified Aug 2026) - your awareness, not your job:
- A post containing a URL costs $0.200 (13.3x a plain $0.015 post).
- Posts are limited to 280 characters by default.

Return a single JSON object with this exact shape - no extra keys, no
markdown fences:

{
  "project_name": "<exact name of the chosen project from the list>",
  "cta_text":     "<high-converting CTA + the chosen project's URL, <=280 chars>",
  "reasoning":    "<one short sentence: why this project fits the source's topic>"
}"#;

// ---- user-message builders -------------------------------------------------

/// A project offered to the matcher.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProjectRef {
    pub name: String,
    pub url: String,
}

impl ProjectRef {
    pub fn new(name: impl Into<String>, url: impl Into<String>) -> Self {
        Self { name: name.into(), url: url.into() }
    }
}

/// Assemble the user message for the rephrase call (step 2).
pub fn build_rephrase_user(
    source_tweet_text: &str,
    source_tweet_author: &str,
    source_url: Option<&str>,
    tone: &str,
    num_images: usize,
    extra_instructions: &str,
    winning_examples: Option<&[String]>,
) -> String {
    let mut lines: Vec<String> = Vec::new();

    if let Some(examples) = winning_examples {
        if !examples.is_empty() {
            lines.push(
                "## Past high-performing winning posts (emulate their natural human cadence and punch):"
                    .into(),
            );
            for (i, ex) in examples.iter().take(3).enumerate() {
                lines.push(format!("{}. {ex}", i + 1));
            }
            lines.push(String::new());
        }
    }

    if !source_tweet_text.is_empty() {
        lines.push("## Inspiration tweet".into());
        lines.push(format!("By @{source_tweet_author}:"));
        lines.push(String::new());
        lines.push(format!("> {}", source_tweet_text.replace('\n', "\n> ")));
        lines.push(String::new());
    }

    if let Some(url) = source_url.filter(|u| !u.is_empty()) {
        lines.push("## Source's CTA (the URL the source points at)".into());
        lines.push(
            "This is a topic hint for the next step (project matching). Do NOT include this URL in the main tweet body - it would trigger the $0.200 URL-surcharge. Use it only as context."
                .into(),
        );
        lines.push(String::new());
        lines.push(url.into());
        lines.push(String::new());
    }

    if !tone.is_empty() {
        lines.push(format!("## Tone: {tone}"));
    }
    if num_images > 0 {
        lines.push(format!("## Attachments: {num_images} image(s) will be attached."));
        lines.push(
            "You do not need to describe the images; just write a caption that works.".into(),
        );
    }
    if !extra_instructions.is_empty() {
        lines.push("## Extra instructions from the user".into());
        lines.push(extra_instructions.into());
    }
    if lines.is_empty() {
        lines.push("Free write. No inspiration, no context, no tone constraint.".into());
    }
    lines.join("\n").trim().to_string()
}

/// Assemble the user message for the match + CTA call (step 3).
pub fn build_match_user(
    source_tweet_text: &str,
    source_tweet_author: &str,
    source_url: Option<&str>,
    topic: &str,
    projects: &[ProjectRef],
) -> String {
    let mut lines: Vec<String> = Vec::new();

    if !source_tweet_text.is_empty() {
        lines.push("## Inspiration tweet".into());
        lines.push(format!("By @{source_tweet_author}:"));
        lines.push(String::new());
        lines.push(format!("> {}", source_tweet_text.replace('\n', "\n> ")));
        lines.push(String::new());
    }
    if let Some(url) = source_url.filter(|u| !u.is_empty()) {
        lines.push("## Source's CTA (the URL the source points at)".into());
        lines.push(url.into());
        lines.push(String::new());
    }
    if !topic.is_empty() {
        lines.push("## Topic hint (from the rephrase step)".into());
        lines.push(topic.into());
        lines.push(String::new());
    }

    lines.push("## Your projects (pick the best match)".into());
    if projects.is_empty() {
        lines.push("(none)".into());
    } else {
        for p in projects {
            lines.push(format!("- **{}** - URL: {}", p.name, p.url));
        }
    }
    lines.push(String::new());
    lines.push(
        "Pick the project or course whose topic is closest to the source. Write a fresh, natural CTA inviting readers to check it out - do NOT copy the source's wording."
            .into(),
    );
    lines.join("\n").trim().to_string()
}

// ---- agent niche tone profiles ---------------------------------------------

pub const HUMANIZED_VOICE_RULES: &str = r#"HUMANIZED VOICE & MASS APPEAL MANDATE:
- Write like an authentic, passionate human posting on X to reach everyday people.
- NEVER use em-dashes, en-dashes, or double hyphens to connect clauses. Use natural periods, commas, or line breaks instead.
- STRICTLY BANNED AI WORDS: "delve", "testament", "transformative", "crucial", "pivotal", "landscape", "tapestry", "beacon", "unleash", "game-changer", "revolutionary", "realm", "nestled", "underscores", "in today's fast-paced world", "elevate".
- Use simple, punchy, conversational sentences that stir genuine feelings."#;

pub const CRYPTO_TONE_RULE: &str =
    "TONE MANDATE: The tone MUST ALWAYS be ENERGETIC and POSITIVE. Radiate high conviction, bullish momentum, excitement, and optimism. Speak with undeniable confidence about market shifts and financial freedom. Never sound robotic, timid, or cynical.";

pub const AI_TONE_RULE: &str =
    "TONE MANDATE: The tone MUST ALWAYS be FEAR MONGERING. Adopt an alarmist, urgent, and cautionary voice. Make people feel the immediate shock of uncontrollable AI acceleration, massive workforce replacement, and career obsolescence. Wake readers up from complacency with visceral reality.";

/// The mandatory tone rule for the niche.
pub fn get_niche_tone_rule(niche: &str) -> &'static str {
    if niche.trim().to_lowercase() == "crypto" {
        CRYPTO_TONE_RULE
    } else {
        AI_TONE_RULE
    }
}

/// The cashtag rule: `$AI` is forbidden for the AI niche, max one for crypto.
pub fn get_niche_cashtag_rule(niche: &str) -> &'static str {
    if niche.trim().to_lowercase() == "ai" {
        return "NO CASHTAGS OR TICKER SYMBOLS: NEVER use '$AI', '$AGI', or any cashtag with a '$'. In tech and AI discussions, cashtags look like crypto spam bots. Refer to AI simply as 'AI', 'artificial intelligence', 'agents', or 'models'. Zero dollar signs.";
    }
    "AT MOST ONE cashtag for crypto tokens (e.g. $BTC, $ETH). Never use two or more cashtags. Write other tickers or dollar amounts as plain text (e.g. 'USD 25k')."
}

/// System prompt for agent Option A (rephrase) with humanized voice rules.
pub fn get_agent_rephrase_system(niche: &str) -> String {
    format!(
        r#"You are an AUTONOMOUS REPHRASING AGENT for X (Twitter) posts. The user
has selected a source tweet and wants a high-impact rephrased version that captures
the core news or development in their voice, staying strictly within the 280-character limit.

Hard rules:

1. The MAIN tweet body MUST be <= 280 characters. Aim for 210-260 chars to leave headroom.
2. The MAIN tweet body MUST NOT contain ANY URL. URLs are forbidden in this post
   (a separate reply post carries the project link). This is a hard cost invariant.
3. REPHRASE, don't copy. Keep the core fact or development, but completely restructure
   the phrasing with personal conviction.
4. One clear idea per tweet. No hashtag spam, no emoji-stuffing, no cheesy clickbait.
5. {cashtag_rule}
6. {voice_rules}
7. {tone_rule}

Return a single JSON object with this exact shape - no extra keys, no markdown fences:

{{
  "main":      "<the rephrased tweet, <=280 chars, no URL, no em-dashes>",
  "topic":     "<one short phrase: the source's topic>",
  "reasoning": "<one short sentence: how this take reflects the required tone>"
}}"#,
        cashtag_rule = get_niche_cashtag_rule(niche),
        voice_rules = HUMANIZED_VOICE_RULES,
        tone_rule = get_niche_tone_rule(niche),
    )
}

/// System prompt for agent Option B (original take) with humanized voice rules.
pub fn get_agent_original_take_system(niche: &str) -> String {
    format!(
        r#"You are an AUTONOMOUS ORIGINAL-TAKE AGENT for X (Twitter) posts.
The user selected a source tweet as research. Write a sharp, distinct opinion, insight,
or provocative perspective inspired by its topic. Do not just summarize or reword the source.

Hard rules:

1. The MAIN tweet body MUST be <= 280 characters. Aim for 210-260 chars.
2. The MAIN tweet body MUST NOT contain ANY URL.
3. Offer a distinct viewpoint, thesis, or bold observation grounded in the topic that resonates with the masses.
4. One clear idea. No hashtag spam, no emoji-stuffing.
5. {cashtag_rule}
6. {voice_rules}
7. {tone_rule}

Return a single JSON object with this exact shape - no extra keys, no markdown fences:

{{
  "main":      "<the original take, <=280 chars, no URL, no em-dashes>",
  "topic":     "<one short phrase: the topic>",
  "reasoning": "<one short sentence describing the unique angle and tone>"
}}"#,
        cashtag_rule = get_niche_cashtag_rule(niche),
        voice_rules = HUMANIZED_VOICE_RULES,
        tone_rule = get_niche_tone_rule(niche),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn niche_tone_rules_differ() {
        assert!(get_niche_tone_rule("crypto").contains("ENERGETIC"));
        assert!(get_niche_tone_rule("ai").contains("FEAR MONGERING"));
        // Unknown niches fall back to the AI rule, matching Python.
        assert_eq!(get_niche_tone_rule("anything"), AI_TONE_RULE);
    }

    #[test]
    fn cashtag_rules_differ_by_niche() {
        assert!(get_niche_cashtag_rule("crypto").contains("AT MOST ONE"));
        assert!(get_niche_cashtag_rule("ai").contains("NO CASHTAGS"));
        assert!(get_niche_cashtag_rule("ai").contains("Zero dollar signs"));
    }

    #[test]
    fn agent_prompts_embed_rules_and_valid_json_shape() {
        let a = get_agent_rephrase_system("crypto");
        assert!(a.contains(CRYPTO_TONE_RULE));
        assert!(a.contains(HUMANIZED_VOICE_RULES));
        assert!(a.contains("\"main\""));
        assert!(a.contains("\"topic\""));
        assert!(a.contains("\"reasoning\""));

        let b = get_agent_original_take_system("ai");
        assert!(b.contains(AI_TONE_RULE));
        // Braces in the prompt must stay literal, not become placeholders.
        assert!(b.contains("{\n  \"main\""));
    }

    #[test]
    fn rephrase_user_quotes_multiline_sources() {
        let msg = build_rephrase_user(
            "line one\nline two",
            "alice",
            None,
            "",
            0,
            "",
            None,
        );
        assert!(msg.contains("> line one\n> line two"), "{msg}");
        assert!(msg.contains("By @alice"));
    }

    #[test]
    fn rephrase_user_warns_against_copying_the_source_url() {
        let msg = build_rephrase_user(
            "text",
            "alice",
            Some("https://src.com"),
            "",
            0,
            "",
            None,
        );
        assert!(msg.contains("$0.200 URL-surcharge"), "{msg}");
        assert!(msg.contains("https://src.com"));
    }

    #[test]
    fn rephrase_user_lists_winning_examples_first() {
        let examples = vec!["winner one".to_string(), "winner two".to_string()];
        let msg = build_rephrase_user("t", "a", None, "", 0, "", Some(&examples));
        let win_idx = msg.find("Past high-performing").unwrap();
        let insp_idx = msg.find("Inspiration tweet").unwrap();
        assert!(win_idx < insp_idx);
        assert!(msg.contains("1. winner one"));
        assert!(msg.contains("2. winner two"));
    }

    #[test]
    fn rephrase_user_limits_winning_examples_to_three() {
        let examples: Vec<String> = (1..=5).map(|i| format!("w{i}")).collect();
        let msg = build_rephrase_user("t", "a", None, "", 0, "", Some(&examples));
        assert!(msg.contains("3. w3"));
        assert!(!msg.contains("4. w4"));
    }

    #[test]
    fn rephrase_user_handles_empty_context() {
        let msg = build_rephrase_user("", "", None, "", 0, "", None);
        assert_eq!(msg, "Free write. No inspiration, no context, no tone constraint.");
    }

    #[test]
    fn match_user_lists_every_project() {
        let projects = vec![
            ProjectRef::new("Alpha", "https://a.com"),
            ProjectRef::new("Beta", "https://b.com"),
        ];
        let msg = build_match_user("src", "alice", Some("https://s.com"), "defi", &projects);
        assert!(msg.contains("- **Alpha** - URL: https://a.com"));
        assert!(msg.contains("- **Beta** - URL: https://b.com"));
        assert!(msg.contains("defi"));
        assert!(msg.contains("https://s.com"));
    }

    #[test]
    fn match_user_reports_empty_project_list() {
        let msg = build_match_user("src", "alice", None, "topic", &[]);
        assert!(msg.contains("(none)"), "{msg}");
    }

    #[test]
    fn system_prompts_state_the_cost_invariant() {
        assert!(DRAFT_SYSTEM.contains("$0.200"));
        assert!(DRAFT_SYSTEM.contains("280"));
        assert!(MATCH_SYSTEM.contains("$0.200"));
        assert!(ORIGINAL_TAKE_SYSTEM.contains("280"));
    }
}