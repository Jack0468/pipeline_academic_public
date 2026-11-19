"""Guard for the no-emoji rule: find (and with --fix, replace) emojis in the project, the vault and
any other folders given.

    python scripts/check_no_emoji.py                  # this repo + the vault; exit 1 if any are found
    python scripts/check_no_emoji.py --fix            # replace with words (common.EMOJI_WORDS), drop the rest
    python scripts/check_no_emoji.py --fix PATH ...   # other folders (e.g. my_ai_skills)

Text files only (.py .md .ps1 .yaml .yml .txt .json .tex .css .cfg .toml .example). Skips .git, state/,
out/, slides/, .obsidian/ and caches. refresh.ps1 runs it in check mode, as a warning.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import EMOJI, REPO, VAULT, no_emoji

EXT = {".py", ".md", ".ps1", ".yaml", ".yml", ".txt", ".json", ".tex", ".css", ".cfg", ".toml", ".example", ".sh"}
SKIP = {".git", "state", "out", "slides", "_archive", ".obsidian", "__pycache__", "node_modules", ".trash"}


def files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and (p.suffix in EXT or p.name == ".env.example") and not SKIP & set(p.relative_to(root).parts):
            yield p


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--fix", action="store_true")
    a = ap.parse_args()
    found = 0
    for root in a.paths or [REPO, VAULT]:
        for p in files(root):
            try:
                t = p.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits = EMOJI.findall(t)
            if not hits:
                continue
            found += len(hits)
            if a.fix:
                p.write_text(no_emoji(t), "utf-8")
            print(f"  {'fixed' if a.fix else 'found'} {len(hits):4d}  {p}")
    print(f"{found} emoji(s) {'replaced' if a.fix else 'found'}" + ("" if a.fix or not found else ": run with --fix"))
    sys.exit(1 if found and not a.fix else 0)


if __name__ == "__main__":
    main()
