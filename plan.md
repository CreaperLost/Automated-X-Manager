# X-Automation — AI Content Research, Creation, Curation & Publisher

## Summary

A single-user, local Streamlit web app that turns curated X (Twitter) content
into original, on-brand posts. The flow is a four-tab loop: **Fetch → Review →
Create → Publish**. The user seeds a pool of X accounts to monitor, picks the
tweets they like, lets MiniMax ghostwrite a similar post that weaves in their
own project links, and finally posts it now or schedules it for later.

**Scope.** Read recent tweets from a configured handle pool, let the user
select and annotate favorites, generate an AI draft grounded in a local
`Project:Link` file, attach images, and publish to a single X account
immediately or on a schedule. One X account per installation. One user per
installation. No multi-tenancy, no team features, no analytics dashboard.

**Key design decisions (locked with the user).**

- **UI.** Streamlit local web app (single command, single browser tab,
  no Node/Rust toolchain).
- **URL post cost.** X charges `$0.015` for a plain post and `$0.200` for a
  post that contains a URL — a 13.3× gap. The app **always emits a
  link-containing draft as a two-post thread** (main tweet + reply with the
  link), totalling `$0.030` per link post and saving ~85% vs. the inline
  alternative. The cost breakdown is shown in the Publish tab before every
  send; the user can override per-draft if they really want an inline link.
- **Stack.** Python 3.11+, Streamlit, SQLite, httpx (X API), OpenAI SDK
  (MiniMax API is OpenAI-compatible), APScheduler, Pydantic, PyYAML,
  python-dotenv. Single `requirements.txt`. Runs on Windows PowerShell and
  WSL/Linux.
- **Authentication.** OAuth 2.0 Authorization Code with PKCE for writes
  (scopes: `tweet.read`, `tweet.write`, `users.read`, `media.write`,
  `offline.access`). App-only Bearer token for reads. A one-time
  `scripts/auth_setup.py` runs a tiny local HTTP server on
  `http://127.0.0.1:8765/callback` to capture the redirect — Streamlit
  itself does not host the callback.
- **Scheduling.** X API v2 has no native schedule endpoint. APScheduler
  `BackgroundScheduler` runs in-process; jobs persist in the SQLite job
  store and fire while the app is open. UI shows a banner when a scheduled
  post is within five minutes so the user knows to keep the tab open.
- **Phase 0 prerequisite: X API Skills installed first.** Before any app
  code, the X API Skills from `https://docs.x.com/tools/ai` are installed:
  `skill.md` and `llms.txt` are committed to `data/`; the X OpenAPI spec
  is bundled as a reference; the XMCP and Docs MCP servers are configured
  in the development environment so the agent building the project can
  call X API endpoints and read X docs on the fly. Phase 0 ends with
  `scripts/verify_setup.py` printing `OK: X API skills installed`.

**Non-goals (explicitly out of scope for v1).**

- Multi-account X publishing, multi-user, multi-tenant.
- Real-time filtered-stream monitoring (Pro tier, $5k/mo, not justified).
- Full-archive search (Enterprise only; 7-day recent search is enough).
- Like, follow, reply, DM, bookmark, mute, or any other write beyond
  `POST /2/tweets` on the user's own timeline.
- Cross-platform publishing (Bluesky, LinkedIn, etc.).
- A hosted/SaaS deployment. This is a personal local tool.
- A web crawler/scraper fallback. The X API is the only read path.

---

## Current state

`C:\Users\George\Desktop\x-automation` is **empty**. This plan is the
greenfield setup; it is self-contained and defines its own conventions
rather than inheriting any from neighbouring folders.

The X-API and MiniMax API surfaces used here were both verified against
current (Aug 2026) documentation. Headline facts that shape the design:

| Item | Value | Source |
|---|---|---|
| X reads, third-party post | $0.005 per post | xpoz / bundle / data365 Aug 2026 |
| X reads, user profile | $0.010 per profile | same |
| X owned reads | $0.001 per resource | same |
| X `POST /2/tweets` plain | $0.015 per post | same |
| X `POST /2/tweets` with URL | $0.200 per post | same |
| X `GET /2/tweets/search/recent` rate limit | 450/15min app, 300/15min user | socialnexis Aug 2026 |
| X `GET /2/users/:id/tweets` | 3,500/15min app, 5,000/15min user | same |
| X `POST /2/tweets` rate limit | 100/15min user, 10k/24h app | bundle Aug 2026 |
| X OAuth 2.0 access token TTL | ~2 hours (refresh required) | singhamandeep Aug 2026 |
| X media upload | v2 `POST /2/media/upload` (multipart), 5 MB/img, 4 img/post | bundle Aug 2026 |
| MiniMax chat endpoint | `https://api.minimaxi.com/v1/chat/completions` | tu-zi, morphllm Aug 2026 |
| MiniMax-M2.7 price | $0.30/M input, $1.20/M output | morphllm Aug 2026 |
| MiniMax-M3 | 1M context, multimodal (text/image/video in) | cometapi Aug 2026 |

The plan treats these as the contract. If any of them shift before
implementation, the affected module is the one to revisit (call-outs in the
component list below).

---

## Proposed architecture

### Repository layout

