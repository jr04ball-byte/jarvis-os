# V25 local backend contract

Every request below requires `Authorization: Bearer AI_API_TOKEN`.

- `GET /v1/companion/status?after=0`: version, push-to-talk capability, redacted activity history/cursor and maintenance outcome. Poll with the returned sequence as `after`; history is process-local and resets on restart.
- `POST /v1/companion/chat`: existing AgentChatRequest JSON, for example `{"model":"auto","messages":[{"role":"user","content":"Hello"}]}`. Replies are text/agent JSON. A `confirmation` response pauses sensitive execution.
- `POST /v1/companion/confirm`: `{"confirmation_id":"SERVER_TICKET","confirmed":true}` after presenting the exact proposed action to the user. `false` cancels. Existing expiry and single-use semantics apply.
- `POST /v1/companion/upload`: multipart form with one `file` and optional `folder` (default `Inbox`). Files are streamed to a temporary file, limited by `JARVIS_PHONE_UPLOAD_MAX_MB` (default 250 MB), hashed, then moved atomically into `JARVIS_PHONE_UPLOAD_DIR` (default `<ai-system>/phone-uploads`). Duplicate names receive ` (2)`, ` (3)`, and so on.
- `GET /v1/companion/files?limit=50`: newest uploaded files with relative path, size, and modification time.
- `GET /v1/companion/files/{folder}/{filename}`: download a stored companion file.

401 means the credential did not match; 503 means no server token is configured; 422 means invalid input; 429 means the existing agent limit was reached. Treat timeouts as unknown outcomes and do not automatically resend actions. No audio output or background microphone API is provided.
