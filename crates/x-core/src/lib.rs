//! Domain core for X-Automation.
//!
//! A direct port of the Python `src/x_auto` package minus the Streamlit
//! UI. Everything here is framework-free and independently testable:
//!
//! * [`config`] — settings resolution (env + YAML + niche overlay)
//! * [`store`] — SQLite schema, models, repositories
//! * [`utils`] — text/URL/cashtag rules, file validation, media library
//! * [`x`] — X API client, OAuth 2.0 PKCE token manager, media upload, publish
//! * [`ai`] — MiniMax client, prompts, draft workflows, project CSV sync
//!
//! The Tauri layer in `src-tauri` owns the UI and calls into these
//! modules; nothing here depends on Tauri.

pub mod ai;
pub mod config;
pub mod error;
pub mod store;
pub mod utils;
pub mod x;

pub use error::{Error, Result};