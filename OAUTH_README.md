# AI System — Google OAuth Integration

Standalone Google OAuth 2.0 flow for the local AI assistant, giving it scoped
access to your Gmail and Google Calendar. Tokens are encrypted at rest on disk
and persisted in the `api_data` Docker volume so they survive restarts.

This module was added to the existing `email-agent-saas`-style stack running
under `C:\Users\jr04b\ai-system`.

---

## What this gives you

After a one-time setup, the AI assistant can:

- **Read your Gmail** (list recent messages, search by query)
- **Read your Calendar** (list upcoming events)
- **Identify which Google account is connected** (via `userinfo.email`)

The scopes requested are the least-privilege set needed for those operations:

| Scope | Why |
|---|---|
| `openid` | OpenID Connect sign-in |
| `https://www.googleapis.com/auth/userinfo.email` | Identify the connected account |
| `https://www.googleapis.com/auth/gmail.readonly` | Read email |
| `https://www.googleapis.com/auth/gmail.send` | Send email (requires explicit confirmation per send) |
| `https://www.googleapis.com/auth/gmail.compose` | Create drafts |
| `https://www.googleapis.com/auth/calendar` | Full Calendar read/write |

> **Cost note:** Gmail and Calendar APIs are free for personal/OAuth use.
> You will not be charged, and no billing account or credit card is required.

---

## Files

| File | Purpose |
|---|---|
| `api-gateway/google_oauth.py` | OAuth flow, token storage, Gmail + Calendar helpers |
| `api-gateway/main.py` | FastAPI app — adds 6 new routes (see below) |
| `api-gateway/requirements.txt` | Adds `cryptography` and `python-dotenv` |
| `api-gateway/Dockerfile` | Slim CPU-only Python image (gateway proxies to Ollama; only Ollama uses the GPU) |
| `docker-compose.yml` | API gateway now requests the GPU; OAuth env vars wired in |
| `.env` | **Real OAuth credentials (gitignored — never commit)** |
| `.env.example` | Template with blank values (safe to commit) |
| `.gitignore` | Protects `.env`, tokens, `data/`, pycache |

---

## One-time Google Cloud Console setup

The OAuth client and credentials are already in your `email-agent-saas/.env`.
You still need to enable the Gmail and Calendar APIs in Google Cloud Console
and register the new redirect URI:

1. Open https://console.cloud.google.com and select the project that owns
   client `467782559106-…`.
2. **APIs & Services → Library** → enable:
   - **Gmail API**
   - **Google Calendar API**
3. **APIs & Services → OAuth consent screen** → add these scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `openid`
4. **APIs & Services → Credentials** → edit your OAuth client → under
   **Authorized redirect URIs** add:
   - `http://localhost:8000/auth/google/callback`
5. Save.

No billing account is needed. Skip any prompts asking you to enable billing.

---

## Running it

From `C:\Users\jr04b\ai-system`:

```powershell
docker-compose up -d --build
```

First build takes 5–10 minutes because the CUDA base image is ~3 GB.
Subsequent builds are fast.

Wait ~30 seconds for everything to start, then check status:

```powershell
curl http://localhost:8000/auth/google/status
```

You should see:

```json
{ "accounts": [], "configured": true }
```

`configured: true` confirms the OAuth env vars loaded correctly.

### Connect your Google account

```powershell
curl http://localhost:8000/auth/google/start
```

Returns JSON with a `url`. Open that URL in your browser. You'll be asked to
sign in to Google, then see a consent screen listing the requested scopes.
Click **Allow**. Google redirects you back to the callback URL and shows:

```json
{ "ok": true, "email": "you@gmail.com", "scopes": "openid ... email ..." }
```

Your account is now connected. Tokens are encrypted on disk.

### Use the API

**List recent Gmail messages:**

```
GET http://localhost:8000/gmail/messages?email=you@gmail.com&max_results=10
```

Optional `query` parameter for Gmail search syntax, e.g.
`?query=is:unread from:boss@company.com`.

**List upcoming calendar events:**

```
GET http://localhost:8000/calendar/events?email=you@gmail.com&max_results=10
```

**Check connection status:**

```
GET http://localhost:8000/auth/google/status
```

Returns each connected account with:

```json
{
  "email": "you@gmail.com",
  "scopes": "...",
  "expires_at": 1735689600,
  "needs_refresh": false,
  "has_refresh_token": true
}
```

