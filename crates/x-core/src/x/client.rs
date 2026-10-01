//! Thin async HTTP wrapper around the X API v2 endpoints we use.
//!
//! Ported from `src/x_auto/x/client.py`.
//!
//! Endpoints (verified Aug 2026):
//! ```text
//! GET  /2/users/by/username/:handle    Bearer  $0.010
//! GET  /2/users/:id/tweets            Bearer  $0.005 * N
//! POST /2/tweets                      User    $0.015 (plain) | $0.200 (URL inline)
//! DELETE /2/tweets/:id                User    $0.010
//! POST /2/media/upload                User    free
//! GET  /2/users/me                    User    $0.010
//! ```
//!
//! Rate-limit info is surfaced from response headers, and the client
//! retries once on 5xx/429 and once more on 401 after invalidating the
//! cached token.

use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::config::Settings;
use crate::error::{Error, Result};
use crate::x::auth::TokenManager;
use crate::x::costs::{SessionMeter, COST_POST_DELETED, COST_POST_PLAIN};

pub const API_BASE: &str = "https://api.x.com/2";
pub const USER_AGENT: &str = "X-Automation/0.1.0 (+https://docs.x.com)";

/// An X user (author or self).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct XUser {
    pub id: String,
    pub username: String,
    pub name: String,
}

/// A tweet as returned by X, before persistence.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct XTweet {
    pub id: String,
    pub text: String,
    pub author_id: String,
    /// ISO-8601 as X sends it.
    pub created_at: String,
    #[serde(default)]
    pub public_metrics: serde_json::Value,
    #[serde(default)]
    pub source_image_url: Option<String>,
}

/// Rate-limit state from the response headers.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub struct RateLimitInfo {
    pub limit: i64,
    pub remaining: i64,
    pub reset_unix: i64,
}

impl RateLimitInfo {
    pub fn seconds_to_reset(&self) -> i64 {
        let now = chrono::Utc::now().timestamp();
        (self.reset_unix - now).max(0)
    }
}

fn parse_rate_limit_headers(headers: &reqwest::header::HeaderMap) -> Option<RateLimitInfo> {
    let g = |k: &str| -> i64 {
        headers
            .get(k)
            .and_then(|v| v.to_str().ok())
            .and_then(|s| s.parse().ok())
            .unwrap_or(0)
    };
    if !headers.contains_key("x-rate-limit-limit") {
        return None;
    }
    Some(RateLimitInfo {
        limit: g("x-rate-limit-limit"),
        remaining: g("x-rate-limit-remaining"),
        reset_unix: g("x-rate-limit-reset"),
    })
}

