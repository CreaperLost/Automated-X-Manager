# X-Automation

A single-user, local Streamlit app that turns curated X (Twitter)
content into on-brand posts. Four tabs in the main view: **Fetch →
Review → Create → Publish**. A "Settings" expander in the sidebar
holds the model picker and the Project:Link CSV editor. Powered by
the X API and the MiniMax API.

## Run and stop the app

The comprehensive boot script handles venv, deps, `.env`,
`verify_setup.py`, the OAuth flow (only if no tokens yet), and starts
Streamlit. **Re-running it is safe** — it only does the steps that
aren't already done.

### Windows PowerShell (your shell)

```powershell
.\scripts\boot.ps1
```

The first run will print `Creating .venv`, install requirements, copy
`.env.example` → `.env`, and stop with a message asking you to fill
in your X / MiniMax credentials. Edit `.env`, then re-run
`.\scripts\boot.ps1`; this time it completes the OAuth consent and
launches Streamlit on `http://localhost:8501`.

To stop the app: press **Ctrl-C** in the PowerShell window that ran
the script. The venv, the `.env`, and the OAuth tokens all persist, so
the next `.\scripts\boot.ps1` will skip straight to launch.

### macOS / Linux / WSL

```bash
./scripts/boot.sh
```

Same behavior. **Ctrl-C** to stop.

### Run only (assumes setup is already done)

If you've already completed first-time setup, you can launch Streamlit
directly without going through the boot script:

```powershell
# Windows
.venv\Scripts\streamlit.exe run src\x_auto\app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
```

```bash
# macOS / Linux / WSL
.venv/bin/streamlit run src/x_auto/app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
```

