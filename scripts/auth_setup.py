"""One-time OAuth 2.0 (PKCE) setup for the X API.

What it does:
  1. Read X_CLIENT_ID, X_CLIENT_SECRET, X_BEARER_TOKEN from .env.
  2. Generate PKCE verifier + S256 challenge + CSRF state.
  3. Open the user's browser to the X consent URL.
  4. Spin up a tiny HTTP server on 127.0.0.1:<X_AUTH_CALLBACK_PORT> that
     captures the redirect (with the auth code), verifies the state, and
     exchanges the code for tokens.
  5. Persist the resulting token bundle to data/oauth_tokens.json.

Run this once, then again whenever the refresh token is revoked.
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import urllib.parse
import webbrowser
from pathlib import Path

# Make src/ importable when run as `python scripts/auth_setup.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from x_auto.config import get_settings  # noqa: E402
from x_auto.x.auth import (  # noqa: E402
    DEFAULT_SCOPES,
    TokenStore,
    build_authorize_url,
    exchange_code,
    new_pkce_pair,
    new_state,
)


class _Handler(http.server.BaseHTTPRequestHandler):
    """Captures the OAuth code, prints a friendly HTML, and shuts down."""

    server_version = "XAutoAuth/0.1"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path != "/callback":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        # Verify state.
        state = (params.get("state") or [""])[0]
        expected = getattr(self.server, "_xauto_state", "")
        if not state or state != expected:
            self._respond(400, "<h1>State mismatch — try again.</h1>")
            return

        code = (params.get("code") or [""])[0]
        if not code:
            self._respond(400, "<h1>No code in callback.</h1>")
            return

        try:
            settings = get_settings()
            bundle = exchange_code(
                client_id=settings.x.client_id,
                client_secret=settings.x.client_secret,
                code=code,
                code_verifier=getattr(self.server, "_xauto_verifier", ""),
                redirect_uri=f"http://127.0.0.1:{settings.x.callback_port}/callback",
            )
            bundle.bearer_token = settings.x.bearer_token
            TokenStore(settings.data_dir / "oauth_tokens.json").save(bundle)
        except Exception as exc:  # noqa: BLE001
            self._respond(500, f"<h1>Token exchange failed:</h1><pre>{exc}</pre>")
            return

        self._respond(
            200,
            "<h1>Authorization complete.</h1>"
            "<p>You can close this tab and return to the app.</p>",
        )
        # Trigger server shutdown from another thread.
        self.server._xauto_done = True  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        # Quiet the default request log.
        pass

    def _respond(self, code: int, html: str) -> None:
        body = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>body{font:16px system-ui;max-width:480px;margin:64px auto;}"
            "</style></head><body>" + html + "</body></html>"
        ).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(socketserver.TCPServer):
    allow_reuse_address = True
    _xauto_state: str = ""
    _xauto_verifier: str = ""
    _xauto_done: bool = False


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    settings = get_settings()
    if not settings.x.client_id:
        print("ERROR: X_CLIENT_ID is not set in .env", file=sys.stderr)
        return 2

    pkce = new_pkce_pair()
    state = new_state()
    redirect_uri = f"http://127.0.0.1:{settings.x.callback_port}/callback"
    authorize_url = build_authorize_url(
        client_id=settings.x.client_id,
        redirect_uri=redirect_uri,
        scopes=DEFAULT_SCOPES,
        state=state,
        code_challenge=pkce.code_challenge,
    )

    print("Opening browser to X consent screen…")
    print("If the browser does not open, paste this URL into your browser:")
    print()
    print("  " + authorize_url)
    print()
    webbrowser.open(authorize_url)

    with _Server(("127.0.0.1", settings.x.callback_port), _Handler) as httpd:
        httpd._xauto_state = state
        httpd._xauto_verifier = pkce.code_verifier
        # Run until the handler flips _xauto_done (or we time out).
        httpd.timeout = 300  # type: ignore[attr-defined]
        while not getattr(httpd, "_xauto_done", False):
            httpd.handle_request()

    print("Tokens saved to data/oauth_tokens.json.")
    print("You can now run: streamlit run src/x_auto/app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