```
x-automation/
├── AGENTS.md                       # Engineering approach (this repo's contract)
├── README.md                       # Setup, first run, day-to-day use
├── requirements.txt                # Pinned deps
├── pyproject.toml                  # Tooling: ruff, pytest config
├── .env.example                    # Secret template
├── .gitignore                      # Excludes data/, .env, __pycache__
├── config/
│   ├── accounts.yaml               # Pool of X handles to monitor
│   ├── projects.yaml               # Project:Link pairs (the "mostly pairs" file)
│   └── settings.yaml               # Default model, poll window, etc.
├── data/                           # Gitignored. Runtime state lives here.
│   ├── state.db                    # SQLite: app data
│   ├── scheduler.sqlite            # APScheduler job store
│   ├── oauth_tokens.json           # X OAuth tokens (encrypted-at-rest by OS)
│   └── media_cache/                # Images uploaded but not yet posted
├── src/x_auto/
│   ├── __init__.py
│   ├── app.py                      # Streamlit entrypoint
│   ├── config.py                   # Env + YAML loading
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── layout.py               # Sidebar (cost meter, status, schedule)
│   │   ├── tab_fetch.py
│   │   ├── tab_review.py
│   │   ├── tab_create.py
│   │   └── tab_publish.py
│   ├── x/
│   │   ├── __init__.py
│   │   ├── auth.py                 # OAuth 2.0 PKCE + refresh
│   │   ├── client.py               # X API v2 httpx wrapper
│   │   ├── media.py                # /2/media/upload INIT/APPEND/FINALIZE/STATUS
│   │   └── costs.py                # URL detection, $ estimator
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── client.py               # MiniMax OpenAI-compatible client
│   │   ├── prompts.py              # Templates, JSON-mode schema
│   │   └── projects.py             # Project:Link resolver + RAG-lite retrieval
│   ├── store/
│   │   ├── __init__.py
│   │   ├── db.py                   # Schema + migrations
│   │   ├── models.py               # Pydantic v2 models
│   │   └── repos.py                # AccountRepo, TweetRepo, DraftRepo, ScheduleRepo
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── runner.py               # APScheduler bootstrap + job handler
│   └── utils/
│       ├── __init__.py
│       ├── text.py                 # URL regex, char counting, slug
│       └── files.py                # Image validation (≤5 MB, JPEG/PNG/WebP/GIF)
├── scripts/
│   ├── auth_setup.py               # One-time OAuth flow (local callback server)
│   └── run.sh                      # Convenience: streamlit run src/x_auto/app.py
└── tests/
    ├── conftest.py                 # Fixtures: in-memory SQLite, recorded HTTP
    ├── test_text.py
    ├── test_costs.py
    ├── test_projects.py
    ├── test_prompts.py
    ├── test_db.py
    ├── test_x_client.py            # Uses recorded fixtures
    ├── test_ai_client.py           # Uses recorded fixtures
    └── test_scheduler.py
```

### End-to-end flow

```
   ┌─────────────────────────────────────────────────────────────┐
   │  TAB 1 · FETCH                                              │
   │  Read accounts.yaml → GET /2/users/by/username/:h ×N       │
   │                   → GET /2/users/:id/tweets (last 20)        │
   │  Persist tweets in `tweets` (status='new')                  │
   │  Sidebar: $X.XX spent this session                          │
   └─────────────────────┬───────────────────────────────────────┘
                         ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  TAB 2 · REVIEW                                             │
   │  List status='new' tweets; multi-select with checkbox.      │
   │  Optional "why I like it" note. Marking moves to selected.  │
   │  De-duplicates against previous fetches.                    │
   └─────────────────────┬───────────────────────────────────────┘
                         ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  TAB 3 · CREATE                                             │
   │  Pick one selected tweet. Pick ≥1 project from projects.yaml│
   │  Upload ≥0 images. Pick tone hint. Click Generate.          │
   │  MiniMax /v1/chat/completions (json_object mode)            │
   │  Editable draft preview: "Main: ... / Reply: <link>"        │
   │  Save → drafts table (status='draft' → user clicks Final)   │
   └─────────────────────┬───────────────────────────────────────┘
                         ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  TAB 4 · PUBLISH                                            │
   │  List status='final' drafts. Per draft:                     │
   │    - Cost preview (with link-in-reply savings)              │
   │    - "Post now" → media upload → POST main → POST reply    │
   │    - "Schedule" → datetime picker → APScheduler job        │
   │  Post-log table at bottom: last 20 sends with x_tweet_id    │
   └─────────────────────────────────────────────────────────────┘
```

### Data model (SQLite — `data/state.db`)

