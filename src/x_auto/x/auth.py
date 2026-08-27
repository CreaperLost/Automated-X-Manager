"""OAuth 2.0 Authorization Code with PKCE for X.

Scopes used (configured at the developer portal):
  - tweet.read    (read user-context data)
  - tweet.write   (create posts)
  - users.read    (resolve @handle -> user_id)
  - media.write   (upload media for posts)
  - offline.access (issue a refresh token)

Token storage:
  data/oauth_tokens.json
    {
      "x_user": {
        "access_token":  "...",
        "refresh_token": "...",
        "expires_at":    "2026-08-27T12:34:56+00:00",
        "scope":         "tweet.read tweet.write users.read media.write offline.access",
        "bearer_token":  "..."   # app-only bearer (read-only; cheap)
      }
    }
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings

AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"

DEFAULT_SCOPES = "tweet.read tweet.write users.read media.write offline.access"


@dataclass(frozen=True)
class PKCEPair:
    code_verifier: str
    code_challenge: str


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_pkce_pair() -> PKCEPair:
    """Generate a fresh PKCE code_verifier + S256 code_challenge."""
    verifier = _b64url_nopad(secrets.token_bytes(48))
    challenge = _b64url_nopad(hashlib.sha256(verifier.encode("ascii")).digest())
    return PKCEPair(code_verifier=verifier, code_challenge=challenge)


def new_state() -> str:
    return _b64url_nopad(secrets.token_bytes(24))


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: str = DEFAULT_SCOPES,
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: str
    bearer_token: str = ""

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        return datetime.now(UTC) >= self.expires_at - timedelta(minutes=5)

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "scope": self.scope,
            "bearer_token": self.bearer_token,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TokenBundle:
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            scope=str(data.get("scope", "")),
            bearer_token=str(data.get("bearer_token", "")),
        )


class TokenStore:
    """File-backed token store at data/oauth_tokens.json (mode 0600)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> TokenBundle | None:
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        bundle = raw.get("x_user")
        if not isinstance(bundle, dict):
            return None
        try:
            return TokenBundle.from_json(bundle)
        except (KeyError, ValueError):
            return None

    def save(self, bundle: TokenBundle) -> None:
        payload = {"x_user": bundle.to_json()}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # os.chmod works on Windows for the read-only bit; the 0600 mode
        # is a best-effort intent. The OS-level DACL is what enforces.
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._path)


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> TokenBundle:
    """Exchange an authorization code for an access + refresh token."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    # When the client has a secret, the X token endpoint requires HTTP Basic
    # auth on the credentials. We add both header and form fields to be safe.
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            TOKEN_URL,
            data=data,
            auth=(client_id, client_secret) if client_secret else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    body = resp.json()
    return _bundle_from_token_response(body)


def refresh_tokens(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> TokenBundle:
    """Use a refresh token to get a new access token (rotates the refresh)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            TOKEN_URL,
            data=data,
            auth=(client_id, client_secret) if client_secret else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    body = resp.json()
    return _bundle_from_token_response(body)


def _bundle_from_token_response(body: dict[str, Any]) -> TokenBundle:
    access_token = str(body["access_token"])
    refresh_token = str(body.get("refresh_token", ""))
    scope = str(body.get("scope", DEFAULT_SCOPES))
    expires_in = int(body.get("expires_in", 7200))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scope=scope,
    )


class TokenManager:
    """Auto-refreshing token accessor.

    Usage:
        mgr = TokenManager(get_settings(), TokenStore(get_settings().data_dir / "oauth_tokens.json"))
        access = mgr.access_token()  # refreshes if needed
    """

    def __init__(self, settings=None, store: TokenStore | None = None) -> None:
        self._settings = settings or get_settings()
        path = self._settings.data_dir / "oauth_tokens.json"
        self._store = store or TokenStore(path)
        self._cached: TokenBundle | None = None

    def _settings_cached(self) -> Any:
        return self._settings

    def _load_or_raise(self) -> TokenBundle:
        if self._cached is None:
            loaded = self._store.load()
            if loaded is None:
                raise RuntimeError(
                    "X OAuth tokens not found. Run: python scripts/auth_setup.py"
                )
            self._cached = loaded
        return self._cached

    def bearer(self) -> str:
        """Return the app-only bearer token (cheap, no refresh)."""
        b = self._load_or_raise()
        return b.bearer_token or self._settings.x.bearer_token

    def access_token(self) -> str:
        """Return a valid user-context access token, refreshing if needed."""
        bundle = self._load_or_raise()
        if bundle.needs_refresh and bundle.refresh_token:
            try:
                new_bundle = refresh_tokens(
                    client_id=self._settings.x.client_id,
                    client_secret=self._settings.x.client_secret,
                    refresh_token=bundle.refresh_token,
                )
                new_bundle.bearer_token = bundle.bearer_token or self._settings.x.bearer_token
                self._store.save(new_bundle)
                self._cached = new_bundle
                return new_bundle.access_token
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Token refresh failed: {exc}. Re-authorize via: "
                    f"python scripts/auth_setup.py"
                ) from exc
        return bundle.access_token

    def save_initial(self, bundle: TokenBundle) -> None:
        """Persist a brand-new token bundle (from auth_setup.py)."""
        self._store.save(bundle)
        self._cached = bundle
