"""Phase 0 verification: confirm the X API Skills are installed.

This is a read-only check. It does not write to the network; it only
inspects files that are expected to live under data/. It also reads
config/mcp.json to confirm the MCP server config is parseable.

Usage (from the repo root):
    python scripts/verify_setup.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"

REQUIRED_ARTIFACTS: list[tuple[str, str, int]] = [
    # (relative path, source URL, minimum size in bytes)
    ("x_skill.md", "https://docs.x.com/skill.md", 1_000),
    ("x_llms.txt", "https://docs.x.com/llms.txt", 500),
    ("x_openapi.json", "https://api.x.com/2/openapi.json", 100_000),
]


def _check_artifact(rel_path: str, source_url: str, min_size: int) -> str | None:
    target = DATA_DIR / rel_path
    if not target.exists():
        return f"missing: {target} (fetch from {source_url})"
    if not target.is_file():
        return f"not a file: {target}"
    size = target.stat().st_size
    if size < min_size:
        return f"too small: {target} is {size} bytes (expected >= {min_size})"
    # quick sanity peek
    head = target.read_text(encoding="utf-8", errors="replace")[:200].strip()
    if not head:
        return f"empty: {target}"
    return None


def _check_mcp_config() -> str | None:
    target = CONFIG_DIR / "mcp.json"
    if not target.exists():
        return f"missing: {target}"
    try:
        cfg = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"invalid JSON in {target}: {exc}"
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return f"mcp.json has no mcpServers entries: {target}"
    return None


def main() -> int:
    print("Phase 0 verification — checking X API Skills installation\n")
    problems: list[str] = []

    for rel_path, source_url, min_size in REQUIRED_ARTIFACTS:
        problem = _check_artifact(rel_path, source_url, min_size)
        if problem is None:
            target = DATA_DIR / rel_path
            print(f"  [OK]  {rel_path:<20}  {target.stat().st_size:>10,} bytes")
        else:
            print(f"  [!!]  {rel_path:<20}  {problem}")
            problems.append(problem)

    problem = _check_mcp_config()
    if problem is None:
        cfg = json.loads((CONFIG_DIR / "mcp.json").read_text(encoding="utf-8"))
        names = ", ".join(sorted(cfg["mcpServers"].keys()))
        print(f"  [OK]  config/mcp.json      {len(cfg['mcpServers'])} server(s): {names}")
    else:
        print(f"  [!!]  config/mcp.json      {problem}")
        problems.append(problem)

    print()
    if problems:
        print("FAIL: Phase 0 incomplete. Fix the items above, then re-run.")
        print("  - To fetch the data files:  python scripts/refresh_x_docs.py")
        print("  - To edit mcp.json:         open config/mcp.json in your editor")
        return 1
    print("OK: X API skills installed (skill.md, llms.txt, openapi.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