```sql
CREATE TABLE accounts (
  handle           TEXT PRIMARY KEY,           -- e.g. "OpenAI"
  user_id          TEXT NOT NULL,
  display_name     TEXT,
  added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_fetched_at  TIMESTAMP
);

CREATE TABLE tweets (
  id               TEXT PRIMARY KEY,           -- X tweet id (string)
  account_handle   TEXT NOT NULL REFERENCES accounts(handle),
  text             TEXT NOT NULL,
  created_at       TIMESTAMP NOT NULL,
  public_metrics   TEXT,                       -- JSON: {like, retweet, reply, quote, impression}
  fetched_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status           TEXT NOT NULL DEFAULT 'new' -- 'new' | 'selected' | 'archived'
);
CREATE INDEX idx_tweets_status ON tweets(status, fetched_at DESC);

CREATE TABLE projects (
  name             TEXT PRIMARY KEY,           -- e.g. "Acme Search"
  url              TEXT NOT NULL,
  description      TEXT,
  tags             TEXT                        -- comma-separated, for retrieval
);

CREATE TABLE drafts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  source_tweet_id  TEXT REFERENCES tweets(id),
  body             TEXT NOT NULL,              -- the main tweet
  link_url         TEXT,                       -- the project URL (will go in reply)
  image_paths      TEXT,                       -- JSON array of local paths
  tone             TEXT,
  status           TEXT NOT NULL DEFAULT 'draft', -- 'draft' | 'final' | 'posted' | 'scheduled' | 'failed'
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finalized_at     TIMESTAMP,
  posted_at        TIMESTAMP,
  x_tweet_id       TEXT,                       -- main tweet id from X
  x_reply_id       TEXT,                       -- reply tweet id from X
  cost_usd         REAL,
  error            TEXT
);
CREATE INDEX idx_drafts_status ON drafts(status);

CREATE TABLE post_log (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id         INTEGER REFERENCES drafts(id),
  action           TEXT,                       -- 'post_now' | 'schedule' | 'fire_scheduled' | 'retry'
  cost_usd         REAL,
  result           TEXT,                       -- 'success' | 'failed' | 'rate_limited' | 'auth_error'
  detail           TEXT,                       -- X response id or error message
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE oauth_tokens (
  key              TEXT PRIMARY KEY,           -- 'x_user'
  access_token     TEXT NOT NULL,
  refresh_token    TEXT NOT NULL,
  expires_at       TIMESTAMP NOT NULL,
  scope            TEXT NOT NULL,
  bearer_token     TEXT                        -- app-only bearer for reads
);
```

Pydantic models in `store/models.py` mirror these. Repos in `store/repos.py`
expose the methods the UI needs (`get_new_tweets`, `mark_selected`,
`save_draft`, `finalize_draft`, `mark_posted`, …) — no ORM, just
parameterized SQL.

### X-API client (`src/x_auto/x/`)

**`auth.py`** — OAuth 2.0 PKCE.

- `build_authorize_url(client_id, redirect_uri, scopes, state, code_challenge)`
- `exchange_code(client_id, code, code_verifier, redirect_uri) -> Tokens`
- `refresh(client_id, refresh_token) -> Tokens` (refresh tokens rotate; always
  persist the new one)
- `load_tokens() / save_tokens()` — read/write `data/oauth_tokens.json`
  (mode 0600). The file is gitignored.
- A token manager `TokenManager` checks `expires_at` and auto-refreshes
  ~5 minutes before expiry, with one retry on `401`.

**`client.py`** — thin async httpx wrapper. Endpoints used:

| Endpoint | Auth | Cost | Used in |
|---|---|---|---|
| `GET /2/users/by/username/:handle` | app bearer | $0.010 | Fetch tab |
| `GET /2/users/:id/tweets?max_results=20&tweet.fields=public_metrics,created_at&exclude=replies,retweets` | app bearer | $0.005 × N | Fetch tab |
| `GET /2/tweets/search/recent?query=from:handle&max_results=10` | app bearer | $0.005 × N | (optional, Phase 2+) |
| `GET /2/users/me` | user OAuth | $0.010 | Settings tab (verify connected account) |
| `POST /2/tweets` (main) | user OAuth | $0.015 | Publish |
| `POST /2/tweets` (reply) | user OAuth | $0.015 | Publish |
| `POST /2/media/upload` (INIT/APPEND/FINALIZE) | user OAuth (`media.write`) | (free) | Publish |
| `GET /2/media/upload?command=STATUS&media_id=…` | user OAuth | (free) | Publish |
| `DELETE /2/tweets/:id` | user OAuth | $0.010 | Post-log "undo" button |

- Centralised retry: one retry on 5xx and on `429` after honoring
  `x-rate-limit-reset`. Surface rate-limit info in the UI sidebar.
- Every call is logged with cost → `post_log` table or in-memory session
  meter (sidebar shows real-time `$ this session`).

**`media.py`** — three commands against `POST /2/media/upload`.

- `init(total_bytes, mime) -> media_id` (≤5 MB for image → simple one-shot)
- For images (≤5 MB), use the one-shot endpoint
  `POST /2/media/upload` with multipart `media` field — simpler than
  INIT/APPEND/FINALIZE and the official quickstart covers it. Reserve
  chunked upload for video (out of scope v1).
- Status polling only for video; for images the response is final.
- Returns `media_id`; the publish flow attaches up to 4 ids to
  `media.media_ids`.
- File validation happens in `utils/files.py` before upload (size, MIME).

**`costs.py`** — pure functions, easy to unit test.

- `contains_url(text: str) -> bool` — regex matching X's autolinker:
  `https?://`, `www.`, and bare domains with the common TLDs
  (`.com .io .ai .app .dev .co .net .org .me` plus a few more).
  Spelled-out domains (`"brand dot com"`) are NOT detected.
- `estimate_post_cost(text, has_image, link_inline) -> CostBreakdown`
  returning a dataclass: `{main: float, reply: float, total: float,
  reason: str}`. The UI uses this verbatim.
- `session_meter` — running counter; flushed to `post_log` on every
  successful send; shown in the sidebar.

