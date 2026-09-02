# Security policy

## Reporting a vulnerability

Do not open a public issue containing credentials, OAuth tokens, private post
content, database files, or logs with request headers. Handles and project URLs
configured in this repository are intentionally tracked, so only add values
that are safe to share with everyone who can access the repository.

Use the repository host's private security-advisory feature when available. If
that is not available, contact the maintainer privately before disclosing the
issue publicly.

## Sensitive local files

The following paths must remain untracked:

- `.env`
- `data/oauth_tokens.json`
- `data/*.db` and `data/*.sqlite`
- files stored inside `data/media_cache/` (only folder placeholders are tracked)


If any credential is committed, revoke or rotate it immediately. Removing it
from the latest commit is not sufficient; it must also be purged from Git
history before the repository is shared.
