"""Make every slide-page reference clickable in Obsidian: "(p. 23)" → opens the lecture PDF at page 23.

Recall files and recall checks cite slide pages ("Method: p. 23", "(pp. 17, 19)", "(pp. 22–23)"). Those
are PDF page numbers (the viewer's page, not the number printed on the slide). Obsidian can only open
PDFs that are inside the vault, so this:
  1. copies each unit's lecture PDFs into <vault>/units/<id>/slides/ (only new or changed files;
     the folder is gitignored and safe to delete; OneDrive stays the source of truth);
  2. rewrites page references in units/<id>/recall/*.md and recall_checks/*.md as
     [[units/<id>/slides/<file>.pdf#page=N|p. N]]. It's idempotent: already-linked references are left alone.
The file's `slides:` frontmatter says which PDF its pages refer to. Zero AI calls; refresh.ps1 runs it.

Usage:
    python scripts/link_slides.py [--unit ABCD1001]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

from common import unit_path, ALLOW_SAT_HELP, work_units
from common import VAULT, find_sources, load_units, vault_unit_dir

PAGE = re.compile(r"(?<![|\w#=])(pp?)\.\s?(\d{1,3})((?:\s?(?:,|–|-|and)\s?\d{1,3})*)(?![\w\]])")


def sync_slides(u) -> dict[str, Path]:
    """Copy lecture PDFs into the vault; returns {source filename: vault copy}."""
    dst_dir = unit_path(u, "slides")
    out = {}
    for src in find_sources(u, "lectures"):
        dst = dst_dir / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size or dst.stat().st_mtime < src.stat().st_mtime:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        out[src.name] = dst
    return out


def slide_of(md: str) -> str | None:
    m = re.search(r'^slides:\s*"?file:///([^"\n]+)"?', md, re.M)
    return Path(urllib.parse.unquote(m.group(1))).name if m else None


def link(md: str, target: str) -> str:
    """Rewrite page refs outside the frontmatter and outside existing wikilinks."""
    head, sep, body = md.partition("\n---\n") if md.startswith("---") else ("", "", md)

    def one(n: str, label: str) -> str:
        return f"[[{target}#page={n}|{label}]]"

    def sub(m: re.Match) -> str:
        kind, first, rest = m.group(1), m.group(2), m.group(3)
        if not rest:
            return one(first, f"{kind}. {first}")
        parts = re.split(r"(\s?(?:,|–|-|and)\s?)", rest)                 # keep separators
        out = one(first, f"{kind}. {first}")
        for i in range(1, len(parts), 2):
            out += parts[i] + one(parts[i + 1], parts[i + 1])
        return out

    chunks = re.split(r"(\[\[[^\]]*\]\])", body)                          # don't touch existing links
    body = "".join(c if c.startswith("[[") else PAGE.sub(sub, c) for c in chunks)
    return head + sep + body


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    args = ap.parse_args()
    gi = VAULT / ".gitignore"
    if gi.exists() and "units/*/slides/" not in gi.read_text("utf-8"):
        gi.write_text(gi.read_text("utf-8").rstrip() + "\nunits/*/slides/\n", "utf-8")
    for u in work_units(args.unit, args.allow_sat):
        copies = sync_slides(u)
        changed = 0
        for folder in ("recall", "recall_checks", "practice_checks"):
            for p in sorted((vault_unit_dir(u) / folder).glob("*.md")):
                md = p.read_text("utf-8")
                name = slide_of(md)
                if not name or name not in copies:
                    continue
                target = copies[name].relative_to(VAULT).as_posix()
                new = link(md, target)
                if new != md:
                    p.write_text(new, "utf-8")
                    changed += 1
        print(f"  {u['code']}: {len(copies)} slide PDFs in vault, {changed} file(s) linked")


if __name__ == "__main__":
    main()
