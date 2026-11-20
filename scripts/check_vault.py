"""Check units on the standard layout against docs/VAULT_STANDARD.md. Read-only, no AI.

    python scripts/check_vault.py                  # every unit on layout 2
    python scripts/check_vault.py --unit ABCD1002
    python scripts/check_vault.py --brief          # problems only

FAIL: something is in the wrong place, a required property is missing, or a vault link points at nothing.
WARN: worth a look (dead file:/// links, identical copies). Exit code 1 if anything FAILs. _archive/ and slides/ are
not checked: archived files stay exactly as they were.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

from common import VAULT, file_hash, load_units, vault_unit_dir
from paths import PDF_FOLDERS, ROOT_NOTES, UNIT_FOLDERS, layout

SUPERSEDED = re.compile(r"_superseded|_previous|_as_generated", re.I)
WIKILINK = re.compile(r"\[\[([^\[\]|#\n\\]+)(?:#[^\[\]|\n\\]*)?(?:\\?\|[^\[\]\n]*)?\]\]")
FILE_LINK = re.compile(r"file:///([^)\s>\"]+)")
FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


def live_files(root: Path) -> list[Path]:
    skip = {"_archive", "slides", "tutorials"}                  # slides/, tutorials/: copies of OneDrive PDFs
    return sorted(p for p in root.rglob("*") if p.is_file() and not skip & set(p.relative_to(root).parts))


def check_place(root: Path) -> list[str]:
    bad = []
    for p in sorted(root.iterdir()):
        if p.is_dir():
            if p.name not in UNIT_FOLDERS and not p.name.startswith("EXAM_"):
                bad.append(f"unexpected folder {p.name}/")
        elif p.name not in ROOT_NOTES:
            bad.append(f"{p.name} is loose in the unit folder (only {', '.join(sorted(ROOT_NOTES))} belong there)")
    for p in live_files(root):
        rel = p.relative_to(root)
        if SUPERSEDED.search(p.stem):
            bad.append(f"{rel.as_posix()} is a superseded version: move it to _archive/")
        if p.suffix.lower() == ".pdf" and rel.parts[0] not in PDF_FOLDERS and not rel.parts[0].startswith("EXAM_"):
            bad.append(f"{rel.as_posix()}: PDFs belong in {', '.join(sorted(PDF_FOLDERS))}")
        if " " in p.name and not p.name.startswith("handwritten_"):       # transcriptions mirror their source PDF's name
            bad.append(f"{rel.as_posix()}: no spaces in file names")
    return bad


def check_properties(root: Path) -> list[str]:
    bad = []
    for p in live_files(root):
        if p.suffix != ".md":
            continue
        rel = p.relative_to(root).as_posix()
        m = FRONT.match(p.read_text("utf-8"))
        keys = set(re.findall(r"(?m)^([A-Za-z_]+):", m.group(1))) if m else set()
        missing = {"unit", "type"} - keys
        if not keys & {"source", "authors"}:
            missing.add("source or authors")
        if missing:
            bad.append(f"{rel}: missing {', '.join(sorted(missing))}")
    return bad


def check_links(root: Path) -> tuple[list[str], list[str]]:
    """(broken vault links, warnings). The hub may link standard notes that haven't been generated yet: a warning."""
    names = {p.stem for p in VAULT.rglob("*.md") if ".obsidian" not in p.parts}
    broken, pending, dead = [], [], defaultdict(int)
    for p in live_files(root):
        if p.suffix != ".md":
            continue
        rel = p.relative_to(root).as_posix()
        found = pending if rel == "_unit.md" else broken
        text = p.read_text("utf-8")
        for target in WIKILINK.findall(text):
            target = target.strip()
            if "/" not in target:
                if target.removesuffix(".md") not in names and re.fullmatch(r"[A-Za-z_][\w .-]*", target):
                    found.append(f"{rel}: [[{target}]] matches no note")
                continue
            exists = (VAULT / target).exists() or (VAULT / f"{target}.md").exists()
            if not exists and target.startswith(("units/", "00_dashboard/")):
                found.append(f"{rel}: [[{target}]] points at nothing")
        for uri in set(FILE_LINK.findall(text)):
            if not Path(urllib.parse.unquote(uri)).exists():
                dead[rel] += 1
    return sorted(set(broken)), sorted(set(pending)) + [f"{k}: {n} dead file:/// link(s)" for k, n in sorted(dead.items())]


def check_copies(root: Path) -> list[str]:
    seen = defaultdict(list)
    for p in live_files(root):
        seen[file_hash(p)].append(p.relative_to(root).as_posix())
    return [f"identical files: {', '.join(v)}" for v in seen.values() if len(v) > 1]


def check_unit(u: dict) -> tuple[list[str], list[str]]:
    root = vault_unit_dir(u)
    broken, dead = check_links(root)
    return check_place(root) + check_properties(root) + broken, dead + check_copies(root)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--brief", action="store_true", help="only print problems")
    a = ap.parse_args()
    units = [u for u in load_units(a.unit) if a.unit or layout(u) >= 2]
    failed = False
    for u in units:
        if layout(u) < 2:
            print(f"  {u['code']}: on layout 1, not checked (scripts/migrate_vault.py moves a unit to the standard)")
            continue
        fails, warns = check_unit(u)
        failed |= bool(fails)
        if fails or warns or not a.brief:
            print(f"  {'FAIL' if fails else 'ok'} {u['code']}: {len(fails)} problem(s), {len(warns)} warning(s)")
        for m in fails:
            print(f"    FAIL {m}")
        for m in warns:
            print(f"    WARN {m}")
    if not units and not a.brief:
        print("  no unit is on the standard layout yet")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
