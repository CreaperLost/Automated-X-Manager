//! MiniMax chat client. The endpoint is OpenAI-compatible.
//!
//! Ported from `src/x_auto/ai/client.py`. The real work is robustness:
//! MiniMax M-series models emit a leading `<think>…</think>` block and
//! sometimes wrap JSON in prose, so [`parse_and_validate`] strips the
//! reasoning, extracts the first balanced object, and checks the
//! required keys are strings.

use std::time::Duration;

use regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

use crate::config::Settings;
use crate::error::{Error, Result};

/// A model reply that failed validation, or a call that never landed.
pub type DraftGenerationError = Error;

/// Sync MiniMax client. One instance per process.
pub struct AiClient {
    settings: Settings,
    http: reqwest::Client,
}

impl AiClient {
    pub fn new(settings: Settings) -> Result<Self> {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(120))
            .build()
            .map_err(|e| Error::Io(format!("build ai http client: {e}")))?;
        Ok(Self { settings, http })
    }

    /// Whether `MINIMAX_API_KEY` is present.
    pub fn configured(&self) -> bool {
        self.settings.minimax.configured()
    }

    /// Call the chat API and return the validated JSON object.
    ///
    /// Retries up to `max_retries` times total. Failure modes:
    ///
    /// * **network / 5xx** — exponential backoff, then retry.
    /// * **no JSON in the reply** (the model spent its budget inside a
    ///   `<think>` block) — follow up with a strict "JSON only" prompt.
    /// * **malformed JSON** — retry with the same prompt.
    pub async fn generate_draft(
        &self,
        system: &str,
        user: &str,
        max_retries: u32,
        required_keys: &[&str],
    ) -> Result<Map<String, Value>> {
        if !self.configured() {
            return Err(Error::Ai(
                "MINIMAX_API_KEY is not configured. Set it in .env and restart the app.".into(),
            ));
        }
        let mm = &self.settings.minimax;
        let mut last_err: Option<Error> = None;

        for attempt in 0..=max_retries {
            let messages = messages_for_attempt(system, user, attempt, last_err.is_some());
            let payload = json!({
                "model": mm.model_id,
                "response_format": { "type": "json_object" },
                "messages": messages,
                "temperature": mm.temperature,
                "max_tokens": mm.max_tokens,
            });

            let call = self
                .http
                .post(format!("{}/chat/completions", mm.base_url.trim_end_matches('/')))
                .bearer_auth(&mm.api_key)
                .json(&payload)
                .send()
                .await;

            let resp = match call {
                Ok(r) => r,
                Err(e) => {
                    last_err = Some(Error::Ai(format!("MiniMax call failed: {e}")));
                    if attempt == max_retries {
                        return Err(last_err.expect("just set"));
                    }
                    sleep(1 + attempt as u64 * 2).await;
                    continue;
                }
            };

            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            if !status.is_success() {
                last_err = Some(Error::Ai(format!(
                    "MiniMax call failed: HTTP {}: {}",
                    status.as_u16(),
                    body.chars().take(300).collect::<String>()
                )));
                if attempt == max_retries {
                    return Err(last_err.expect("just set"));
                }
                sleep(1 + attempt as u64 * 2).await;
                continue;
            }

            let content = extract_content(&body);
            match parse_and_validate(&content, required_keys) {
                Ok(map) => return Ok(map),
                Err(e) => {
                    last_err = Some(e);
                    if attempt < max_retries {
                        sleep(1 + attempt as u64).await;
                        continue;
                    }
                    return Err(last_err.expect("just set"));
                }
            }
        }

        Err(last_err.unwrap_or_else(|| Error::Ai("MiniMax call failed after retries".into())))
    }
}

async fn sleep(seconds: u64) {
    tokio::time::sleep(Duration::from_secs(seconds)).await;
}

