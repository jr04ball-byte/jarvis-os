"""Google OAuth2 flow for Gmail + Calendar access.

Reuses the Gmail OAuth credentials from the email-agent-saas project.
Stores tokens encrypted in api_data volume so the AI assistant can access
Gmail and Calendar on the user's behalf.

Setup steps (one-time):
  1. Open Google Cloud Console → your project
  2. Enable APIs: Gmail API, Google Calendar API
  3. Configure OAuth consent screen (add scopes below)
  4. Add redirect URI: http://localhost:8000/auth/google/callback
  5. Copy CLIENT_ID / CLIENT_SECRET into .env

Scopes requested (least-privilege, expand as needed):
  - gmail.readonly    — read email
  - gmail.send        — send email (requires confirmation per send)
  - gmail.compose     — create drafts
  - calendar          — full calendar access (events, free/busy)
  - userinfo.email    — identify which Google account is connected
"""
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# ---- Config ---------------------------------------------------------------

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")

# If no encryption key set, derive a stable one from a local file so tokens
# survive restarts. (Not ideal — proper key management comes later.)
_TOKEN_DIR = os.getenv("GOOGLE_TOKEN_DIR") or os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data"))
_KEY_FILE = Path(_TOKEN_DIR) / ".token_key"

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"

# ---- Token storage --------------------------------------------------------

class TokenStore:
    """Encrypted token persistence. Single-user for now (this is a personal
    home assistant), but the file format is JSON so it can grow."""

    def __init__(self, data_dir: str | None = None):
        # Honor API_DATA_DIR so native runs (./data) and Docker (/app/data)
        # use the same directory as the encryption key file below.
        self.path = Path(data_dir or _TOKEN_DIR) / "google_tokens.enc"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = self._get_fernet()

    def _get_fernet(self) -> Fernet:
        if TOKEN_ENCRYPTION_KEY:
            # Expect a 32-byte url-safe base64 key
            try:
                return Fernet(TOKEN_ENCRYPTION_KEY.encode())
            except Exception:
                raise RuntimeError(
                    "TOKEN_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                    "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
        # Fallback: derive once and persist locally
        if _KEY_FILE.exists():
            return Fernet(_KEY_FILE.read_bytes())
        key = Fernet.generate_key()
        _KEY_FILE.write_bytes(key)
        _KEY_FILE.chmod(0o600)
        return Fernet(key)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except Exception as e:
            logger.error("token decrypt failed: %s", e)
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
        self.path.chmod(0o600)

    def save_tokens(self, email: str, tokens: dict[str, Any]) -> None:
        data = self._load()
        data[email] = {**tokens, "saved_at": time.time()}
        self._save(data)

    def load_tokens(self, email: str) -> dict[str, Any] | None:
        return self._load().get(email)

    def delete_account(self, email: str) -> bool:
        """Forget a connected account. Returns True if one was removed."""
        data = self._load()
        if email not in data:
            return False
        del data[email]
        self._save(data)
        return True

    def list_accounts(self) -> list:
        return list(self._load().keys())

# ---- State (CSRF + flow tracking) -----------------------------------------

_state_store: dict[str, float] = {}
_STATE_TTL = 600  # 10 minutes

def _new_state() -> str:
    s = secrets.token_urlsafe(32)
    _state_store[s] = time.time()
    return s

def _consume_state(state: str) -> bool:
    issued = _state_store.pop(state, None)
    if not issued:
        return False
    return (time.time() - issued) < _STATE_TTL

# Periodic cleanup of expired states
def _sweep_states() -> None:
    cutoff = time.time() - _STATE_TTL
    for k in [k for k, t in _state_store.items() if t < cutoff]:
        _state_store.pop(k, None)

# ---- Public API -----------------------------------------------------------

def build_auth_url() -> str:
    """Start the OAuth flow — return URL to redirect the user to."""
    if not CLIENT_ID:
        raise RuntimeError("GOOGLE_CLIENT_ID not set in environment")
    _sweep_states()
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",       # get a refresh_token
        "prompt": "consent",            # force consent to always get refresh_token
        "state": _new_state(),
    }
    return f"{AUTH_URL}?{urlencode(params)}"

