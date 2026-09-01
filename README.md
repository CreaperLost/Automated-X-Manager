# X-Automation

X-Automation is a local Streamlit application for researching posts on X,
turning selected sources into original drafts, and publishing them with
explicit control over every paid action.

The application runs on your computer. API credentials, OAuth tokens, fetched
posts, drafts, and uploaded media remain in local ignored files.

## Workflow

The interface has three views:

1. **Sources** — manually fetch recent posts from handles you choose, or reuse
   sources already stored locally.
2. **Create** — select one source, choose **Rephrase** or **Original take**,
   generate one draft, edit it, and optionally attach a source or personal
   image.
3. **Queue** — manage drafts, published posts, failures, and post history.

Fetching and AI generation only happen after you click their buttons. The app
does not automatically fetch sources or generate drafts.

## Important cost behavior

X API reads, writes, and AI generation may cost money depending on your
accounts and plans. The app shows estimates before relevant actions, but
provider pricing can change and the provider's invoice is authoritative.

When a draft contains a project URL, X-Automation places the URL in a separate
reply by default. This preserves the main post's readability and follows the
app's built-in URL-cost protection.

## Requirements

- Python 3.11 or newer
- An X developer application with OAuth 2.0 enabled
- X API credentials with read and write access
- A MiniMax API key
- Windows PowerShell, or Bash on macOS/Linux/WSL

Register this OAuth callback URL in the X developer console:

```text
http://127.0.0.1:8765/callback
```

The app requests these OAuth scopes:

```text
tweet.read tweet.write users.read media.write offline.access
```

## Quick start

Clone the repository, then run the appropriate bootstrap script.

### Windows PowerShell

```powershell
.\scripts\boot.ps1
```

### macOS, Linux, or WSL

```bash
chmod +x scripts/boot.sh scripts/run.sh
./scripts/boot.sh
```

On the first run, the script creates `.venv` and `.env`. Add your credentials
to `.env` and run the bootstrap script again:

```dotenv
X_BEARER_TOKEN=
X_CLIENT_ID=
X_CLIENT_SECRET=
X_AUTH_CALLBACK_PORT=8765
MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimax.io/v1
```

The OAuth consent page opens in your browser. After authorization, the app is
available at [http://localhost:8501](http://localhost:8501).

Never commit `.env` or `data/oauth_tokens.json`.

## First-time configuration

Use the sidebar inside the app:

- **Handles** — add or remove X usernames, then click **Save**. Handles are
  used only when you manually click **Fetch recent**.
- **Projects** — add project names and URLs. During generation, the second AI
  step selects a relevant project and creates a short CTA reply containing its
  URL.
- **Model** — select the available MiniMax model.

Personal handle and project files are intentionally excluded from Git:

- `config/accounts.yaml`
- `data/projects.csv`

Public examples are provided as `config/accounts.example.yaml` and
`data/projects.example.csv`.

## Daily use

### One-click start and stop in Codex

The checked-in Codex local environment adds **Run** and **Stop** actions to the
desktop app's top bar. **Run** starts X-Automation in the background and waits
for its health check; **Stop** ends only the server started by that action.

Run the bootstrap once before using the actions:

```bash
bash scripts/boot.sh
```

You can use the same commands outside Codex:

```bash
bash scripts/start.sh
bash scripts/stop.sh
```

Runtime output is written to `data/x-automation.log`.

1. Open **Sources**.
2. Reuse a saved source or click **Fetch recent**.
3. Select a source and open **Create**.
4. Choose **Rephrase** or **Original take**.
5. Optionally select a source image or one of your own images.
6. Click **Generate draft**.
7. Edit and review the post.
8. Choose **Post now**.

Source posts are inspiration only. The app does not quote third-party posts
when publishing.

## Local data

These files are created locally and ignored by Git:

| Path | Purpose |
| --- | --- |
| `.env` | API credentials |
| `config/accounts.yaml` | Monitored handles |
| `data/projects.csv` | Personal projects and URLs |
| `data/oauth_tokens.json` | X OAuth access and refresh tokens |
| `data/state.db` | Sources, drafts, post history, and application state |
| `data/media_cache/` | Image and video library, organized into one folder per project |

Back up the `data/` directory if you need to preserve drafts and media.
Do not publish that backup.

## Running manually

After setup, Windows users can run:

```powershell
.\.venv\Scripts\python.exe -m streamlit run src\x_auto\app.py
```

macOS/Linux users can run:

```bash
.venv/bin/python -m streamlit run src/x_auto/app.py
```

Stop the foreground server with `Ctrl+C`. Windows users can also run:

```powershell
.\scripts\stop.ps1
```

## OAuth troubleshooting

If X rejects a token refresh, reauthorize the app:

```powershell
.\.venv\Scripts\python.exe scripts\auth_setup.py
```

Or on macOS/Linux:

```bash
.venv/bin/python scripts/auth_setup.py
```

Complete the browser consent flow, then retry the failed post. Confirm that the
callback URL and OAuth scopes in the X developer console exactly match the
values above.

If Streamlit reports that `server.port` cannot be used in development mode,
launch through the supplied bootstrap script. The repository's
`.streamlit/config.toml` disables Streamlit development mode for local use.

## Development

Install dependencies and run the checks:

```powershell
.\.venv\Scripts\python.exe -m ruff check . --no-cache
.\.venv\Scripts\python.exe -m pytest
```

Tests use mocked HTTP responses and do not make real X API requests.

## Security

Before sharing logs or reporting a problem, remove credentials, OAuth tokens,
post contents, referral URLs, and personal account information. See
[SECURITY.md](SECURITY.md) for reporting guidance.

## Disclaimer

This is an independent project and is not affiliated with X or MiniMax. You are
responsible for API usage, provider charges, content review, account compliance,
and the final publishing time.
