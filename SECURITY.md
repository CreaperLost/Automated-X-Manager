# Security policy

## Reporting a vulnerability

Do not open a public issue containing credentials, OAuth tokens, private post
content, database files, or logs with request headers. Creator lists, project
URLs, media catalogs, and project media folders are personal local data and
must stay out of Git. Only public, credential-free examples belong in the repository.

Use the repository host's private security-advisory feature when available. If
that is not available, contact the maintainer privately before disclosing the
issue publicly.

## Sensitive local files

The following paths must remain untracked:

- `.env`
- OAuth tokens and all token backups, including `oauth_tokens.json.bak-*`
- `auth_url.txt`, private key files, and credential file backups
- `config/**/creators.yaml` and `config/**/accounts.yaml`, including backups
- Everything in `data/`, except `data/projects.example.csv`: databases, logs,
  project links, catalogs, media, and folder placeholders

Ignore rules do not remove files already tracked by Git. Untrack private files
with `git rm --cached` while preserving their local copies. Before sharing,
check both the proposed commit and the existing history for credentials.

## Local commit safeguard

Enable the repository's pre-commit check with `git config core.hooksPath .githooks`.
It requires Python and checks staged contents, including force-added ignored
files, known local credentials, and common secret formats. Run it manually with
`python scripts/check_git_secrets.py`. It reports counts without showing secret
values. Hooks are local to each checkout and can be bypassed; this is a
best-effort safeguard, so review changes before sharing them.

If any credential is committed, revoke or rotate it immediately. Removing it
from the latest commit is not sufficient; it must also be purged from Git
history before the repository is shared.
