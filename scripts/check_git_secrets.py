"""Check staged Git blobs without printing credential values.

This is a best-effort local safeguard, not a guarantee against every secret format.
"""
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]


def git(*args, data=None, check=True):
    return subprocess.run(['git', *args], cwd=ROOT, input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def local_secrets():
    values = set()
    env = ROOT / '.env'
    if env.is_file():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            name, value = line.split('=', 1)
            if any(word in name.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CLIENT_ID')):
                value = value.strip().strip('\"\'')
                if len(value) >= 12:
                    values.add(value)
    for path in (ROOT / 'data').rglob('oauth_tokens*'):
        if not path.is_file():
            continue
        try:
            bundle = json.loads(path.read_text(encoding='utf-8-sig')).get('x_user', {})
            for name in ('access_token', 'refresh_token', 'bearer_token'):
                value = bundle.get(name, '')
                if isinstance(value, str) and len(value) >= 12:
                    values.add(value)
        except (ValueError, OSError, AttributeError):
            continue
    return {version.encode() for value in values
            for version in (value, urllib.parse.quote(value, safe=''), json.dumps(value)[1:-1])}


PATTERNS = [
    re.compile(rb'sk-[A-Za-z0-9_-]{20,}'),
    re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(rb'AKIA[A-Z0-9]{16}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|bearer[_-]?token|password)\s*[\"\']?\s*[:=]\s*[\"\']([^\"\'\s]{12,})'),
]


def suspicious(data, needles):
    if any(needle in data for needle in needles):
        return True
    for pattern in PATTERNS:
        for match in pattern.finditer(data):
            value = match.group(1) if match.lastindex else match.group(0)
            if not any(word in value.lower() for word in (b'test', b'example', b'placeholder', b'dummy', b'fake')):
                return True
    return False


def main():
    if sys.argv[1:]:
        raise SystemExit('Usage: python scripts/check_git_secrets.py')
    paths = [path for path in git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z').stdout.split(b'\0') if path]
    if not paths:
        print('Secret check passed: no added or modified staged files.')
        return 0
    ignored = set(git('check-ignore', '--no-index', '--stdin', '-z', data=b'\0'.join(paths)+b'\0', check=False).stdout.split(b'\0')) - {b''}
    needles = local_secrets()
    blocked = 0
    for path in paths:
        if path in ignored:
            blocked += 1
            continue
        # Scan the actual staged blob, even when the working file differs.
        blob = git('show', ':' + path.decode('utf-8', 'surrogateescape')).stdout
        if suspicious(blob, needles):
            blocked += 1
    if blocked:
        print(f'Commit blocked: {blocked} staged file(s) are ignored or contain detectable credentials. Unstage private files or remove secrets. No credential values are shown.', file=sys.stderr)
        return 1
    print(f'Secret check passed: {len(paths)} staged file(s) scanned.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