### AI client (`src/x_auto/ai/`)

**`client.py`** — MiniMax via the official `openai` SDK pointed at
`https://api.minimaxi.com/v1`.

```python
from openai import OpenAI
client = OpenAI(
    base_url="https://api.minimaxi.com/v1",
    api_key=settings.minimax_api_key,
)
resp = client.chat.completions.create(
    model=settings.model_id,           # default "MiniMax-M2.7"
    response_format={"type": "json_object"},
    messages=[...],
    temperature=0.7,
    max_tokens=400,
)
```

- Default model: `MiniMax-M2.7` (200k context, $0.30/$1.20 per 1M tokens —
  right size for a tweet draft). User can override in `settings.yaml` to
  `MiniMax-M3` (1M context, multimodal — use only if image-conditioned
  generation is requested) or `MiniMax-M2.7-highspeed` (faster, same price
  band) — both visible in `config/settings.yaml` with comments.
- Streaming not needed: drafts are short and the UI waits on a button.
- `MAX_RETRIES=2`; if MiniMax returns malformed JSON, retry once with a
  system-side "fix your JSON" message; if it still fails, surface the
  error and let the user retry.

**`prompts.py`** — two templates:

1. `DRAFT_SYSTEM` — sets the role, length cap (≤280 chars for the main
   body; replies can be longer but stay under 700), style guide (one idea
   per tweet, no hashtag spam, no emoji-stuffing, no clickbait), and the
   hard constraint that the main body MUST NOT contain a URL (it goes in
   the reply). A condensed summary of the X API constraints is baked
   into the system message (auth required for writes, ≤280 chars, 4
   images max, 5 MB per image, URL posts cost $0.200, `t.co` shortening
   makes every URL count as 23 chars), so the model has the rules even
   if `data/x_skill.md` is unavailable. When the cached skill file
   *is* present, a final sentence tells the model it can read it via
   its tool list for edge cases — keeps the prompt small while leaving
   the door open for accurate answers.
