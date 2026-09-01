# Security policy

## Reporting a vulnerability

Do not open a public issue containing credentials, OAuth tokens, private post
content, personal handles, referral URLs, database files, or logs with request
headers.

Use the repository host's private security-advisory feature when available. If
that is not available, contact the maintainer privately before disclosing the
issue publicly.

## Sensitive local files

The following paths must remain untracked:

- `.env`
- `config/accounts.yaml`
- `data/projects.csv`
- `data/oauth_tokens.json`
- `data/*.db` and `data/*.sqlite`
- `data/media_cache/`
- `data/scheduler.lock`

If any credential is committed, revoke or rotate it immediately. Removing it
from the latest commit is not sufficient; it must also be purged from Git
history before the repository is shared.
