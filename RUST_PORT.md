# X-Automation (Rust / Tauri)

The Rust port of X-Automation. Same workflow, same pricing rules, same
database — packaged as a native desktop app instead of a local Streamlit
server.

The Python implementation is still present in `src/x_auto/` and `tests/`
and has **not** been modified. Nothing here reads or rewrites it.

## Layout

```
Cargo.toml              workspace root
crates/x-core/          domain core — no UI, no Tauri, fully testable
  src/config.rs         settings: env + YAML + per-niche overlay
  src/store/            SQLite schema, models, repositories, winners
  src/utils/            text/URL/cashtag rules, files, media, virality
  src/x/                X API client, OAuth 2.0 PKCE, media upload, publish
  src/ai/               MiniMax client, prompts, both draft workflows
src-tauri/              Tauri shell
  src/commands.rs       the only bridge between UI and core
  src/bin/auth_setup.rs one-shot OAuth consent helper
dist/                   the frontend (plain HTML/CSS/JS, no build step)
```

## Running it

```powershell
cargo run --release            # the app
cargo run --bin auth_setup     # optional standalone X authorization helper
```

The first `cargo build --release` takes a while (Tauri's dependency tree);
subsequent builds are fast.

```powershell
cargo test -p x-core                              # 158 unit tests
cargo test -p x-core --test real_data -- --ignored # reads a COPY of data/state.db
```

## Data and secrets

Everything the Python app wrote is read by the Rust app unchanged:

| Path | Read by Rust | Notes |
|---|---|---|
| `data/state.db` | yes | same schema; schema replay is idempotent |
| `data/<niche>/state.db` | yes | per-niche databases |
| `data/oauth_tokens.json` | yes | shared by both niches; written by login and token refresh |
| `.env` | yes | never logged, never returned to the UI |
| `config/*.yaml` | yes | `settings.yaml`, `creators.yaml` |

Two deliberate departures from the Python version:

1. The dead `schedules` / `apscheduler_jobs` tables are **not** created.
   No code has referenced them since the scheduler was removed. Existing
   `.db` files still contain them and are left untouched.
2. The schema replay adds missing columns to old tables rather than
   requiring a migration step.

Your `.env` and OAuth tokens are the only readers of your credentials. No
command returns a token to the frontend, and error messages are built so
they cannot echo one.

## Rules worth preserving in any future change

These are enforced in `crates/x-core` and covered by tests. They are not
style preferences:

- **A URL never goes in the main post body.** X charges $0.200 for a post
  containing an autolinked URL versus $0.015 plain — a 13.3× difference.
  The link goes in a separate reply. `validate_post_body` rejects the
  publish before any paid call.
- **At most one cashtag per post.** X returns 403 for two or more, so the
  round-trip is wasted. `$AI` is treated as a cashtag and is forbidden
  outright in the AI niche; dollar amounts like `$25k` count too.
- **A 280-character cap**, counted the way X counts: every URL counts as
  23 characters via its t.co shortener.
- **Quotes of third-party posts are never sent to the API.** X rejects
  them with 403 unless you authored the quoted post. Sources are
  third-party by definition, so a quote id is metadata only.

## Cost model

| Operation | Cost |
|---|---|
| Read a post (third-party) | $0.005 |
| Read a profile | $0.010 |
| Create a post | $0.015 |
| Create a post with a URL inline | $0.200 |
| Delete a post | $0.010 |
| Media upload | free |

Every paid action is previewed with its cost in the UI before it runs.

## Known gaps

- The scheduler is gone in both versions; only the empty tables remained.
- Media previews in the picker are listed by path, not rendered as
  thumbnails.
- Click **Authorize X** in the app and approve access in your browser. The app
  receives the callback and saves posting credentials automatically, shared by
  both niches. The standalone `auth_setup` helper remains available as an alternative.
