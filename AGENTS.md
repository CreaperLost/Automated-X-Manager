# X-Automation — Agent Contract

This file is the engineering contract for any agent (human or AI) working
on this repo. Read it before writing or modifying code.

## Workspace boundary

**All work happens inside `C:\Users\George\Desktop\Automated-X-Manager`.**
Never read, write, import, or symlink to anything outside this folder.
No `pip install` from a sibling project, no shared venv, no shared state
file. Everything this repo needs lives under one of:

- the repo root (source, config, tests, scripts, `requirements.txt`, `pyproject.toml`)
- `data/` (gitignored runtime state, plus the committed X API Skills)
- `.venv/` (gitignored local virtual environment)

If a step seems to require leaving the folder, stop and ask the user.

## Phase 0 verification (run first)

Before any Phase 1+ work, confirm the X API Skills are installed:

```powershell
python scripts/verify_setup.py
```

This must print `OK: X API skills installed (skill.md, llms.txt, openapi.json)`
or list exactly what's missing. Expected artifacts in `data/`:

| File | Source | Purpose |
|---|---|---|
| `data/x_skill.md` | `https://docs.x.com/skill.md` | Capability summary in agentskills.io format. Injected into the AI prompt. |
| `data/x_llms.txt` | `https://docs.x.com/llms.txt` | Documentation index. Available to the AI prompt on demand. |
| `data/x_openapi.json` | `https://api.x.com/2/openapi.json` | Machine-readable spec. Reference for hand-written client. |

If the bundled files are stale, refresh them:

```powershell
python scripts/refresh_x_docs.py
```

The `config/mcp.json` file documents the XMCP and Docs MCP server
configs for the dev environment. To activate them, follow the steps in
that file's `_comment` field (or `mavis mcp create ...` — the exact
command is not the responsibility of this repo, the developer
configures their own environment).

## Engineering rules

- Smallest vertical slice that satisfies the current phase's acceptance.
- One `requirements.txt` at the repo root. One Python venv (`.venv`).
- No frameworks, services, queues, ORMs, or plugin systems added without
  a current requirement. No "just-in-case" config flags.
- The Streamlit app is the product. CLI scripts exist only to fill gaps
  Streamlit cannot serve (notably the OAuth callback).
- Real X-API verification before claiming a stage done; tests use
  recorded fixtures and never make real network calls.
- No secrets in git. `.env` is gitignored; `.env.example` is the source
  of truth for the secret shape. `data/oauth_tokens.json` is gitignored
  and created with mode 0600.
- Windows PowerShell is the user's primary shell; scripts must run
  there. WSL is a secondary supported target.

## URL cost invariant

X charges `$0.015` for a plain post and `$0.200` for a post that
contains a URL — a 13.3× gap. The app always emits a link-containing
draft as a two-post thread (main + reply) for a total of `$0.030`. The
`x/costs.py` `estimate_post_cost` and the Create tab's
`contains_url` check enforce this. The Publish tab lets the user
override per-draft, but the default is link-in-reply.

## Phases

| Phase | Goal | Acceptance |
|---|---|---|
| 0 | X API Skills installed | `verify_setup.py` OK; `data/x_*.{md, txt, json}` present |
| 1 | Skeleton + Fetch + Review | 50 tweets ingested from 3 handles, 5 selected, no writes to X |
| 2 | AI Create | a draft generated, edited, saved, no URL in main body |
| 3 | Publish now | a final draft posts as a two-tweet thread on the test timeline |
| 4 | Schedule | a scheduled draft posts at the requested time; closed-app window reaps |

Each phase ends with a manual smoke run and a green `pytest -q`.
Phase 0 ends with a green `verify_setup.py` instead.

## Files of interest

- `plan.md` — the approved plan this repo is being built from. Read
  before making structural changes.
- `README.md` — user-facing setup and usage.
- `src/x_auto/app.py` — Streamlit entrypoint.
- `src/x_auto/x/client.py` — hand-written X API client (4 endpoints).
- `src/x_auto/ai/prompts.py` — MiniMax prompt templates.
- `src/x_auto/scheduler/runner.py` — APScheduler bootstrap.
- `scripts/auth_setup.py` — one-time OAuth flow.
- `scripts/verify_setup.py` — Phase 0 verification.
- `data/x_skill.md` — injected into the AI prompt.
