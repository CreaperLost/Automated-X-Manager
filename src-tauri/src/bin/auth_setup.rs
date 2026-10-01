//! OAuth 2.0 PKCE setup helper.
//!
//! Runs the browser consent flow once and writes
//! `data/oauth_tokens.json`, shared by both niches. Mirrors the Python
//! `scripts/auth_setup.py`.
//!
//! Usage:
//! ```text
//! cargo run --bin auth_setup            # crypto
//! cargo run --bin auth_setup -- ai      # ai
//! ```

use std::net::TcpListener;
use std::time::Duration;

use x_core::config;
use x_core::x::auth::{
    build_authorize_url, exchange_code, new_pkce_pair, new_state, TokenBundle, TokenStore,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let niche = std::env::args()
        .nth(1)
        .filter(|a| !a.starts_with('-'))
        .unwrap_or_else(|| config::DEFAULT_NICHE.to_string());

    let settings = config::get_settings(&niche);
    if settings.x.client_id.is_empty() {
        eprintln!("X_CLIENT_ID is not set in .env.");
        std::process::exit(1);
    }

    let pkce = new_pkce_pair();
    let expected_state = new_state();
    let port = settings.x.callback_port;
    let redirect_uri = format!("http://127.0.0.1:{port}/callback");
    let url = build_authorize_url(
        &settings.x.client_id,
        &redirect_uri,
        x_core::x::auth::DEFAULT_SCOPES,
        &expected_state,
        &pkce.code_challenge,
    );

    // Bind before opening the browser so the redirect never races us.
    let port_u16 = u16::try_from(port)
        .map_err(|_| format!("X_AUTH_CALLBACK_PORT must be a valid TCP port, got {port}"))?;
    let listener = TcpListener::bind(("127.0.0.1", port_u16))?;
    println!("Open this URL to authorize:\n\n{url}\n");
    println!("Waiting for the callback on {redirect_uri} ...");
    open_browser(&url);

    let code = x_core::x::callback::wait_for_callback(listener, &expected_state, Duration::from_secs(300))?;
    let rt = tokio::runtime::Runtime::new()?;
    let http = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()?;
    let mut bundle: TokenBundle = rt.block_on(exchange_code(
        &http,
        &settings.x.client_id,
        &settings.x.client_secret,
        &code,
        &pkce.code_verifier,
        &redirect_uri,
    ))?;

    // Keep the app-only bearer so reads stay cheap.
    if bundle.bearer_token.is_empty() {
        bundle.bearer_token = settings.x.bearer_token.clone();
    }

    // Mirrors the Python original: write to the shared `data/` folder
    // rather than per-niche, so the same tokens work for both niches.
    let store_path = settings.repo_root.join("data").join("oauth_tokens.json");
    let store = TokenStore::new(&store_path);
    store.save(&bundle)?;

    println!("\nAuthorized. Tokens saved to {}", store.path().display());
    Ok(())
}

fn open_browser(url: &str) {
    // Always also drop the URL in a file the user can paste into a browser
    // manually. We hit a real bug here once: `cmd /C start "" "<url>"`
    // re-parses the constructed command line in cmd, and the URL's `&`
    // characters get treated as command separators — so cmd runs the
    // command, fails to find each token like `client_id`, and the
    // browser never opens. The user sees the URL print + cmd errors and
    // has no way to know the redirect is waiting.
    if let Ok(path) = std::env::current_exe() {
        if let Some(dir) = path.parent() {
            let _ = std::fs::write(dir.join("auth_url.txt"), url);
        }
    }

    #[cfg(target_os = "windows")]
    {
        // rundll32 url.dll,FileProtocolHandler is the documented Windows
        // way to open a URL from the shell. The argument never has to
        // round-trip through cmd's parser, so `&` is safe.
        let _ = std::process::Command::new("rundll32.exe")
            .args(["url.dll,FileProtocolHandler", url])
            .spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open").arg(url).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = std::process::Command::new("xdg-open").arg(url).spawn();
    }
}