/// The first attempt sends system+user. A retry after a "no JSON" error
/// appends a strict follow-up so the model skips the think block.
fn messages_for_attempt(system: &str, user: &str, attempt: u32, had_error: bool) -> Vec<Value> {
    let mut messages = vec![
        json!({ "role": "system", "content": system }),
        json!({ "role": "user", "content": user }),
    ];
    if attempt > 0 && had_error {
        messages.push(json!({
            "role": "user",
            "content": "Your previous reply had no JSON object. Reply again with \
ONLY the JSON object - no chain-of-thought, no explanation, no markdown fences. \
The first character of your reply must be '{'."
        }));
    }
    messages
}

/// Pull `choices[0].message.content` out of a chat-completions response.
fn extract_content(body: &str) -> String {
    let Ok(v) = serde_json::from_str::<Value>(body) else {
        return String::new();
    };
    v["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Parse the assistant's text as a JSON object and check the required keys.
///
/// Tolerant of the two real-world MiniMax quirks: a leading
/// `<think>…</think>` block even under `response_format`, and JSON
/// wrapped in prose. Locates the first `{` and its matching `}`.
pub fn parse_and_validate(content: &str, required_keys: &[&str]) -> Result<Map<String, Value>> {
    if content.trim().is_empty() {
        return Err(Error::Ai("empty response from MiniMax".into()));
    }
    let cleaned = strip_think_blocks(content);
    let json_text = extract_json_object(&cleaned);
    if json_text.is_empty() {
        return Err(Error::Ai("MiniMax returned no JSON object in its reply".into()));
    }
    let data: Value = serde_json::from_str(&json_text)
        .map_err(|e| Error::Ai(format!("MiniMax returned invalid JSON: {e}")))?;
    let Value::Object(map) = data else {
        return Err(Error::Ai("MiniMax returned a non-object JSON value".into()));
    };
    for key in required_keys {
        match map.get(*key) {
            None => {
                return Err(Error::Ai(format!(
                    "MiniMax JSON missing required key '{key}'"
                )))
            }
            Some(v) if !v.is_string() => {
                return Err(Error::Ai(format!(
                    "MiniMax JSON '{key}' must be a string"
                )))
            }
            _ => {}
        }
    }
    Ok(map)
}

/// Remove any `<think>…</think>` blocks the model inserted.
pub fn strip_think_blocks(text: &str) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"(?s)<think>.*?</think>").expect("valid regex"));
    re.replace_all(text, "").trim().to_string()
}