2. `DRAFT_USER` — assembles: selected tweet (text + author), the chosen
   project entries (`name`, `url`, one-line description`), tone hint,
   image-attach note ("you are attaching N images; you do not need to
   describe them, just write the caption text").

The assistant is told to return a JSON object with this exact shape
(enforced by `response_format` + a post-parse check):

```json
{
  "main": "the main tweet text, ≤280 chars, no URL",
  "reply": "the project link, exactly the URL string",
  "reasoning": "one short sentence: why this framing"
}
```

The UI parses this; if `main` accidentally contains a URL, the UI strips
it and warns the user ("AI included a URL in the main body — moved to
reply to save $0.170"). This is the safety net for the cost invariant.

**`projects.py`** — `Project:Link` resolver.

- Loads `config/projects.yaml` at startup into the `projects` table.
- Schema: `{name, url, description, tags}` per entry. The user said
  "mostly pairs of `Project:Link`", so `name` and `url` are the only
  required fields; `description` and `tags` are optional for richer
  retrieval.
- Retrieval for the AI prompt: in v1, the user picks projects from a
  multiselect in the Create tab. A "match by tweet text" button (TF-IDF
  over `name + tags + description`) is a v1.1 add — not in v1, so the
  prompt stays deterministic and the user keeps control.

### Streamlit app (`src/x_auto/app.py` and `ui/`)

**`app.py`** — bootstraps:

- Loads config, ensures `data/` exists, runs DB migrations, loads tokens.
- Starts the APScheduler `BackgroundScheduler` (daemon thread) if not
  already running — guarded by a lockfile so the app can be reloaded
  without double-scheduling.
- Calls `st.set_page_config(layout="wide", page_title="X-Automation")`
  and `st.tabs(["1 · Fetch", "2 · Review", "3 · Create", "4 · Publish"])`.
- Renders `ui/layout.render_sidebar()` for cost meter, connected account,
  next-scheduled-post banner, and dev links.

**Sidebar (`ui/layout.py`):**

```
┌──────────────────────────────┐
│ X-Automation                 │
│ Account: @yourhandle         │
│ Session spend: $0.045        │
│   ↳ 9 reads ($0.045)         │
│   ↳ 0 posts ($0.000)         │
│ ──────────────────────────── │
│ Next scheduled: 14:30        │
│ ⚠ Keep this tab open         │
│ ──────────────────────────── │
│ Model: MiniMax-M2.7          │
│ [ Re-authorize X ]           │
└──────────────────────────────┘
```

**Tab 1 — Fetch (`ui/tab_fetch.py`):**

- Shows the current handle pool from `accounts.yaml` (editable inline).
- "Fetch recent" button → for each handle: lookup user_id, fetch last 20
  tweets (excluding replies/retweets), upsert into `tweets` table.
- Progress bar + per-handle status ("@naval · 14 new, 6 already seen").
- "Last fetch" timestamp per handle.
- Cost: $0.010 per handle + $0.005 per new tweet; sidebar updates live.

**Tab 2 — Review (`ui/tab_review.py`):**

- `st.data_editor` over `status='new'` tweets with a `selected` boolean
  column. Optional `note` column.
- Filter chips: by author, by min likes, by keyword.
- "Promote to selected" button moves rows to `status='selected'`. The
  "Archive" button drops them from view but keeps them in DB.
- A second panel shows currently selected (from previous sessions)
  waiting to be used in Create.

**Tab 3 — Create (`ui/tab_create.py`):**

- Dropdown: pick one selected tweet (or "free write, no inspiration").
- Multiselect: pick ≥1 project from `projects` table (loaded from YAML).
- File uploader: 0–4 images. Each is validated (≤5 MB, MIME) and stored
  in `data/media_cache/<uuid>.<ext>`. Preview thumbnails inline.
- Tone selectbox: neutral / enthusiastic / analytical / contrarian / hype.
- "Generate draft" button → calls `ai.client.generate_draft(...)`,
  parses JSON, runs the URL-safety check, renders editable previews:

  ```
  ┌─ Main (≤280 chars) ─────────────────────┐
  │ [editable textarea]                      │
  └──────────────────────────────────────────┘
  ┌─ Reply (link) ──────────────────────────┐
  │ https://acme.com/search                  │
  └──────────────────────────────────────────┘
  Cost: $0.015 (main) + $0.015 (reply) = $0.030
  vs. inline URL: $0.200 — you save $0.170
  ```

- "Regenerate", "Save as draft", "Save as final" buttons. Saving as
  `final` is the gate to appear in the Publish tab.

**Tab 4 — Publish (`ui/tab_publish.py`):**

- Lists drafts `status='final'`, newest first.
- Per draft: cost preview, image thumbnails, "Post now" and "Schedule"
  buttons.
- "Post now" flow:
  1. If images: upload each to `POST /2/media/upload` → collect media_ids.
  2. `POST /2/tweets` with `text=main` and `media.media_ids=…` → capture
     `x_tweet_id` from response.
  3. `POST /2/tweets` with `text=<project url>` and
     `reply.in_reply_to_tweet_id=x_tweet_id` → capture `x_reply_id`.
  4. Update `drafts` row to `posted`, write `post_log`, update sidebar
     cost.
- "Schedule" flow:
  1. `st.datetime_input` for fire time (must be future, ≤30 days out).
  2. Insert a draft in `status='scheduled'` and a row in
     `schedules` table.
  3. APScheduler job fires at the time; the same Post-now flow runs in
     the background.
- Post-log table at the bottom: last 20 sends with `x_tweet_id` linking
  to the live tweet, retry/delete actions.

### Scheduler (`src/x_auto/scheduler/runner.py`)

- Uses `APScheduler.schedulers.background.BackgroundScheduler`.
- Job store: `SQLAlchemyJobStore(url="sqlite:///data/scheduler.sqlite")`
  so jobs survive an app restart.
- One job per scheduled draft, `id=f"draft_<id>"`, `run_date=fire_at`,
  `misfire_grace_time=300` (5 min), `coalesce=True`.
- Job handler calls a shared `publish.publish_draft(draft_id)` function
  used by both the UI button and the scheduler.
- Bootstrapping is idempotent: at app start, scan the `schedules` table
  for `status='pending'` rows whose `fire_at` is in the past and run
  them immediately (with a "fired late" log line). This handles the case
  where the app was closed at fire time.
- Surface a "missed fire" warning in the sidebar so the user knows.

### OAuth setup (`scripts/auth_setup.py`)

Streamlit does not host an OAuth callback. The cleanest pattern:

1. User runs `python scripts/auth_setup.py` once.
2. Script reads `X_CLIENT_ID` and `X_CLIENT_SECRET` from `.env`.
3. Computes PKCE `code_verifier` + `code_challenge`.
4. Opens the browser to
   `https://x.com/i/oauth2/authorize?...` with the right scopes and
   `redirect_uri=http://127.0.0.1:8765/callback`.
5. Starts a `http.server.HTTPServer` on `127.0.0.1:8765` that captures
   the `code` query param.
6. Exchanges the code for tokens; persists to `data/oauth_tokens.json`
   with mode `0600`. Also requests the app-only bearer from the
   `/2/oauth2/token` endpoint with `client_credentials` grant.
7. Prints a confirmation and exits.

The Streamlit app reads `data/oauth_tokens.json` from then on. The
"Re-authorize X" button in the sidebar simply re-launches this script.

### X's official AI-agent resources (informational)

X publishes first-class resources for AI agents that use their API, indexed
at `https://docs.x.com/tools/ai`. They are not native X AI generation
endpoints — X still has no "generate a tweet" endpoint — but they are
useful inputs and a future integration path. Recorded here so a future
agent doesn't re-discover them.

| Resource | What it is | Used by this plan |
|---|---|---|
| `https://docs.x.com/skill.md` | Capability summary in agentskills.io format: auth methods, common endpoints, rate limit headers, query parameters, decision guidance, common gotchas, verification checklist. | **Yes, v1.** Cached to `data/x_skill.md` on first run. Injected as a system-message reference inside the MiniMax prompt (see `ai/prompts.py` below) so the model knows the published X constraints (≤280 chars, $0.200 URL surcharge, OAuth scopes, media limits) without us re-typing them. |
| `https://docs.x.com/llms.txt` | Documentation index for LLM ingestion. | **Yes, Phase 0** (bundled at `data/x_llms.txt`). Available at runtime if the prompt needs to look up a specific endpoint on the fly; not loaded by default to keep the request small. |
| `https://api.x.com/2/openapi.json` | Machine-readable API spec. | **Yes, Phase 0** (bundled at `data/x_openapi.json` as a reference and tiebreaker for ambiguity). **No at runtime v1** — the four endpoints we use are hand-written; OpenAPI is for cross-checking, not generating. |
| `XMCP` (X's hosted MCP server) | Exposes 200+ X API endpoints as MCP tools. | **Yes, Phase 0** (configured in the dev environment's MCP list so the building agent can call X endpoints during implementation). **No at runtime v1** — the deployed app uses the direct httpx client, not XMCP, because we don't want a hosted-MCP hop in the publish path. Listed as a v2 alternative if the user wants to swap `x/client.py` for an MCP-native client. |
| `Docs MCP` | Search and read any X docs page via MCP. | **Yes, Phase 0** (configured for the building agent). Not used at runtime. |

The system prompt fed to MiniMax includes a compact, self-authored
condensation of the X constraints (so a transient network failure on
`docs.x.com` cannot break draft generation), with a verbatim line at the
bottom — *"For the full X API capability reference, see
`data/x_skill.md` which is in the agent's tool list"* — so the model
knows it can pull the canonical document when it needs an edge-case
answer.

### Config files

`config/accounts.yaml`:
```yaml
- handle: naval
- handle: paulg
- handle: levelsio
```

`config/projects.yaml`:
```yaml
- name: Acme Search
  url: https://acme.com/search
  description: A fast, privacy-respecting meta-search engine.
  tags: [search, privacy, saas]
- name: Helios RAG
  url: https://helios.dev
  description: Open-source RAG framework.
  tags: [rag, ai, oss]
```

`config/settings.yaml`:
```yaml
minimax:
  base_url: https://api.minimaxi.com/v1
  model_id: MiniMax-M2.7
  temperature: 0.7
  max_tokens: 400
x:
  recent_max_results: 20
  exclude: [replies, retweets]
  rate_limit_buffer_seconds: 5
schedule:
  max_lookahead_days: 30
  min_lead_minutes: 5
ui:
  page_title: X-Automation
```

`AGENTS.md` codifies this repo's engineering contract (see the
"Engineering approach" section below). `README.md` walks through the
setup steps: X dev app, `pip install -r requirements.txt`, `cp
.env.example .env`, `python scripts/auth_setup.py`, `streamlit run
src/x_auto/app.py`.

