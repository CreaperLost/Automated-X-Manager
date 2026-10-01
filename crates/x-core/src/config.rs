//! Configuration loading: env + YAML, with sensible defaults.
//!
//! Single source of truth for every setting the app uses.
//!
//! Resolution order (later wins):
//!   1. Hard-coded defaults in this module.
//!   2. `config/settings.yaml`.
//!   3. Environment variables (via `dotenvy` on `.env`).
//!
//! Ported from `src/x_auto/config.py`. Behaviour is kept identical,
//! including the niche-specific directory fallback and deep merge.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use serde::{Deserialize, Serialize};

/// Repository root: the directory containing `config/`, `data/`, `crates/`.
pub fn repo_root() -> PathBuf {
    // `CARGO_MANIFEST_DIR` = <repo>/crates/x-core at compile time. Two
    // parents up is the repo root.
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| manifest.to_path_buf())
}

pub fn data_dir() -> PathBuf {
    repo_root().join("data")
}

pub fn config_dir() -> PathBuf {
    repo_root().join("config")
}

pub const SUPPORTED_NICHES: [&str; 2] = ["crypto", "ai"];
pub const DEFAULT_NICHE: &str = "crypto";

/// X handles are 1–15 chars of `[A-Za-z0-9_]`.
fn is_valid_handle(handle: &str) -> bool {
    !handle.is_empty()
        && handle.chars().count() <= 15
        && handle
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_')
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Account {
    pub handle: String,
}

fn load_yaml(path: &Path) -> Option<serde_yaml::Value> {
    if !path.exists() {
        return None;
    }
    let raw = std::fs::read_to_string(path).ok()?;
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    serde_yaml::from_str(trimmed).ok()
}

/// Read, normalize, validate, and de-duplicate monitored X handles.
pub fn load_accounts(config_dir: &Path) -> Vec<Account> {
    let mut path = config_dir.join("creators.yaml");
    if !path.exists() {
        // Legacy fallback if creators.yaml is not found.
        let legacy = config_dir.join("accounts.yaml");
        if legacy.exists() {
            path = legacy;
        }
    }
    let Some(data) = load_yaml(&path) else {
        return Vec::new();
    };

    // Accept either a bare list or a mapping with `creators`/`accounts`.
    let items: Vec<serde_yaml::Value> = match data {
        serde_yaml::Value::Sequence(seq) => seq.into_iter().collect(),
        serde_yaml::Value::Mapping(map) => {
            let mut found: Vec<serde_yaml::Value> = Vec::new();
            for k in ["creators", "accounts"] {
                if let Some(seq) = map
                    .get(serde_yaml::Value::String(k.to_string()))
                    .and_then(|v| v.as_sequence())
                {
                    found = seq.clone();
                    break;
                }
            }
            found
        }
        _ => Vec::new(),
    };

    let mut out: Vec<Account> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for item in items {
        let serde_yaml::Value::Mapping(m) = item else { continue };
        let Some(raw_handle) = m.get(serde_yaml::Value::String("handle".into())) else {
            continue;
        };
        let handle = match raw_handle {
            serde_yaml::Value::String(s) => s.clone(),
            serde_yaml::Value::Number(n) => n.to_string(),
            serde_yaml::Value::Bool(b) => b.to_string(),
            _ => continue,
        };
        let handle = handle.trim_start_matches('@').trim().to_string();
        let key = handle.to_lowercase();
        if !is_valid_handle(&handle) || !seen.insert(key) {
            continue;
        }
        out.push(Account { handle });
    }
    out
}

/// Persist validated handles to `creators.yaml`; returns normalized rows written.
pub fn write_accounts(config_dir: &Path, handles: &[String]) -> Vec<Account> {
    let mut rows: Vec<Account> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for raw in handles {
        let handle = raw.trim_start_matches('@').trim().to_string();
        let key = handle.to_lowercase();
        if !is_valid_handle(&handle) || !seen.insert(key) {
            continue;
        }
        rows.push(Account { handle });
    }
    let path = config_dir.join("creators.yaml");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut content = String::from("# Pool of X handles to monitor.\n");
    if let Ok(body) = serde_yaml::to_string(&rows) {
        content.push_str(&body);
    }
    let _ = std::fs::write(&path, content);
    rows
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MinimaxSettings {
    pub base_url: String,
    pub model_id: String,
    pub temperature: f64,
    pub max_tokens: i64,
    #[serde(default)]
    pub api_key: String,
}

impl MinimaxSettings {
    pub fn configured(&self) -> bool {
        !self.api_key.is_empty()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct XSettings {
    #[serde(default)]
    pub bearer_token: String,
    // (remaining fields carry their own `#[serde(default)]` attributes)
    #[serde(default)]
    pub client_id: String,
    #[serde(default)]
    pub client_secret: String,
    #[serde(default = "default_callback_port")]
    pub callback_port: i64,
    #[serde(default = "default_recent_max")]
    pub recent_max_results: i64,
    #[serde(default = "default_exclude")]
    pub exclude: Vec<String>,
    #[serde(default = "default_rate_buffer")]
    pub rate_limit_buffer_seconds: i64,
}

fn default_callback_port() -> i64 {
    8765
}
fn default_recent_max() -> i64 {
    5
}
fn default_rate_buffer() -> i64 {
    5
}
fn default_exclude() -> Vec<String> {
    vec!["replies".into(), "retweets".into()]
}

impl Default for XSettings {
    fn default() -> Self {
        Self {
            bearer_token: String::new(),
            client_id: String::new(),
            client_secret: String::new(),
            callback_port: default_callback_port(),
            recent_max_results: default_recent_max(),
            exclude: default_exclude(),
            rate_limit_buffer_seconds: default_rate_buffer(),
        }
    }
}

impl XSettings {
    pub fn configured(&self) -> bool {
        !self.bearer_token.is_empty()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct UiSettings {
    pub page_title: String,
    pub cost_warning_threshold_usd: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Settings {
    pub repo_root: PathBuf,
    pub data_dir: PathBuf,
    pub config_dir: PathBuf,
    pub accounts: Vec<Account>,
    pub minimax: MinimaxSettings,
    pub x: XSettings,
    pub ui: UiSettings,
    pub niche: String,
}

fn deep_merge(base: serde_yaml::Value, overlay: serde_yaml::Value) -> serde_yaml::Value {
    match (base, overlay) {
        (serde_yaml::Value::Mapping(mut b), serde_yaml::Value::Mapping(o)) => {
            for (k, v) in o {
                let merged = match b.remove(&k) {
                    Some(existing) => deep_merge(existing, v),
                    None => v,
                };
                b.insert(k, merged);
            }
            serde_yaml::Value::Mapping(b)
        }
        (_, overlay) => overlay,
    }
}

fn yaml_get<'a>(root: &'a serde_yaml::Value, key: &str) -> Option<&'a serde_yaml::Value> {
    root.get(serde_yaml::Value::String(key.to_string()))
}

fn as_str(v: Option<&serde_yaml::Value>) -> Option<String> {
    match v {
        Some(serde_yaml::Value::String(s)) => Some(s.clone()),
        Some(serde_yaml::Value::Number(n)) => Some(n.to_string()),
        Some(serde_yaml::Value::Bool(b)) => Some(b.to_string()),
        _ => None,
    }
}

fn as_i64(v: Option<&serde_yaml::Value>) -> Option<i64> {
    match v {
        Some(serde_yaml::Value::Number(n)) => n.as_i64(),
        Some(serde_yaml::Value::String(s)) => s.parse().ok(),
        _ => None,
    }
}

fn as_f64(v: Option<&serde_yaml::Value>) -> Option<f64> {
    match v {
        Some(serde_yaml::Value::Number(n)) => n.as_f64(),
        Some(serde_yaml::Value::String(s)) => s.parse().ok(),
        _ => None,
    }
}

fn as_str_list(v: Option<&serde_yaml::Value>) -> Option<Vec<String>> {
    match v {
        Some(serde_yaml::Value::Sequence(seq)) => Some(
            seq.iter()
                .filter_map(|i| match i {
                    serde_yaml::Value::String(s) => Some(s.clone()),
                    serde_yaml::Value::Number(n) => Some(n.to_string()),
                    _ => None,
                })
                .collect(),
        ),
        _ => None,
    }
}

/// Load settings for a niche. Cached per-niche like the Python `lru_cache`.
pub fn get_settings(niche: &str) -> Settings {
    static CACHE: OnceLock<Mutex<BTreeMap<String, Settings>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(BTreeMap::new()));
    let niche_clean = {
        let n = niche.trim().to_lowercase();
        if n.is_empty() {
            DEFAULT_NICHE.to_string()
        } else {
            n
        }
    };
    if let Ok(map) = cache.lock() {
        if let Some(hit) = map.get(&niche_clean) {
            return hit.clone();
        }
    }
    let built = build_settings(&niche_clean);
    if let Ok(mut map) = cache.lock() {
        map.insert(niche_clean, built.clone());
    }
    built
}

/// Clear the per-niche settings cache (tests, and niche writes).
pub fn clear_settings_cache() {
    static CACHE: OnceLock<Mutex<BTreeMap<String, Settings>>> = OnceLock::new();
    if let Some(cache) = CACHE.get() {
        if let Ok(mut map) = cache.lock() {
            map.clear();
        }
    }
}

fn build_settings(niche_clean: &str) -> Settings {
    let root = repo_root();
    // `.env` never overrides a real environment variable (dotenvy
    // `override(false)` semantics).
    let _ = dotenvy::from_path(root.join(".env"));

    let base_config_dir = config_dir();
    let base_data_dir = data_dir();

    let niche_config_dir = base_config_dir.join(niche_clean);
    let effective_config_dir = if niche_config_dir.exists() {
        niche_config_dir.clone()
    } else {
        base_config_dir.clone()
    };

    let niche_data_dir = base_data_dir.join(niche_clean);
    let effective_data_dir =
        if niche_data_dir.exists() || niche_config_dir.exists() {
            niche_data_dir.clone()
        } else {
            base_data_dir.clone()
        };

    let empty = serde_yaml::Value::Mapping(Default::default());
    let base_raw = load_yaml(&base_config_dir.join("settings.yaml")).unwrap_or(empty);

    let raw = if effective_config_dir != base_config_dir
        && effective_config_dir.join("settings.yaml").exists()
    {
        let niche_raw = load_yaml(&effective_config_dir.join("settings.yaml"))
            .unwrap_or(serde_yaml::Value::Mapping(Default::default()));
        deep_merge(base_raw, niche_raw)
    } else {
        base_raw
    };

    let env = |k: &str| -> String { std::env::var(k).unwrap_or_default().trim().to_string() };

    let mm_raw = yaml_get(&raw, "minimax");
    let minimax = MinimaxSettings {
        base_url: {
            let e = env("MINIMAX_BASE_URL");
            if e.is_empty() {
                as_str(mm_raw.and_then(|m| yaml_get(m, "base_url")))
                    .unwrap_or_else(|| "https://api.minimax.io/v1".to_string())
            } else {
                e
            }
        },
        model_id: as_str(mm_raw.and_then(|m| yaml_get(m, "model_id")))
            .unwrap_or_else(|| "MiniMax-M2.7".to_string()),
        temperature: as_f64(mm_raw.and_then(|m| yaml_get(m, "temperature"))).unwrap_or(0.7),
        max_tokens: as_i64(mm_raw.and_then(|m| yaml_get(m, "max_tokens"))).unwrap_or(2048),
        api_key: env("MINIMAX_API_KEY"),
    };

    let x_raw = yaml_get(&raw, "x");
    let x_settings = XSettings {
        bearer_token: env("X_BEARER_TOKEN"),
        client_id: env("X_CLIENT_ID"),
        client_secret: env("X_CLIENT_SECRET"),
        callback_port: {
            let e = env("X_AUTH_CALLBACK_PORT");
            if e.is_empty() {
                as_i64(x_raw.and_then(|m| yaml_get(m, "callback_port")))
                    .unwrap_or_else(default_callback_port)
            } else {
                e.parse().unwrap_or_else(|_| default_callback_port())
            }
        },
        recent_max_results: as_i64(x_raw.and_then(|m| yaml_get(m, "recent_max_results")))
            .unwrap_or_else(default_recent_max),
        exclude: as_str_list(x_raw.and_then(|m| yaml_get(m, "exclude")))
            .unwrap_or_else(default_exclude),
        rate_limit_buffer_seconds: as_i64(x_raw.and_then(|m| yaml_get(m, "rate_limit_buffer_seconds")))
            .unwrap_or_else(default_rate_buffer),
    };

    let ui_raw = yaml_get(&raw, "ui");
    let default_title = if niche_clean == "crypto" {
        "X-Automation · Crypto".to_string()
    } else {
        format!("X-Automation · {}", title_case(niche_clean))
    };
    let ui = UiSettings {
        page_title: as_str(ui_raw.and_then(|m| yaml_get(m, "page_title")))
            .unwrap_or(default_title),
        cost_warning_threshold_usd: as_f64(
            ui_raw.and_then(|m| yaml_get(m, "cost_warning_threshold_usd")),
        )
        .unwrap_or(1.00),
    };

    Settings {
        accounts: load_accounts(&effective_config_dir),
        repo_root: root,
        data_dir: effective_data_dir,
        config_dir: effective_config_dir,
        minimax,
        x: x_settings,
        ui,
        niche: niche_clean.to_string(),
    }
}

/// Mirrors Python's `str.title()` for the niche word ("ai" -> "Ai").
fn title_case(s: &str) -> String {
    s.split_whitespace()
        .map(|w| {
            let mut c = w.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handle_validation_matches_x_rules() {
        assert!(is_valid_handle("elonmusk"));
        assert!(is_valid_handle("a_b_c123"));
        assert!(!is_valid_handle(""));
        assert!(!is_valid_handle("way_too_long_handle_here"));
        assert!(!is_valid_handle("has-dash"));
        assert!(!is_valid_handle("has space"));
    }

    #[test]
    fn title_case_matches_python() {
        assert_eq!(title_case("ai"), "Ai");
        assert_eq!(title_case("crypto"), "Crypto");
    }

    #[test]
    fn deep_merge_overlays_nested_maps() {
        let base: serde_yaml::Value = serde_yaml::from_str("a:\n  b: 1\n  c: 2\n").unwrap();
        let over: serde_yaml::Value = serde_yaml::from_str("a:\n  c: 9\nd: 3\n").unwrap();
        let merged = deep_merge(base, over);
        assert_eq!(as_i64(yaml_get(&merged, "a").and_then(|m| yaml_get(m, "b"))), Some(1));
        assert_eq!(as_i64(yaml_get(&merged, "a").and_then(|m| yaml_get(m, "c"))), Some(9));
        assert_eq!(as_i64(yaml_get(&merged, "d")), Some(3));
    }

    #[test]
    fn configured_reflects_presence_of_keys() {
        let mm = MinimaxSettings {
            base_url: "x".into(),
            model_id: "y".into(),
            temperature: 0.7,
            max_tokens: 10,
            api_key: String::new(),
        };
        assert!(!mm.configured());
        let x = XSettings::default();
        assert!(!x.configured());
        assert_eq!(x.callback_port, 8765);
        assert_eq!(x.exclude, vec!["replies", "retweets"]);
    }
}