//! OAuth 2.0 Authorization Code with PKCE for X.
//!
//! Ported from `src/x_auto/x/auth.py`.
//!
//! Scopes (must match the developer portal):
//!   `tweet.read tweet.write users.read media.write offline.access`
//!
//! Token storage: `data/<niche>/oauth_tokens.json`
//! ```json
//! { "x_user": { "access_token": "...", "refresh_token": "...",
//!   "expires_at": "2026-08-27T12:34:56+00:00", "scope": "...",
//!   "bearer_token": "..." } }
//! ```
//!
//! The refresh path is the subtle part: two processes (the app and
//! `auth_setup`) can rotate the same refresh token concurrently, so
//! [`TokenManager::access_token`] re-reads the file before refreshing and
//! prefers a freshly-rotated bundle when a refresh races.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use chrono::{DateTime, Duration, Utc};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::config::Settings;
use crate::error::{Error, Result};

pub const AUTH_URL: &str = "https://x.com/i/oauth2/authorize";
pub const TOKEN_URL: &str = "https://api.x.com/2/oauth2/token";

pub const DEFAULT_SCOPES: &str = "tweet.read tweet.write users.read media.write offline.access";

/// Refresh this far before actual expiry to avoid racing the deadline.
const REFRESH_LEEWAY_MINUTES: i64 = 5;

fn b64url_nopad(data: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(data)
}

/// A PKCE verifier/challenge pair (S256).
#[derive(Debug, Clone)]
pub struct PkcePair {
    pub code_verifier: String,
    pub code_challenge: String,
}

/// Generate a fresh PKCE pair: 48 random bytes, base64url, unpadded.
pub fn new_pkce_pair() -> PkcePair {
    let mut buf = [0u8; 48];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    let code_verifier = b64url_nopad(&buf);
    let digest = Sha256::digest(code_verifier.as_bytes());
    PkcePair {
        code_verifier,
        code_challenge: b64url_nopad(&digest),
    }
}

/// Opaque CSRF state for the authorize redirect.
pub fn new_state() -> String {
    let mut buf = [0u8; 24];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    b64url_nopad(&buf)
}

/// Build the X authorize URL the browser is sent to.
pub fn build_authorize_url(
    client_id: &str,
    redirect_uri: &str,
    scopes: &str,
    state: &str,
    code_challenge: &str,
) -> String {
    // Hand-built so parameter order matches the Python original exactly.
    let encoded = |s: &str| {
        s.bytes()
            .map(|b| match b {
                b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                    (b as char).to_string()
                }
                b' ' => "+".to_string(),
                _ => format!("%{:02X}", b),
            })
            .collect::<String>()
    };
    format!(
        "{AUTH_URL}?response_type=code&client_id={}&redirect_uri={}&scope={}&state={}\
&code_challenge={}&code_challenge_method=S256",
        encoded(client_id),
        encoded(redirect_uri),
        encoded(scopes),
        encoded(state),
        encoded(code_challenge),
    )
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TokenBundle {
    pub access_token: String,
    #[serde(default)]
    pub refresh_token: String,
    pub expires_at: DateTime<Utc>,
    #[serde(default)]
    pub scope: String,
    #[serde(default)]
    pub bearer_token: String,
}

impl TokenBundle {
    pub fn is_expired(&self) -> bool {
        Utc::now() >= self.expires_at
    }

    /// True within the refresh leeway window, so we refresh proactively.
    pub fn needs_refresh(&self) -> bool {
        Utc::now() >= self.expires_at - Duration::minutes(REFRESH_LEEWAY_MINUTES)
    }
}

/// File-backed token store at `data/<niche>/oauth_tokens.json`.
pub struct TokenStore {
    path: PathBuf,
}

#[derive(Serialize, Deserialize)]
struct TokenFile {
    x_user: TokenBundle,
}

impl TokenStore {
    pub fn new(path: &Path) -> Self {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        Self { path: path.to_path_buf() }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> Option<TokenBundle> {
        let raw = std::fs::read_to_string(&self.path).ok()?;
        let parsed: TokenFile = serde_json::from_str(&raw).ok()?;
        Some(parsed.x_user)
    }

    /// Write via temp file + rename so a crash can't leave a truncated
    /// token file (which would force a full re-auth).
    pub fn save(&self, bundle: &TokenBundle) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::Io(format!("create token dir: {e}")))?;
        }
        let body = serde_json::to_string_pretty(&TokenFile { x_user: bundle.clone() })
            .map_err(|e| Error::Io(format!("serialize token: {e}")))?;
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, body).map_err(|e| Error::Io(format!("write token: {e}")))?;
        std::fs::rename(&tmp, &self.path)
            .map_err(|e| Error::Io(format!("rename token: {e}")))?;
        Ok(())
    }
}