### State, ownership, concurrency, failure, recovery

- **Single-process, single-user.** No locks needed beyond APScheduler's
  internal ones. SQLite in WAL mode for read/write concurrency between the
  UI thread and the scheduler thread.
- **Token lifetime.** Access token expires ~2h. `TokenManager.get_valid_token()`
  refreshes proactively; if the refresh itself fails (refresh token
  rotated elsewhere, revoked), it surfaces a re-auth banner and the
  Publish tab greys out.
- **Cost meter.** In-memory accumulator; flushed to `post_log` on each
  successful X call. Sidebar reads both the accumulator and the DB on
  every rerun — they reconcile via `cost_usd` and `detail.x_tweet_id`
  (success) or `result='failed'` (no charge).
- **Media ids expire in 24h.** If a user leaves a draft for a day, we
  re-upload at publish time. The cached file is kept; only the X-side
  `media_id` is short-lived.
- **Schedule missed because the app was closed.** Boot-time reaper
  finds `status='pending'` rows with `fire_at < now - 5min`, fires
  them, marks `fired_late=true` in `post_log`, and posts a sidebar
  warning so the user notices.
- **Rate limits.** `client.py` reads `x-rate-limit-remaining` and
  `x-rate-limit-reset` on every response. On 429, parse the
  `retry_after` and show a "X rate-limited, retrying in N seconds"
  status with a spinner. Single retry; second 429 surfaces to the UI.
- **MiniMax failures.** JSON-mode + post-parse validation catches
  malformed outputs. Network errors → 2 retries with exponential
  backoff (1s, 3s). Persistent failure → "Generate draft" button shows
  the error verbatim.
- **Image validation.** Reject >5 MB and wrong MIME in `utils/files.py`
  *before* the upload attempt. The user sees a clear "Image must be
  ≤5 MB and JPEG/PNG/GIF/WebP" message.

### Tests and acceptance

**Unit tests (pytest, runnable on Windows and WSL):**