async def exchange_code(code: str) -> dict[str, Any]:
    """Exchange auth code for tokens. Returns tokens dict + user email."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(TOKEN_URL, data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        })
        r.raise_for_status()
        tokens = r.json()

        # Identify the connected Google account
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        u = await c.get(USERINFO_URL, headers=headers)
        u.raise_for_status()
        email = u.json().get("email")
        if not email:
            raise RuntimeError("userinfo response missing email")

    tokens["scope"] = tokens.get("scope", "")
    return {"email": email, "tokens": tokens}

async def refresh_if_needed(email: str, store: TokenStore) -> dict[str, Any]:
    """Return a valid access_token, refreshing via refresh_token if expired."""
    tok = store.load_tokens(email)
    if not tok:
        raise PermissionError(f"no tokens stored for {email}")

    expires_at = tok.get("saved_at", 0) + int(tok.get("expires_in", 3600))
    # Refresh 60s early to avoid edge cases
    if time.time() < expires_at - 60:
        return tok

    if "refresh_token" not in tok:
        raise PermissionError(f"token for {email} expired and no refresh_token")

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(TOKEN_URL, data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
        })
        r.raise_for_status()
        new_tok = r.json()
    new_tok["refresh_token"] = tok["refresh_token"]  # Google doesn't re-issue
    new_tok["scope"] = new_tok.get("scope", tok.get("scope", ""))
    store.save_tokens(email, new_tok)
    return new_tok

# ---- Gmail + Calendar helpers (for later) ---------------------------------

async def gmail_list_messages(email: str, store: TokenStore, max_results: int = 20, query: str = "") -> list:
    tok = await refresh_if_needed(email, store)
    async with httpx.AsyncClient(timeout=15) as c:
        params = {"maxResults": max_results}
        if query:
            params["q"] = query
        r = await c.get(f"{GMAIL_BASE}/users/me/messages",
                        params=params,
                        headers={"Authorization": f"Bearer {tok['access_token']}"})
        r.raise_for_status()
        return r.json().get("messages", [])

async def calendar_list_events(email: str, store: TokenStore, max_results: int = 10, time_min: str | None = None) -> list:
    tok = await refresh_if_needed(email, store)
    async with httpx.AsyncClient(timeout=15) as c:
        params = {"maxResults": max_results, "singleEvents": "true", "orderBy": "startTime"}
        if time_min:
            params["timeMin"] = time_min
        r = await c.get(f"{CALENDAR_BASE}/calendars/primary/events",
                        params=params,
                        headers={"Authorization": f"Bearer {tok['access_token']}"})
        r.raise_for_status()
        return r.json().get("items", [])

async def gmail_get_message(email: str, store: TokenStore, message_id: str) -> dict:
    tok = await refresh_if_needed(email, store)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{GMAIL_BASE}/users/me/messages/{message_id}", params={"format":"full"}, headers={"Authorization":f"Bearer {tok['access_token']}"})
        r.raise_for_status(); return r.json()

async def gmail_send_message(email: str, store: TokenStore, to: str, subject: str, body: str) -> dict:
    import base64
    from email.message import EmailMessage
    tok = await refresh_if_needed(email, store)
    msg = EmailMessage(); msg["To"] = to; msg["Subject"] = subject; msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{GMAIL_BASE}/users/me/messages/send", json={"raw":raw}, headers={"Authorization":f"Bearer {tok['access_token']}","Content-Type":"application/json"})
        r.raise_for_status(); return r.json()

async def calendar_create_event(email: str, store: TokenStore, event: dict) -> dict:
    tok = await refresh_if_needed(email, store)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{CALENDAR_BASE}/calendars/primary/events", json=event, headers={"Authorization":f"Bearer {tok['access_token']}","Content-Type":"application/json"})
        r.raise_for_status(); return r.json()

async def calendar_update_event(email: str, store: TokenStore, event_id: str, event: dict) -> dict:
    tok = await refresh_if_needed(email, store)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.patch(f"{CALENDAR_BASE}/calendars/primary/events/{event_id}", json=event, headers={"Authorization":f"Bearer {tok['access_token']}","Content-Type":"application/json"})
        r.raise_for_status(); return r.json()

async def calendar_delete_event(email: str, store: TokenStore, event_id: str) -> bool:
    tok = await refresh_if_needed(email, store)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(f"{CALENDAR_BASE}/calendars/primary/events/{event_id}", headers={"Authorization":f"Bearer {tok['access_token']}"})
        r.raise_for_status(); return True