Open [http://localhost:8501](http://localhost:8501) in your browser. **Ctrl-C** in the
terminal to stop.

### Stop the app (without restarting the shell)

- **Foreground (boot script or `streamlit run`):** press **Ctrl-C** in
  the same PowerShell / terminal window.
- **Background (the dev loop):** if you launched Streamlit in a
  separate task and want to kill it cleanly:

  ```powershell
  Get-Process -Name 'streamlit','streamlit-run' -ErrorAction SilentlyContinue | Stop-Process -Force
  ```

  Or from any shell:

  ```bash
  pkill -f 'streamlit run'
  ```

  Port 8501 will be free again.

---

## What it does

1. **Fetch.** Read the latest tweets from a pool of X handles you
   configure (`config/accounts.yaml`).
2. **Review.** Mark the tweets you like. Promote to "Selected".
3. **Create.** Pick a selected tweet, optionally pick a project from
   `data/projects.csv` and one image, add any extra instructions, then
   click **Generate draft**. The AI rephrases the source in your voice
   and writes a short CTA reply that points at your own project link.
   The main body has no URL; the link lives in the reply, dodging X's
   $0.200 URL-surcharge. A live **📱 Preview** sits next to the editor
   and shows what the tweet will look like on X — main, reply, and
   any attached image — as you type.
4. **Publish.** Drafts sit in a 3-up grid; each has a one-click
   **Post** button and a `⋯` popover for **Schedule** (date picker),
   **Open in Create**, and **Discard**. After **Scheduled** and
   **Published** (with one-tap **Repost** and **Paraphrase & Repost**)
   comes the post log. Every card carries its own preview so you can
   see exactly what was sent.

A **Settings** expander in the sidebar (collapsed by default) holds:

- the MiniMax model picker (M3 / M2.7 / M2.7-highspeed), and
- the inline `data/projects.csv` editor (add, edit, delete, save).

### Persistence

Every piece of state lives on disk under `data/`, so a browser
refresh, an app restart, or a Ctrl-C never loses your work:

| State                             | Where                                                             |
| --------------------------------- | ----------------------------------------------------------------- |
| Generated drafts (status="draft") | `data/state.db` (`drafts` table)                              |
| Scheduled, posted                 | `data/state.db`                                                 |
| Repost / paraphrase history       | `data/state.db` (each is a new draft row)                       |
| Source tweets, accounts, projects | `data/state.db`                                                 |
| Local image cache + X`media_id` | `data/state.db` + `data/media_cache/`                         |
| OAuth tokens                      | `data/oauth_tokens.json` (mode 0600)                            |
| Scheduled jobs                    | `data/scheduler.sqlite` (APScheduler jobstore)                  |
| X API Skills                      | `data/x_skill.md`, `data/x_llms.txt`, `data/x_openapi.json` |

The Create tab writes the generated draft to the DB the moment
**Generate draft** finishes, so closing the browser mid-edit doesn't
lose it. The Publish tab's **Drafts** section lists every saved
draft; click `⋯` → **Open in Create ↗** on any card to resume editing.

---

## First-time setup (Windows PowerShell)

The boot script above handles this. The steps below are here for
reference or for doing them by hand.

### 1. X API Skills (Phase 0)

Already committed to `data/` in this repo. To refresh from upstream:

```powershell
python scripts/refresh_x_docs.py
python scripts/verify_setup.py
```

Expected output:

```
OK: X API skills installed (skill.md, llms.txt, openapi.json)
```

### 2. Create a X developer app

1. Go to [https://console.x.com](https://console.x.com) and create a new app (Web App type).
2. Set the OAuth 2.0 callback URL to `http://127.0.0.1:8765/callback`
   (or whatever you set `X_AUTH_CALLBACK_PORT` to).
3. Enable scopes: `tweet.read`, `tweet.write`, `users.read`,
   `media.write`, `offline.access`.
4. From the "Keys and Access Tokens" tab, copy the **Client ID** and
   **Client Secret**. Also copy the **Bearer Token** if available —
   it powers the read-only fetches.

### 3. Configure secrets

```powershell
Copy-Item .env.example .env
# Edit .env with your real X_BEARER_TOKEN, X_CLIENT_ID, X_CLIENT_SECRET,
# and MINIMAX_API_KEY (https://api.minimaxi.com).
```

### 4. Run the one-time OAuth flow

```powershell
python scripts/auth_setup.py
```

This opens a browser to the X consent screen, captures the redirect
on `127.0.0.1:8765`, exchanges the code for tokens, and stores them
in `data/oauth_tokens.json`. You only do this once; the app refreshes
the access token automatically afterwards.

### 5. Configure the account pool and projects

Edit `config/accounts.yaml` with the X handles to monitor. The
project list is in `data/projects.csv` and is edited from inside the
app's Settings expander — no need to touch the file by hand unless
you want to.

### 6. Launch

```powershell
.\scripts\boot.ps1
```

…or, if you'd rather:

```powershell
streamlit run src/x_auto/app.py
```

The app opens at [http://localhost:8501](http://localhost:8501). **Ctrl-C** in the terminal to
stop it.

---

## Usage

| Tab / panel                 | What to do                                                                                                                                                             |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sidebar (Settings expander) | Pick the AI model, edit the project CSV.                                                                                                                               |
| 1 · Fetch                  | Click "Fetch recent" to pull the latest tweets from your handle pool.                                                                                                  |
| 2 · Review                 | Tick the tweets you want to base a post on. Promote to "Selected".                                                                                                     |
| 3 · Create                 | Pick a selected tweet, optionally a project and one image, write any extra instructions, click Generate. Edit and Save draft.                                          |
| 4 · Publish                | Each draft in the 3-up grid has**Post** (one click) and a `⋯` popover with **Schedule** (date picker), **Open in Create**, and **Discard**. |

---

## Repo layout

```
x-automation/
├── AGENTS.md                # Engineering contract for any agent working on this repo
├── README.md                # This file
├── plan.md                  # The approved plan this repo implements
├── requirements.txt
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/                  # accounts.yaml, settings.yaml, mcp.json
├── data/                    # Runtime state (gitignored) + X API Skills + projects.csv
├── scripts/                 # boot.ps1, boot.sh, auth_setup.py, verify_setup.py, refresh_x_docs.py
├── src/x_auto/              # Source code
└── tests/                   # pytest suite
```

---

## Cost

X API is pay-per-use. The app shows running session cost in a single
line at the top of the main view. Approximate per-call cost
(USD, Aug 2026):

| Operation                               | Cost                                |
| --------------------------------------- | ----------------------------------- |
| Read a third-party tweet                | $0.005                              |
| Read a user profile                     | $0.010                              |
| Read your own data                      | $0.001                              |
| Post a plain tweet                      | $0.015                              |
| Post a tweet with a URL inline          | $0.200                              |
| Post a two-tweet thread (link in reply) | $0.030 (saves $0.170 vs inline URL) |

The app defaults to the two-tweet thread (CTA + your project URL in
the reply) to avoid the URL surcharge.

---

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

Tests use recorded HTTP fixtures and never make real network calls.