| Module | Cases |
|---|---|
| `utils/text.py` | URL detection (https, www, bare domains with/without TLD, spelled-out) |
| `utils/text.py` | char counting with URL-as-23-chars (X's `t.co` shortening) |
| `x/costs.py` | plain post $0.015, image-only $0.015, image+plain $0.015, with URL inline $0.200, with URL as reply $0.030 |
| `ai/projects.py` | load YAML, handle missing fields, normalise URLs, dedupe by URL |
| `ai/prompts.py` | template renders, JSON schema validator accepts good output, rejects missing keys |
| `store/repos.py` | CRUD on each table, in-memory SQLite fixture |
| `x/client.py` | URL building, query string serialisation, error mapping (uses recorded fixtures) |
| `ai/client.py` | sends expected payload, parses response, raises on bad JSON (recorded fixtures) |
| `scheduler/runner.py` | job registration, idempotent bootstrap, missed-fire reaper |

**Integration / smoke (manual, once per release):**

1. `python scripts/auth_setup.py` completes the OAuth flow against the
   user's real X dev app in dev mode.
2. Fetch tab pulls at least 3 tweets from 2 handles — sidebar shows
   `Session spend: $0.040` (2 user lookups @ $0.010 + 6 new tweets
   @ $0.005 = $0.050; numbers will vary).
3. Review tab promotes 1 tweet to selected.
4. Create tab generates a draft referencing 1 project + 1 image.
5. Publish tab posts the draft to the user's dev-mode timeline
   (X dev mode restricts to the test account); the X UI shows a
   two-tweet thread; cost increments by $0.030.

**Acceptance criteria for v1:**

- The four tabs each complete end-to-end against a real X dev account in
  dev mode, with no manual code changes between stages.
- A scheduled draft posted at the requested time while the app is open.
- A draft created and posted does not contain a URL in the main tweet
  body (verified by the `contains_url` invariant check in the Create
  tab).
- The sidebar cost meter reconciles with the `post_log` table to the
  cent.
- All `requirements.txt` packages install cleanly on a fresh venv on
  Windows 11 (the user's machine) and on Ubuntu 22.04.
- `pytest -q` passes locally with no network calls (everything behind
  recorded fixtures).

---

## Implementation phasing

The plan ships in five phases. Each phase is a self-contained vertical
slice — a working app that you can `streamlit run` after each phase, or a
verified, in-place addition in Phase 0's case.

**Phase 0 — Install X API Skills (prerequisite).** Before any code is
written, the X API Skills that X publishes for AI agents (indexed at
`https://docs.x.com/tools/ai`) are installed into the project and the
development environment. This is a setup step, not a code step, but it
is the first thing the README walks through and the first thing the
AGENTS.md tells the next agent to verify. Five sub-steps:

1. **Bundle the X agent-skill documents into the repo.** Download
   `https://docs.x.com/skill.md` → `data/x_skill.md` and
   `https://docs.x.com/llms.txt` → `data/x_llms.txt` (both committed
   so the repo is self-contained; refresh via a `scripts/refresh_x_docs.py`
   helper if X publishes an update). These are the capability summary
   and the LLM-ingestible documentation index, in the formats X designed
   for agent consumption.
2. **Add the X OpenAPI spec as a reference.** Fetch
   `https://api.x.com/2/openapi.json` and save it as
   `data/x_openapi.json`. Useful when hand-writing the client and as a
   fallback when the docs are ambiguous.
3. **Configure the XMCP MCP server** in the development environment
   (mavis `mcp.json` or equivalent) so the agent building the project
   can call any of the 200+ X API endpoints directly. Follow the
   connection steps at `https://docs.x.com/tools/mcp`. The endpoint
   list XMCP exposes is the source of truth for the four endpoints the
   runtime `x/client.py` hand-writes; if XMCP's behavior ever disagrees
   with our hand-written client, the OpenAPI spec is the tiebreaker.
4. **Configure the Docs MCP server** (also at `docs.x.com/tools/ai`)
   so the building agent can search and read X API documentation on
   the fly during implementation. This replaces "I have to web-fetch
   the docs in every session" with a one-time setup.
5. **Document the setup in `AGENTS.md`.** The first section is "Phase 0
   verification": the next agent must run a smoke test that confirms
   `data/x_skill.md` and `data/x_llms.txt` are present and non-empty,
   the XMCP server is reachable, and the Docs MCP server is reachable.
   If any of these fail, the next agent fixes the setup *before* writing
   any Phase 1+ code.

Why this is a separate phase, not folded into Phase 1:

- It is a one-time setup, not iterative code. Putting it in its own
  phase keeps the per-phase acceptance criteria focused on a working
  app, not on environment plumbing.
- It benefits both the building agent (via MCP) and the runtime app
  (via `data/x_skill.md` as a MiniMax prompt reference). Without Phase 0,
  the AI prompt in `ai/prompts.py` either has to re-fetch the docs
  every session or carry a stale copy in the source.
- The X docs page is the canonical source. If X changes which skills
  it publishes (XMCP rename, new llms.txt, etc.), only Phase 0 changes
  — Phase 1+ are insulated.

Phase 0 ends with the README's "Setup" section and `scripts/verify_setup.py`
printing `OK: X API skills installed (skill.md, llms.txt, XMCP, Docs MCP)`.

**Phase 1 — Skeleton + Fetch + Review (read-only).** Project scaffold,
`accounts.yaml`, `projects.yaml`, SQLite + repos, X client with
app-only bearer, Fetch tab, Review tab. Acceptance: 50 tweets
ingested from 3 handles, 5 selected, no writes to X.

**Phase 2 — AI Create.** MiniMax client, prompt templates, JSON
validation, URL-safety check, Create tab. Acceptance: a draft is
generated, edited, and saved with no URL in the main body.

**Phase 3 — Publish now.** OAuth 2.0 PKCE, `scripts/auth_setup.py`,
media upload, two-tweet publish, cost meter, Publish tab
"Post now" only. Acceptance: a final draft posts as a two-tweet
thread on the user's test timeline.

**Phase 4 — Schedule.** APScheduler integration, Schedule flow,
boot-time reaper, sidebar banner. Acceptance: a scheduled draft
posts at the requested time while the app is open, and a draft
scheduled during a closed-app window posts when the app reopens
with a "fired late" log line.

Each phase ends with a manual smoke run against the X dev account
and a green `pytest`. Phase 0 ends instead with a green
`scripts/verify_setup.py` and the four X API Skill artifacts present
in the repo.

---

## Engineering approach

The repo's own `AGENTS.md` codifies the following rules so future
agent runs (including fresh ones) stay on track. All rules apply
within `C:\Users\George\Desktop\x-automation` only — the plan and any
agent following it must never read, write, or reference files outside
this workspace folder. No agent following this plan may import from,
symlink to, `pip install` from, or otherwise touch any other folder on
the machine. All dependencies are vendored into the repo's own
`.venv`; all secrets are loaded from `.env` inside the repo; all
runtime state lives under `data/` inside the repo.

- Smallest vertical slice that satisfies the current phase's acceptance.
- One `requirements.txt` at the repo root. One Python venv (`.venv`).
- No frameworks, services, queues, ORMs, or plugin systems added without
  a current requirement. No "just in case" config flags.
- The Streamlit app is the product. CLI scripts (`scripts/auth_setup.py`)
  exist only to fill gaps Streamlit cannot serve.
- Real X-API verification before claiming a stage done; tests use
  recorded fixtures and never make real network calls.
- No secrets in git. `.env` is gitignored; `.env.example` is the source
  of truth for the secret shape. `data/` is gitignored.
- Windows PowerShell is the user's primary shell; scripts must run
  there. WSL is a secondary supported target.

---

## Unresolved decisions and risks

These are real, small, and tracked. None block v1.

1. **Project-link retrieval for the prompt.** v1 lets the user
   multiselect projects. A "match by tweet text" helper is a v1.1
   nice-to-have; deferred to keep the prompt deterministic and the
   UI in the user's control.
2. **Multi-account X publishing.** v1 is single-account. The data
   model and repos do not preclude a second account, but the OAuth
   flow and token store are single-tenant. A "second X account" is
   a v2 feature.
3. **Tone control beyond the dropdown.** The Create tab exposes
   a 5-option tone selector. More elaborate style tuning (e.g.
   "match my last 10 posts") is a v2 — needs a separate prompt and
   the historical-post reader.
4. **Image generation.** MiniMax's `image-01` could supply images
   when the user has none. Out of scope for v1; the Create tab
   requires the user to upload at least one image only if they
   want to attach one.
5. **Threading longer posts.** The X `POST /2/tweets` chain via
   `reply.in_reply_to_tweet_id` supports threads (2–25 parts). v1
   emits exactly 2 parts (main + reply). A "long thread" composer
   is a v2.
6. **Per-call idempotency keys.** The X API documents optional
   `Idempotency-Key` headers. Not used in v1; the draft-id is a
   natural key, and re-running a "Post now" requires an explicit
   user action, so collisions are unlikely.
7. **OAuth callback port (8765).** If the user has something else
   on 8765, the auth script will fail. Make the port overridable
   via `X_AUTH_CALLBACK_PORT` env var; document the X dev portal
   `http://127.0.0.1:8765/callback` registration step in the
   README.
8. **App-only bearer vs user-context reads.** Reads use the
   app-only bearer for cost (cheaper, $0.005 vs $0.005 — same price,
   but bearer tokens don't expire and don't need refreshing). If
   X changes the app-only tier again, switch to user-context reads;
   the `x/client.py` is the only file that needs to change.
9. **Streamlit rerun + APScheduler thread.** Streamlit reruns the
   whole script on every interaction. The scheduler is started
   exactly once via a `data/scheduler.lock` file check. If the
   user kills the app mid-job, the in-flight publish can leave a
   `status='final'` draft half-posted; the Publish tab exposes a
   "Retry" button that re-runs the publish flow, which is safe
   because each step is idempotent on `x_tweet_id`.
10. **Swap to XMCP for transport.** X now ships a hosted MCP server
    (`docs.x.com/tools/mcp`) that exposes 200+ X API endpoints as
    callable tools. v1 uses direct httpx because it is cheaper to
    reason about, has no extra transport, and lets us pin the
    four endpoints we actually need. A future revision could
    replace `src/x_auto/x/client.py` with an MCP-native client
    if the user adopts an MCP-aware assistant (Cursor, Windsurf,
    Claude) and wants one consistent protocol across the app
    stack. The data model, UI, scheduler, and AI prompt would
    not change.

---

## What ships in the repo at the end of v1

- All files in the layout above, plus `AGENTS.md` and `README.md`.
- `data/state.db` is created on first run; `data/x_skill.md`,
  `data/x_llms.txt`, and `data/x_openapi.json` are committed in Phase 0
  so the repo is self-contained for offline work. `.gitignore` excludes
  `data/`, `.env`, `__pycache__/`, `.venv/`, `*.pyc`.
- A smoke-test script `scripts/smoke.py` that runs the four tabs
  against recorded fixtures and prints a one-line pass/fail — useful
  as a self-check when an agent picks the repo up cold.
- `scripts/verify_setup.py` that confirms the four Phase 0 X API Skill
  artifacts (`data/x_skill.md`, `data/x_llms.txt`, `data/x_openapi.json`,
  and the running XMCP + Docs MCP servers) are present and reachable,
  and prints `OK: X API skills installed` or a list of what's missing.
- The plan that you are reading now (`plan.md` at the repo root,
  in addition to the canonical copy at the session artifacts path).
