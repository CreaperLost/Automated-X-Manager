//! Error types for the X-Automation core.

use std::fmt;

/// A single error type across the core. Variants carry enough context to
/// render a useful message in the UI without leaking credentials.
#[derive(Debug)]
pub enum Error {
    /// Configuration or filesystem problem.
    Io(String),
    /// SQLite failure.
    Db(String),
    /// X API returned an error status.
    XApi {
        status: u16,
        detail: String,
        url: Option<String>,
    },
    /// X returned 429 after the retry budget was spent.
    RateLimited {
        retry_after_seconds: u64,
        url: Option<String>,
    },
    /// User-context token missing or unrefreshable.
    Auth(String),
    /// MiniMax returned malformed output or the call failed.
    Ai(String),
    /// A deterministic local rule failed before any paid write.
    Validation(String),
    /// Media upload or local file problem.
    Media(String),
    /// Anything else.
    Other(String),
}

impl Error {
    pub fn other(msg: impl fmt::Display) -> Self {
        Error::Other(msg.to_string())
    }
    pub fn ai(msg: impl fmt::Display) -> Self {
        Error::Ai(msg.to_string())
    }
    pub fn validation(msg: impl fmt::Display) -> Self {
        Error::Validation(msg.to_string())
    }
    pub fn media(msg: impl fmt::Display) -> Self {
        Error::Media(msg.to_string())
    }

    /// HTTP status when this error came from an HTTP response.
    pub fn status(&self) -> Option<u16> {
        match self {
            Error::XApi { status, .. } => Some(*status),
            Error::RateLimited { .. } => Some(429),
            Error::Auth(_) => Some(401),
            _ => None,
        }
    }

    /// A stable machine-readable tag, so the frontend can branch on the
    /// error kind without string-matching the message.
    pub fn kind(&self) -> &'static str {
        match self {
            Error::Io(_) => "io",
            Error::Db(_) => "db",
            Error::XApi { .. } => "x_api",
            Error::RateLimited { .. } => "rate_limited",
            Error::Auth(_) => "auth",
            Error::Ai(_) => "ai",
            Error::Validation(_) => "validation",
            Error::Media(_) => "media",
            Error::Other(_) => "other",
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io(m) => write!(f, "{m}"),
            Error::Db(m) => write!(f, "database error: {m}"),
            Error::XApi { status, detail, url } => {
                write!(f, "X API {status} on {}: {detail}", url.as_deref().unwrap_or("?"))
            }
            Error::RateLimited { retry_after_seconds, url } => {
                write!(
                    f,
                    "X API 429 on {}: rate limited, retry in {retry_after_seconds}s",
                    url.as_deref().unwrap_or("?")
                )
            }
            Error::Auth(m) => write!(f, "{m}"),
            Error::Ai(m) => write!(f, "{m}"),
            Error::Validation(m) => write!(f, "{m}"),
            Error::Media(m) => write!(f, "{m}"),
            Error::Other(m) => write!(f, "{m}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<rusqlite::Error> for Error {
    fn from(e: rusqlite::Error) -> Self {
        Error::Db(e.to_string())
    }
}

impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error::Other(format!("json: {e}"))
    }
}

impl From<reqwest::Error> for Error {
    fn from(e: reqwest::Error) -> Self {
        Error::XApi {
            status: e.status().map(|s| s.as_u16()).unwrap_or(0),
            detail: format!("network error: {e}"),
            url: None,
        }
    }
}

pub type Result<T> = std::result::Result<T, Error>;