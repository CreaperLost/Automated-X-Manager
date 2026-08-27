"""Refresh the bundled X API Skills from upstream.

Re-downloads the three sources into data/, overwriting the existing
files. Safe to run any time; verify_setup.py will report the new sizes.

Usage (from the repo root):
    python scripts/refresh_x_docs.py
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

# (relative path, source URL, expected content-type prefix)
SOURCES: list[tuple[str, str, str]] = [
    ("x_skill.md", "https://docs.x.com/skill.md", "text/"),
    ("x_llms.txt", "https://docs.x.com/llms.txt", "text/"),
    ("x_openapi.json", "https://api.x.com/2/openapi.json", "application/json"),
]

USER_AGENT = "X-Automation/1.0 (+https://docs.x.com)"
TIMEOUT_SECONDS = 60


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read()


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for rel_path, url, _expected_ct_prefix in SOURCES:
        dest = DATA_DIR / rel_path
        print(f"  fetching {url}")
        try:
            data = _download(url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  [!!]  {rel_path}: {exc}")
            failures.append(rel_path)
            continue
        dest.write_bytes(data)
        print(f"  [OK]  {rel_path}: {len(data):,} bytes")
    print()
    if failures:
        print(f"FAIL: {len(failures)} file(s) failed to refresh: {failures}")
        return 1
    print("OK: X API skills refreshed. Re-run scripts/verify_setup.py to confirm.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