/// Exchange an authorization code for an access + refresh token.
pub async fn exchange_code(
    http: &reqwest::Client,
    client_id: &str,
    client_secret: &str,
    code: &str,
    code_verifier: &str,
    redirect_uri: &str,
) -> Result<TokenBundle> {
    let form: Vec<(&str, String)> = vec![
        ("grant_type", "authorization_code".into()),
        ("code", code.to_string()),
        ("client_id", client_id.to_string()),
        ("code_verifier", code_verifier.to_string()),
        ("redirect_uri", redirect_uri.to_string()),
    ];
    // When the client has a secret, X's token endpoint wants HTTP Basic.
    // The Python original also sent the fields; keep both for parity.
    let _ = client_secret;
    let resp = post_token(http, &form, client_id, client_secret).await?;
    bundle_from_token_response(&resp)
}

/// Use a refresh token to get a new access token (rotating the refresh).
pub async fn refresh_tokens(
    http: &reqwest::Client,
    client_id: &str,
    client_secret: &str,
    refresh_token: &str,
) -> Result<TokenBundle> {
    let form: Vec<(&str, String)> = vec![
        ("grant_type", "refresh_token".into()),
        ("refresh_token", refresh_token.to_string()),
        ("client_id", client_id.to_string()),
    ];
    let resp = post_token(http, &form, client_id, client_secret).await?;
    bundle_from_token_response(&resp)
}

async fn post_token(
    http: &reqwest::Client,
    form: &[(&str, String)],
    client_id: &str,
    client_secret: &str,
) -> Result<serde_json::Value> {
    let mut req = http
        .post(TOKEN_URL)
        .header("Content-Type", "application/x-www-form-urlencoded");
    if !client_secret.is_empty() {
        req = req.basic_auth(client_id, Some(client_secret));
    }
    let resp = req.form(form).send().await.map_err(|e| {
        Error::Auth(format!("token request failed: {}", redact_reqwest(&e)))
    })?;
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(Error::Auth(format!(
            "token endpoint returned HTTP {}: {}",
            status.as_u16(),
            safe_error_detail(&body)
        )));
    }
    serde_json::from_str(&body).map_err(|e| Error::Auth(format!("token response invalid JSON: {e}")))
}

fn bundle_from_token_response(body: &serde_json::Value) -> Result<TokenBundle> {
    let access_token = body
        .get("access_token")
        .and_then(|v| v.as_str())
        .ok_or_else(|| Error::Auth("token response missing access_token".into()))?
        .to_string();
    let refresh_token = body
        .get("refresh_token")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let scope = body
        .get("scope")
        .and_then(|v| v.as_str())
        .unwrap_or(DEFAULT_SCOPES)
        .to_string();
    let expires_in = body
        .get("expires_in")
        .and_then(|v| v.as_i64())
        .unwrap_or(7200);
    Ok(TokenBundle {
        access_token,
        refresh_token,
        expires_at: Utc::now() + Duration::seconds(expires_in),
        scope,
        bearer_token: String::new(),
    })
}

/// Extract OAuth's safe error fields without echoing request credentials.
fn safe_error_detail(body: &str) -> String {
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(body) {
        let mut parts: Vec<String> = Vec::new();
        for key in ["error", "error_description", "title", "detail"] {
            if let Some(s) = v.get(key).and_then(|x| x.as_str()) {
                if !s.is_empty() && !parts.contains(&s.to_string()) {
                    parts.push(s.to_string());
                }
            }
        }
        if !parts.is_empty() {
            return parts.join(" — ");
        }
    }
    body.trim().chars().take(500).collect()
}

fn redact_reqwest(e: &reqwest::Error) -> String {
    // reqwest's Display includes the URL; the URL here is the token
    // endpoint and carries no credentials, so this is safe to surface.
    e.to_string()
}

/// Auto-refreshing access to the stored token bundle.
///
/// Wrapped in a `Mutex` because Tauri commands are `Send + Sync`.
pub struct TokenManager {
    settings: Settings,
    store: TokenStore,
    cached: Mutex<Option<TokenBundle>>,
    http: reqwest::Client,
}

impl TokenManager {
    fn build_http() -> Result<reqwest::Client> {
        reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .map_err(|e| Error::Io(format!("build http client: {e}")))
    }