**Disconnect an account** (forgets locally; does not revoke on Google's side):

```
POST http://localhost:8000/auth/google/disconnect?email=you@gmail.com
```

---

## How the OAuth flow works

1. Client calls `GET /auth/google/start` → returns Google's authorization URL
2. User opens the URL, signs in, grants consent
3. Google redirects to `GET /auth/google/callback?code=…&state=…`
4. Callback verifies the `state` token (CSRF protection), then exchanges
   the `code` for `access_token` + `refresh_token`
5. Tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and written
   to `api_data/google_tokens.enc` keyed by the user's email
6. The encryption key lives at `api_data/.token_key` (auto-generated on first
   run, mode 0600) — or set `TOKEN_ENCRYPTION_KEY` in `.env` to use a
   stable key across deployments

When a token expires, the next API call automatically refreshes it using
`refresh_token`. Google doesn't re-issue `refresh_token` on refresh, so the
existing one is preserved.

---

## How token storage works

Tokens are stored in a single JSON file encrypted with Fernet:

```json
{
  "you@gmail.com": {
    "access_token": "ya29.…",
    "refresh_token": "1//0g…",
    "scope": "openid …",
    "token_type": "Bearer",
    "expires_in": 3600,
    "saved_at": 1735686000
  }
}
```

- **At rest:** the whole JSON is Fernet-encrypted on disk
- **In memory:** tokens are loaded only when an API call needs them, then
  held as a Python dict for the duration of the request
- **Permissions:** the file is `chmod 0600` (owner read/write only)
- **Key rotation:** not supported yet. To rotate, set a new
  `TOKEN_ENCRYPTION_KEY` and re-run the OAuth flow

---

## Security notes

- The OAuth flow is **CSRF-protected** via the `state` parameter (random
  32-byte URL-safe token, 10-minute TTL, single-use via the in-memory store)
- Tokens are **never logged** — only the connected email is logged on success
- The `prompt=consent` parameter forces consent every time, guaranteeing
  you always get a `refresh_token` (without it, Google may skip the consent
  screen on re-auth and not return a new refresh token)
- The api-gateway is CPU-only and holds no GPU reservation. Only Ollama uses
  the GPU, so gateway load can't cause VRAM pressure (Ollama with a large
  model loaded can still OOM on 8 GB — watch `nvidia-smi`)
- The `.env` file contains the OAuth client secret. **Never commit it.**
  It is gitignored but treat any leak as compromised and rotate the
  client secret in Google Cloud Console

---

## Architecture

```
                    ┌─────────────────┐
                    │   Browser/User  │
                    └────────┬────────┘
                             │ visit /auth/google/start
                             ▼
┌────────────────────────────────────────────────────┐
│  api-gateway (port 8000)                           │
│  ┌──────────────────────────────────────────────┐  │
│  │  main.py                                     │  │
│  │   - /auth/google/start                       │  │
│  │   - /auth/google/callback                    │  │
│  │   - /auth/google/status                      │  │
│  │   - /auth/google/disconnect                  │  │
│  │   - /gmail/messages                          │  │
│  │   - /calendar/events                         │  │
│  └──────────┬───────────────────────────────────┘  │
│             │ uses                                 │
│  ┌──────────▼───────────────────────────────────┐  │
│  │  google_oauth.py                             │  │
│  │   - build_auth_url()                         │  │
│  │   - exchange_code()                          │  │
│  │   - refresh_if_needed()                      │  │
│  │   - TokenStore (Fernet-encrypted JSON)       │  │
│  │   - gmail_list_messages()                    │  │
│  │   - calendar_list_events()                   │  │
│  └──────────┬───────────────────────────────────┘  │
│             │ HTTPS                                │
└─────────────┼──────────────────────────────────────┘
              │
              ▼
    ┌─────────────────────┐
    │  Google APIs        │
    │  - OAuth 2.0        │
    │  - Gmail API        │
    │  - Calendar API     │
    │  - Userinfo API     │
    └─────────────────────┘
```

---

## Troubleshooting

**`/auth/google/status` returns `configured: false`**
- The `.env` file is missing or env vars aren't loaded
- Check: `docker exec ai-api-gateway env | grep GOOGLE`
- Fix: ensure `C:\Users\jr04b\ai-system\.env` exists with `GOOGLE_CLIENT_ID`
  and `GOOGLE_CLIENT_SECRET` set

**Callback returns `400 invalid or expired state token`**
- The `state` token is single-use and expires after 10 minutes
- Fix: restart the flow by calling `/auth/google/start` again

**Callback returns `400 token exchange failed: redirect_uri_mismatch`**
- The redirect URI in Google Cloud Console doesn't match exactly
- Must be: `http://localhost:8000/auth/google/callback` (note: `http`, not
  `https`; `localhost`, not `127.0.0.1`; port `8000`)

**`/gmail/messages` returns `401 no tokens stored for ...`**
- You haven't completed the OAuth flow yet
- Fix: visit `/auth/google/start` and consent

**`/gmail/messages` returns `401 token for ... expired and no refresh_token`**
- The refresh token is missing or revoked on Google's side
- Fix: disconnect via `/auth/google/disconnect`, then reconnect via
  `/auth/google/start`

**Gateway build fails on pip install**
- The gateway uses a slim CPU-only image with prebuilt wheels (no compiler
  needed). If a build fails, check network access to PyPI first
- `scikit-learn`/`numpy` install from wheels on Python 3.11 — no CUDA
  toolkit required anywhere in this step

---

## Future work

Not implemented yet, but the architecture supports it cleanly:

- **Gmail send** — POST endpoint that wraps `gmail.send()` API with explicit
  confirmation flow
- **Calendar write** — create/update/delete event endpoints
- **Multi-user** — `TokenStore` is already keyed by email, just needs an
  auth layer on the API
- **LLM-driven actions** — "summarize this thread", "draft a reply based
  on my calendar", etc. (uses the local Ollama models)
- **Token refresh observability** — metrics on how often refresh happens
