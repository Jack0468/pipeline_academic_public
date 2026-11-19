"""Refuse a commit that would publish personal data. The pre-commit hook runs it; you can run it by hand too.

    python scripts/check_personal.py --staged     the files staged for commit (what the hook checks)
    python scripts/check_personal.py              every tracked file

Patterns: generic ones (student IDs, Gmail addresses, local user folders) plus `personal_patterns` and
`publish_block_patterns` from your config/share.yaml (config.example/share.yaml shows the format). Your config lives
outside this repo or in the gitignored config/, so the patterns that name you are never committed themselves.
Prints only where a hit is (file, line, which pattern), never the matching text.
Install the hook once: python scripts/check_personal.py --install-hook
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from common import CONFIG, REPO, load_yaml

GENERIC = [r"\b5\d{8}\b", r"[\w.+-]+@gmail\.com", r"(?i)\b[a-z]:\\users\\(?!<)", r"(?i)/users/[a-z]"]
HOOK = """#!/bin/sh
# Installed by scripts/check_personal.py: refuses commits that contain personal data, and emojis.
PY="${PIPELINE_PYTHON:-$USERPROFILE/.conda/envs/pipeline_academic/python.exe}"
"$PY" scripts/check_personal.py --staged || exit 1
"$PY" scripts/check_no_emoji.py $(git diff --cached --name-only --diff-filter=ACMR) || exit 1
"""


def patterns(config: Path = CONFIG) -> list[str]:
    p = config / "share.yaml"
    own = load_yaml(p) if p.exists() else {}
    return GENERIC + [str(x) for key in ("personal_patterns", "publish_block_patterns") for x in own.get(key) or []]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout


def files(staged: bool) -> dict[str, str]:
    """{path: text} of the staged versions (or the tracked files); binary files are skipped."""
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR") if staged else git("ls-files")
    out = {}
    for rel in names.splitlines():
        try:
            data = git("show", f":{rel}") if staged else (REPO / rel).read_text("utf-8")
        except (UnicodeDecodeError, subprocess.CalledProcessError, OSError):
            continue
        out[rel] = data
    return out


def hits(texts: dict[str, str], pats: list[str]) -> list[str]:
    compiled = [(i, re.compile(p)) for i, p in enumerate(pats)]
    return [f"{rel}:{n}: pattern #{i}" for rel, text in texts.items() for n, line in enumerate(text.splitlines(), 1)
            for i, rx in compiled if rx.search(line)]


def install_hook() -> Path:
    hook = Path(git("rev-parse", "--git-path", "hooks").strip())
    hook = hook if hook.is_absolute() else REPO / hook
    hook.mkdir(parents=True, exist_ok=True)
    (hook / "pre-commit").write_text(HOOK, "utf-8", newline="\n")
    return hook / "pre-commit"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="check the staged files (the pre-commit hook)")
    ap.add_argument("--install-hook", action="store_true", help="install the pre-commit hook in this clone")
    a = ap.parse_args()
    if a.install_hook:
        print(f"  installed {install_hook()}")
        return
    pats = patterns()
    found = hits(files(a.staged), pats)
    if not (CONFIG / "share.yaml").exists():
        print("  note: no config/share.yaml, so only the generic patterns were checked")
    if found:
        print("\n".join(f"  BLOCKED {h}" for h in found))
        sys.exit(f"{len(found)} personal-data hit(s): reword those lines (patterns are numbered as in share.yaml, "
                 f"after {len(GENERIC)} generic ones)")
    print(f"  ok: no personal data ({len(pats)} pattern(s))")


if __name__ == "__main__":
    main()