    pub fn new(settings: Settings) -> Result<Self> {
        let mut path = settings.data_dir.join("oauth_tokens.json");
        // Legacy shared location, used before per-niche directories.
        if !path.exists() {
            let fallback = settings.repo_root.join("data").join("oauth_tokens.json");
            path = fallback;
        }
        Ok(Self {
            store: TokenStore::new(&path),
            settings,
            cached: Mutex::new(None),
            http: Self::build_http()?,
        })
    }

    pub fn with_store(settings: Settings, store: TokenStore) -> Result<Self> {
        Ok(Self {
            settings,
            store,
            cached: Mutex::new(None),
            http: Self::build_http()?,
        })
    }

    pub fn store_path(&self) -> &Path {
        self.store.path()
    }

    fn load_or_raise(&self) -> Result<TokenBundle> {
        let mut cached = self.cached.lock().map_err(|_| Error::Auth("token mutex poisoned".into()))?;
        if cached.is_none() {
            let loaded = self.store.load().ok_or_else(|| {
                Error::Auth(
                    "X OAuth tokens not found. Run: cargo run --bin auth_setup".into(),
                )
            })?;
            *cached = Some(loaded);
        }
        Ok(cached.clone().expect("just populated"))
    }

    /// App-only bearer token (cheap, no refresh).
    pub fn bearer(&self) -> Result<String> {
        let bundle = self.load_or_raise()?;
        if !bundle.bearer_token.is_empty() {
            Ok(bundle.bearer_token)
        } else {
            Ok(self.settings.x.bearer_token.clone())
        }
    }

    /// A valid user-context access token, refreshing when needed.
    pub async fn access_token(&self) -> Result<String> {
        let mut bundle = self.load_or_raise()?;

        if bundle.needs_refresh() {
            // Another process (auth_setup, or a previous app run) may have
            // replaced the file while we held a stale bundle.
            if let Some(stored) = self.store.load() {
                if stored.access_token != bundle.access_token {
                    bundle = stored;
                }
            }
        }

        if bundle.needs_refresh() && !bundle.refresh_token.is_empty() {
            match refresh_tokens(
                &self.http,
                &self.settings.x.client_id,
                &self.settings.x.client_secret,
                &bundle.refresh_token,
            )
            .await
            {
                Ok(mut new_bundle) => {
                    // X only reissues a refresh token sometimes; keep the old
                    // one if it comes back empty or we'd lose refresh access.
                    if new_bundle.refresh_token.is_empty() {
                        new_bundle.refresh_token = bundle.refresh_token.clone();
                    }
                    new_bundle.bearer_token = if bundle.bearer_token.is_empty() {
                        self.settings.x.bearer_token.clone()
                    } else {
                        bundle.bearer_token.clone()
                    };
                    self.store.save(&new_bundle)?;
                    if let Ok(mut c) = self.cached.lock() {
                        *c = Some(new_bundle.clone());
                    }
                    return Ok(new_bundle.access_token);
                }
                Err(e) => {
                    // A concurrent refresh may have rotated the token while our
                    // request was in flight. Prefer that fresh bundle.
                    if let Some(stored) = self.store.load() {
                        if stored.access_token != bundle.access_token && !stored.needs_refresh() {
                            if let Ok(mut c) = self.cached.lock() {
                                *c = Some(stored.clone());
                            }
                            return Ok(stored.access_token);
                        }
                    }
                    return Err(Error::Auth(format!("Token refresh failed ({e})")));
                }
            }
        }
        Ok(bundle.access_token)
    }

    /// Persist a brand-new bundle (from the auth setup flow).
    pub fn save_initial(&self, bundle: TokenBundle) -> Result<()> {
        self.store.save(&bundle)?;
        if let Ok(mut c) = self.cached.lock() {
            *c = Some(bundle);
        }
        Ok(())
    }