/// The first balanced `{…}` substring, or `""`.
///
/// Tolerant of leading prose and trailing characters, which is what the
/// chatty-preamble case produces.
pub fn extract_json_object(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let Some(start) = chars.iter().position(|c| *c == '{') else {
        return String::new();
    };
    let mut depth = 0i32;
    let mut in_string = false;
    let mut escape = false;
    for i in start..chars.len() {
        let ch = chars[i];
        if in_string {
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            '"' => in_string = true,
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return chars[start..=i].iter().collect();
                }
            }
            _ => {}
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEYS: &[&str] = &["main", "topic", "reasoning"];

    #[test]
    fn parses_a_clean_object() {
        let out = parse_and_validate(
            r#"{"main":"hi","topic":"defi","reasoning":"because"}"#,
            KEYS,
        )
        .unwrap();
        assert_eq!(out["main"], "hi");
    }

    #[test]
    fn strips_a_leading_think_block() {
        let raw = r#"<think>let me think about this for a while</think>{"main":"hi","topic":"t","reasoning":"r"}"#;
        let out = parse_and_validate(raw, KEYS).unwrap();
        assert_eq!(out["main"], "hi");
    }

    #[test]
    fn extracts_json_wrapped_in_prose() {
        let raw = "Here is the JSON you asked for:\n{\"main\":\"hi\",\"topic\":\"t\",\"reasoning\":\"r\"}\nHope that helps!";
        let out = parse_and_validate(raw, KEYS).unwrap();
        assert_eq!(out["main"], "hi");
    }

    #[test]
    fn handles_nested_objects_and_braces_in_strings() {
        let raw = r#"{"main":"a } brace","topic":"t","reasoning":{"deep":1}}"#;
        // `reasoning` must be a string, so this fails validation, not parsing.
        let err = parse_and_validate(raw, KEYS).unwrap_err();
        assert!(err.to_string().contains("must be a string"));

        let ok = parse_and_validate(
            r#"{"main":"a } brace","topic":"t","reasoning":"ok"}"#,
            KEYS,
        )
        .unwrap();
        assert_eq!(ok["main"], "a } brace");
    }

    #[test]
    fn empty_and_jsonless_replies_are_errors() {
        assert!(parse_and_validate("", KEYS).is_err());
        assert!(parse_and_validate("   ", KEYS).is_err());
        let err = parse_and_validate("no json at all", KEYS).unwrap_err();
        assert!(err.to_string().contains("no JSON object"));
    }

    #[test]
    fn malformed_json_is_an_error() {
        let err = parse_and_validate(r#"{"main":"hi","#, KEYS).unwrap_err();
        assert!(err.to_string().contains("invalid JSON") || err.to_string().contains("no JSON"));
    }

    #[test]
    fn missing_required_key_is_named() {
        let err = parse_and_validate(r#"{"main":"hi","topic":"t"}"#, KEYS).unwrap_err();
        assert!(err.to_string().contains("reasoning"), "{err}");
    }

    #[test]
    fn non_string_required_value_is_rejected() {
        let err = parse_and_validate(
            r#"{"main":123,"topic":"t","reasoning":"r"}"#,
            KEYS,
        )
        .unwrap_err();
        assert!(err.to_string().contains("'main' must be a string"));
    }

    #[test]
    fn top_level_array_is_rejected() {
        // A bare array has no `{`, so the extractor finds nothing to parse.
        let err = parse_and_validate("[1, 2, 3]", KEYS).unwrap_err();
        assert!(err.to_string().contains("no JSON object"), "{err}");

        // An array *containing* an object: the extractor pulls the inner
        // object out, so validation fails on the missing keys instead.
        let err = parse_and_validate(r#"[{"main":"hi"}]"#, KEYS).unwrap_err();
        assert!(err.to_string().contains("missing required key 'topic'"), "{err}");
    }

    #[test]
    fn unclosed_object_returns_empty() {
        assert_eq!(extract_json_object(r#"{"main":"hi""#), "");
        assert_eq!(extract_json_object("no braces"), "");
    }

    #[test]
    fn extractor_respects_string_boundaries() {
        assert_eq!(extract_json_object(r#"{"a":"{","b":"}"}"#), r#"{"a":"{","b":"}"}"#);
    }

    #[test]
    fn retry_adds_the_strict_followup() {
        let first = messages_for_attempt("sys", "user", 0, false);
        assert_eq!(first.len(), 2);
        let second = messages_for_attempt("sys", "user", 1, true);
        assert_eq!(second.len(), 3);
        assert!(second[2]["content"].as_str().unwrap().contains("ONLY the JSON"));
        // No prior error means no follow-up even on a later attempt.
        assert_eq!(messages_for_attempt("sys", "user", 2, false).len(), 2);
    }

    #[test]
    fn content_extraction_reads_chat_shape() {
        let body = r#"{"choices":[{"message":{"content":"  hello  "}}]}"#;
        assert_eq!(extract_content(body), "hello");
        assert_eq!(extract_content("not json"), "");
        assert_eq!(extract_content(r#"{"choices":[]}"#), "");
    }

    #[tokio::test]
    async fn unconfigured_client_fails_before_any_request() {
        let mut settings = crate::config::get_settings("crypto");
        settings.minimax.api_key = String::new();
        let client = AiClient::new(settings).unwrap();
        assert!(!client.configured());
        let err = client.generate_draft("sys", "user", 2, KEYS).await.unwrap_err();
        assert!(err.to_string().contains("MINIMAX_API_KEY"));
    }
}