fn should_retry(status: u16) -> bool {
    matches!(status, 429 | 500 | 502 | 503 | 504)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthKind {
    /// App-only bearer: cheap reads.
    Bearer,
    /// User context: writes, and `/users/me`.
    User,
}

/// X API client. One instance per process.
pub struct XClient {
    settings: Settings,
    tokens: Arc<TokenManager>,
    meter: Arc<std::sync::Mutex<SessionMeter>>,
    http: reqwest::Client,
}

impl XClient {
    pub fn new(settings: Settings, tokens: Arc<TokenManager>) -> Result<Self> {
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .user_agent(USER_AGENT)
            .default_headers({
                let mut h = reqwest::header::HeaderMap::new();
                h.insert(
                    reqwest::header::ACCEPT,
                    reqwest::header::HeaderValue::from_static("application/json"),
                );
                h
            })
            .build()
            .map_err(|e| Error::Io(format!("build x http client: {e}")))?;
        Ok(Self {
            settings,
            tokens,
            meter: Arc::new(std::sync::Mutex::new(SessionMeter::new())),
            http,
        })
    }

    pub fn settings(&self) -> &Settings {
        &self.settings
    }
    pub fn tokens(&self) -> &Arc<TokenManager> {
        &self.tokens
    }

    /// Snapshot of the session meter.
    pub fn meter_snapshot(&self) -> SessionMeter {
        self.meter.lock().map(|m| m.clone()).unwrap_or_default()
    }

    fn meter_add<F: FnOnce(&mut SessionMeter)>(&self, f: F) {
        if let Ok(mut m) = self.meter.lock() {
            f(&mut m);
        }
    }

    /// Centralised request helper: auth selection, rate-limit parsing,
    /// and the 5xx/429/401 retry budget.
    pub async fn request(
        &self,
        method: reqwest::Method,
        path: &str,
        auth: AuthKind,
        query: Option<&[(&str, String)]>,
        json_body: Option<&serde_json::Value>,
        form: Option<&[(&str, String)]>,
    ) -> Result<(serde_json::Value, Option<RateLimitInfo>)> {
        let url = format!("{API_BASE}{path}");

        // Retried up to twice (initial + 1), matching the Python budget.
        let mut last_err: Option<Error> = None;
        for attempt in 0..2u32 {
            let token = match auth {
                AuthKind::Bearer => {
                    if self.settings.x.bearer_token.is_empty() {
                        return Err(Error::Auth("X_BEARER_TOKEN is not configured".into()));
                    }
                    self.settings.x.bearer_token.clone()
                }
                AuthKind::User => self.tokens.access_token().await.map_err(|e| {
                    // Surface token problems as AuthExpired, matching the
                    // Python AuthExpiredError mapping.
                    Error::Auth(e.to_string())
                })?,
            };

            let mut req = self.http.request(method.clone(), &url).bearer_auth(token);
            if let Some(q) = query {
                req = req.query(q);
            }
            if let Some(j) = json_body {
                req = req.json(j);
            }
            if let Some(f) = form {
                req = req.form(f);
            }

            let resp = match req.send().await {
                Ok(r) => r,
                Err(e) => {
                    let err = Error::XApi {
                        status: 0,
                        detail: format!("network error: {e}"),
                        url: Some(path.to_string()),
                    };
                    if attempt == 1 {
                        return Err(err);
                    }
                    last_err = Some(err);
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                    continue;
                }
            };

            let status = resp.status();
            let rl = parse_rate_limit_headers(resp.headers());
            let retry_after_header = resp
                .headers()
                .get("retry-after")
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(5);
            let body_text = resp.text().await.unwrap_or_default();

            if status.as_u16() < 400 {
                let json = if body_text.trim().is_empty() {
                    serde_json::Value::Null
                } else {
                    serde_json::from_str(&body_text)
                        .map_err(|e| Error::XApi {
                            status: status.as_u16(),
                            detail: format!("invalid JSON response: {e}"),
                            url: Some(path.to_string()),
                        })?
                };
                return Ok((json, rl));
            }

            let code = status.as_u16();
            let detail: String = if body_text.is_empty() {
                "(no body)".to_string()
            } else {
                body_text.chars().take(500).collect()
            };

            if code == 401 && auth == AuthKind::User {
                if attempt == 0 {
                    // Force a re-read + refresh on the next attempt.
                    self.tokens.invalidate_cache();
                    last_err = Some(Error::XApi {
                        status: code,
                        detail: detail.clone(),
                        url: Some(path.to_string()),
                    });
                    continue;
                }
                return Err(Error::Auth(detail));
            }
            if code == 429 {
                let retry_after = retry_after_header;
                if attempt == 0 {
                    last_err = Some(Error::RateLimited {
                        retry_after_seconds: retry_after,
                        url: Some(path.to_string()),
                    });
                    let wait = retry_after.min(30);
                    tokio::time::sleep(std::time::Duration::from_secs(wait)).await;
                    continue;
                }
                return Err(Error::RateLimited {
                    retry_after_seconds: retry_after,
                    url: Some(path.to_string()),
                });
            }
            if should_retry(code) {
                if attempt == 0 {
                    last_err = Some(Error::XApi {
                        status: code,
                        detail: detail.clone(),
                        url: Some(path.to_string()),
                    });
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                    continue;
                }
                return Err(Error::XApi {
                    status: code,
                    detail,
                    url: Some(path.to_string()),
                });
            }
            return Err(Error::XApi {
                status: code,
                detail,
                url: Some(path.to_string()),
            });
        }

        Err(last_err.unwrap_or_else(|| Error::other("request failed without response")))
    }

    // ----- public endpoints -----

    /// `GET /2/users/by/username/:username` ($0.010).
    pub async fn get_user_by_username(&self, username: &str) -> Result<XUser> {
        let handle = username.trim_start_matches('@');
        let (body, _) = self
            .request(
                reqwest::Method::GET,
                &format!("/users/by/username/{handle}"),
                AuthKind::Bearer,
                None,
                None,
                None,
            )
            .await?;
        self.meter_add(|m| m.add_read_profile(1));
        let d = &body["data"];
        Ok(XUser {
            id: d.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            username: d
                .get("username")
                .and_then(|v| v.as_str())
                .unwrap_or(handle)
                .to_string(),
            name: d.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        })
    }

    /// `GET /2/users/:id/tweets` ($0.005 each).
    ///
    /// Picks the first attached photo as `source_image_url`; X returns
    /// media separately under `includes`, keyed by `media_key`.
    pub async fn get_user_tweets(
        &self,
        user_id: &str,
        max_results: usize,
        exclude: &[String],
    ) -> Result<Vec<XTweet>> {
        let max = max_results.clamp(5, 100);
        let fields = "created_at,public_metrics,author_id,attachments";
        let media_fields = "media_key,type,url,preview_image_url";
        let exclude_param = exclude.join(",");
        let query: Vec<(&str, String)> = vec![
            ("max_results", max.to_string()),
            ("tweet.fields", fields.to_string()),
            ("expansions", "attachments.media_keys".to_string()),
            ("media.fields", media_fields.to_string()),
            ("exclude", exclude_param),
        ];
        let (body, _) = self
            .request(
                reqwest::Method::GET,
                &format!("/users/{user_id}/tweets"),
                AuthKind::Bearer,
                Some(&query),
                None,
                None,
            )
            .await?;

        // media_key -> url, for photo lookups.
        let mut media_by_key: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        if let Some(media) = body["includes"]["media"].as_array() {
            for m in media {
                let (Some(key), Some(url)) = (
                    m.get("media_key").and_then(|v| v.as_str()),
                    m.get("url").and_then(|v| v.as_str()),
                ) else {
                    continue;
                };
                if m.get("type").and_then(|v| v.as_str()) == Some("photo") {
                    media_by_key.insert(key.to_string(), url.to_string());
                }
            }
        }

        let mut tweets = Vec::new();
        if let Some(data) = body["data"].as_array() {
            for t in data {
                let mut source_image_url = None;
                if let Some(keys) = t["attachments"]["media_keys"].as_array() {
                    for k in keys {
                        if let Some(key) = k.as_str() {
                            if let Some(url) = media_by_key.get(key) {
                                source_image_url = Some(url.clone());
                                break;
                            }
                        }
                    }
                }
                tweets.push(XTweet {
                    id: t.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                    text: t.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                    author_id: t
                        .get("author_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or(user_id)
                        .to_string(),
                    created_at: t
                        .get("created_at")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    public_metrics: t
                        .get("public_metrics")
                        .cloned()
                        .unwrap_or(serde_json::Value::Object(Default::default())),
                    source_image_url,
                });
            }
        }
        let n = tweets.len() as i64;
        self.meter_add(|m| m.add_read_post(n));
        Ok(tweets)
    }

    /// `GET /2/users/me` ($0.010).
    pub async fn get_me(&self) -> Result<XUser> {
        let (body, _) = self
            .request(reqwest::Method::GET, "/users/me", AuthKind::User, None, None, None)
            .await?;
        let d = &body["data"];
        Ok(XUser {
            id: d.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            username: d.get("username").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            name: d.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        })
    }

    /// `POST /2/tweets` ($0.015 plain | $0.200 with an inline URL).
    ///
    /// Callers must keep URLs out of `text`; the publish flow enforces
    /// this via `validate_post_body`.
    pub async fn create_post(
        &self,
        text: &str,
        media_ids: Option<&[String]>,
        reply_to: Option<&str>,
    ) -> Result<String> {
        let mut body = serde_json::json!({ "text": text });
        if let Some(ids) = media_ids {
            if !ids.is_empty() {
                body["media"] = serde_json::json!({ "media_ids": ids });
            }
        }
        if let Some(parent) = reply_to {
            body["reply"] = serde_json::json!({ "in_reply_to_tweet_id": parent });
        }
        let (resp, _) = self
            .request(reqwest::Method::POST, "/tweets", AuthKind::User, None, Some(&body), None)
            .await?;
        self.meter_add(|m| m.add_write(COST_POST_PLAIN));
        resp["data"]["id"]
            .as_str()
            .map(|s| s.to_string())
            .ok_or_else(|| Error::XApi {
                status: 500,
                detail: format!("no tweet id in response: {resp}"),
                url: Some("/2/tweets".into()),
            })
    }

    /// `DELETE /2/tweets/:id` ($0.010).
    pub async fn delete_post(&self, post_id: &str) -> Result<()> {
        self.request(
            reqwest::Method::DELETE,
            &format!("/tweets/{post_id}"),
            AuthKind::User,
            None,
            None,
            None,
        )
        .await?;
        self.meter_add(|m| m.add_write(COST_POST_DELETED));
        Ok(())
    }

    /// `GET /2/tweets/:id` with `public_metrics` ($0.005).
    pub async fn get_tweet_metrics(&self, post_id: &str) -> Result<serde_json::Value> {
        let query = [("tweet.fields", "public_metrics".to_string())];
        let (body, _) = self
            .request(
                reqwest::Method::GET,
                &format!("/tweets/{post_id}"),
                AuthKind::Bearer,
                Some(&query),
                None,
                None,
            )
            .await?;
        self.meter_add(|m| m.add_read_post(1));
        Ok(body["data"]["public_metrics"].clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retry_decision_matches_retryable_statuses() {
        for s in [429, 500, 502, 503, 504] {
            assert!(should_retry(s), "{s} should retry");
        }
        for s in [400, 401, 403, 404, 422] {
            assert!(!should_retry(s), "{s} should not retry");
        }
    }

    #[test]
    fn rate_limit_headers_parse_or_return_none() {
        let mut headers = reqwest::header::HeaderMap::new();
        assert!(parse_rate_limit_headers(&headers).is_none());

        headers.insert("x-rate-limit-limit", "180".parse().unwrap());
        headers.insert("x-rate-limit-remaining", "179".parse().unwrap());
        headers.insert("x-rate-limit-reset", "9999999999".parse().unwrap());
        let rl = parse_rate_limit_headers(&headers).unwrap();
        assert_eq!(rl.limit, 180);
        assert_eq!(rl.remaining, 179);
        assert!(rl.seconds_to_reset() > 0);
    }

    #[test]
    fn seconds_to_reset_never_negative() {
        let rl = RateLimitInfo { limit: 1, remaining: 0, reset_unix: 0 };
        assert_eq!(rl.seconds_to_reset(), 0);
    }

    #[test]
    fn missing_bearer_is_an_auth_error_not_a_request() {
        let mut settings = crate::config::get_settings("crypto");
        settings.x.bearer_token = String::new();
        let dir = tempfile::tempdir().unwrap();
        let store = crate::x::auth::TokenStore::new(&dir.path().join("t.json"));
        let mgr = Arc::new(TokenManager::with_store(settings.clone(), store).unwrap());
        let client = XClient::new(settings, mgr).unwrap();
        // No bearer configured: fails before any network I/O.
        let rt = tokio::runtime::Runtime::new().unwrap();
        let err = rt
            .block_on(client.get_user_by_username("elonmusk"))
            .unwrap_err();
        assert!(matches!(err, Error::Auth(_)));
    }
}