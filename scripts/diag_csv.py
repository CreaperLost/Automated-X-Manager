"""Quick sanity check: print the loaded projects from data/projects.csv."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from x_auto.config import get_settings
from x_auto.ai.projects import load_csv

s = get_settings()
rows = load_csv(s.data_dir / "projects.csv")
print(f"CSV row count: {len(rows)}")
for r in rows:
    name = r["name"]
    url = r["url"]
    print(f"  {name:14s}  {url}")