    /// Drop the cached bundle, forcing a re-read on next access.
    pub fn invalidate_cache(&self) {
        if let Ok(mut c) = self.cached.lock() {
            *c = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bundle_expires_in(minutes: i64) -> TokenBundle {
        TokenBundle {
            access_token: "acc".into(),
            refresh_token: "ref".into(),
            expires_at: Utc::now() + Duration::minutes(minutes),
            scope: DEFAULT_SCOPES.into(),
            bearer_token: String::new(),
        }
    }

    #[test]
    fn pkce_challenge_matches_sha256_of_verifier() {
        let pair = new_pkce_pair();
        let expected = b64url_nopad(&Sha256::digest(pair.code_verifier.as_bytes()));
        assert_eq!(pair.code_challenge, expected);
        assert!(!pair.code_verifier.contains('='), "must be unpadded");
    }

    #[test]
    fn pkce_pairs_are_unique() {
        assert_ne!(new_pkce_pair().code_verifier, new_pkce_pair().code_verifier);
        assert_ne!(new_state(), new_state());
    }

    #[test]
    fn authorize_url_contains_every_pkce_parameter() {
        let url = build_authorize_url(
            "client123",
            "http://127.0.0.1:8765/callback",
            DEFAULT_SCOPES,
            "stateabc",
            "challengexyz",
        );
        assert!(url.starts_with(AUTH_URL));
        assert!(url.contains("response_type=code"));
        assert!(url.contains("client_id=client123"));
        assert!(url.contains("redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback"));
        assert!(url.contains("code_challenge_method=S256"));
        assert!(url.contains("state=stateabc"));
        assert!(url.contains("code_challenge=challengexyz"));
    }

    #[test]
    fn scopes_are_url_encoded_as_plus() {
        let url = build_authorize_url("c", "r", "a b", "s", "ch");
        assert!(url.contains("scope=a+b"), "{url}");
    }

    #[test]
    fn refresh_leeway_triggers_before_expiry() {
        assert!(bundle_expires_in(2).needs_refresh(), "2m out should refresh");
        assert!(!bundle_expires_in(60).needs_refresh(), "60m out is fine");
        assert!(bundle_expires_in(-1).is_expired());
    }

    #[test]
    fn store_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let store = TokenStore::new(&dir.path().join("oauth_tokens.json"));
        assert!(store.load().is_none());

        let b = bundle_expires_in(120);
        store.save(&b).unwrap();
        let back = store.load().unwrap();
        assert_eq!(back.access_token, "acc");
        assert_eq!(back.refresh_token, "ref");
        assert_eq!(back.scope, DEFAULT_SCOPES);
        let mut rotated = b;
        rotated.access_token = "rotated".into();
        store.save(&rotated).unwrap();
        assert_eq!(store.load().unwrap(), rotated);
    }

    #[test]
    fn store_tolerates_malformed_file() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("oauth_tokens.json");
        std::fs::write(&p, "{ truncated").unwrap();
        assert!(TokenStore::new(&p).load().is_none());
    }

    #[tokio::test]
    async fn missing_token_file_gives_actionable_error() {
        let dir = tempfile::tempdir().unwrap();
        let mut settings = crate::config::get_settings("crypto");
        settings.data_dir = dir.path().to_path_buf();
        let store = TokenStore::new(&dir.path().join("oauth_tokens.json"));
        let mgr = TokenManager::with_store(settings, store).unwrap();
        let err = mgr.access_token().await.unwrap_err();
        assert!(matches!(err, Error::Auth(_)));
        assert!(err.to_string().contains("auth_setup"));
    }

    #[tokio::test]
    async fn manager_created_before_authorization_reads_new_shared_tokens() {
        let dir = tempfile::tempdir().unwrap();
        let mut settings = crate::config::get_settings("crypto");
        settings.repo_root = dir.path().to_path_buf();
        settings.data_dir = dir.path().join("data/crypto");
        let mgr = TokenManager::new(settings).unwrap();
        let shared = dir.path().join("data/oauth_tokens.json");
        assert_eq!(mgr.store_path(), shared);
        assert!(mgr.access_token().await.is_err());
        TokenStore::new(&shared).save(&bundle_expires_in(120)).unwrap();
        assert_eq!(mgr.access_token().await.unwrap(), "acc");
    }

    #[tokio::test]
    async fn valid_bundle_is_returned_without_network() {
        let dir = tempfile::tempdir().unwrap();
        let mut settings = crate::config::get_settings("crypto");
        settings.data_dir = dir.path().to_path_buf();
        let store = TokenStore::new(&dir.path().join("oauth_tokens.json"));
        store.save(&bundle_expires_in(120)).unwrap();
        let mgr = TokenManager::with_store(settings, store).unwrap();
        assert_eq!(mgr.access_token().await.unwrap(), "acc");
    }

    #[test]
    fn safe_error_detail_prefers_structured_fields() {
        let body = r#"{"error":"invalid_grant","error_description":"bad code"}"#;
        let d = safe_error_detail(body);
        assert!(d.contains("invalid_grant"));
        assert!(d.contains("bad code"));
        // Plain text is truncated, not dropped.
        let plain = safe_error_detail("just some text");
        assert_eq!(plain, "just some text");
    }
}
