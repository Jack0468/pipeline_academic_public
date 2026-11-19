"""Move one unit's vault folder to the standard layout (docs/VAULT_STANDARD.md), reversibly. No AI.

    python scripts/migrate_vault.py --unit ABCD1002            # dry run: lists every move and edit
    python scripts/migrate_vault.py --unit ABCD1002 --apply
    python scripts/migrate_vault.py --unit ABCD1002 --rollback # undo the last migration of this unit

What --apply does:
  1. copies the unit folder (without slides/) to state/vault_backups/;
  2. moves files; superseded versions go to _archive/ unchanged, nothing is deleted;
  3. rewrites vault links to the moved files. Obsidian only updates links for moves made inside the app;
  4. adds missing `unit`, `type` and `source` properties and repairs dead slide links in the hub;
  5. sets `vault_layout: 2` in the unit config and hides _archive/ from Obsidian's suggestions;
  6. writes _archive/migration_<date>.md (the record) and a journal for --rollback.
File modification times are kept. Links in other units' folders are never touched. Sat units are frozen (--allow-sat).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

from common import (ALLOW_SAT_HELP, CONFIG, TZ, VAULT, file_uri, find_sources, load_units, unit_link, vault_unit_dir,
                    work_units)
from paths import BACKUP_DIR, CURRENT_LAYOUT, LAYOUTS, MIGRATION_DIR, ROOT_NOTES, layout, note_type
from scaffold_vault import hub_entries

SUPERSEDED = re.compile(r"_superseded|_previous|_as_generated", re.I)
WIKILINK = re.compile(r"\[\[([^\[\]|#\n\\]+)(#[^\[\]|\n\\]*)?(\\?\|[^\[\]\n]*)?\]\]")
FRONT = re.compile(r"\A---(\r?\n)(.*?)(\r?\n)---(\r?\n)", re.S)
HUB_SLIDE = re.compile(r"\[pdf\]\(file:///([^)]+)\)")
FILE_LINK = re.compile(r"file:///([^)\s>\"]+)")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def read(p: Path) -> str:
    with open(p, encoding="utf-8", newline="") as f:      # keep each file's own line endings
        return f.read()


def write_keep_time(p: Path, text: str) -> None:
    st = p.stat()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.utime(p, (st.st_atime, st.st_mtime))


def plan_moves(u: dict, root: Path) -> tuple[dict[str, str], list[str]]:
    """({old path: new path} inside the unit folder, root files with no rule)."""
    old, new, code = LAYOUTS[1], LAYOUTS[CURRENT_LAYOUT], u["code"]
    moves = {}
    for key, rel in old.items():
        a, b = rel.format(code=code), new[key].format(code=code)
        if a != b and (root / a).is_file():
            moves[a] = b
    if (root / "worked_examples.pdf").is_file():          # the older name of the same file
        moves["worked_examples.pdf"] = new["worked_examples_pdf"]
    unplaced = []
    for p in sorted(x for x in root.iterdir() if x.is_file()):
        name = p.name
        topic_pdf = re.fullmatch(rf"{code}_(\w+?)_worked_examples\.pdf", name)
        topic_md = re.fullmatch(r"worked_examples_(\w+)\.md", name)
        if SUPERSEDED.search(p.stem):
            moves[name] = f"_archive/{name}"
        elif name in moves or name in ROOT_NOTES:
            continue
        elif topic_pdf:
            moves[name] = f"practice/worked_examples_{topic_pdf.group(1)}.pdf"
        elif topic_md:
            moves[name] = f"practice/{name}"
        else:
            unplaced.append(name)
    return moves, unplaced


def link_map(u: dict, moves: dict[str, str]) -> dict[str, str]:
    """Link target as written (vault path or bare note name) → new vault path."""
    base, out = f"units/{u['id']}", {}
    for a, b in moves.items():
        for old, new in ((a, b),) if not a.endswith(".md") else ((a[:-3], b[:-3]),):
            out[f"{base}/{old}"] = f"{base}/{new}"
            out[old] = f"{base}/{new}"                    # a bare [[name]] link
    return out


def rewrite_links(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        target = m.group(1).strip()
        new = mapping.get(target.removesuffix(".md"))
        if new is None:
            return m.group(0)
        n += 1
        return f"[[{new}{'.md' if target.endswith('.md') else ''}{m.group(2) or ''}{m.group(3) or ''}]]"
    return WIKILINK.sub(sub, text), n


def add_properties(text: str, code: str, kind: str | None) -> tuple[str, list[str]]:
    m = FRONT.match(text)
    if not m:
        return text, []
    nl, body = m.group(1), m.group(2)
    keys = set(re.findall(r"(?m)^([A-Za-z_]+):", body))
    added = []
    if "type" not in keys and kind:
        added.append("type")
        if "unit" in keys:
            body = re.sub(r"(?m)^(unit:.*?)(\r?)$", lambda x: f"{x.group(1)}{nl}type: {kind}{x.group(2)}", body, count=1)
        else:
            body = f"type: {kind}{nl}{body}"
    if "unit" not in keys:
        body = f"unit: {code}{nl}{body}"
        added.append("unit")
    if not keys & {"source", "authors"}:
        body = f"{body}{nl}source: pipeline_academic"
        added.append("source")
    if not added:
        return text, []
    return f"---{nl}{body}{m.group(3)}---{m.group(4)}{text[m.end():]}", added


def repair_file_links(text: str, current: dict[str, str]) -> tuple[str, int]:
    """file:/// links whose target moved: point them at the file of the same name the unit's sources resolve to."""
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        path = Path(urllib.parse.unquote(m.group(1)))
        if path.exists() or path.name not in current:
            return m.group(0)
        n += 1
        return current[path.name]
    return FILE_LINK.sub(sub, text), n


def repair_hub_slides(text: str, u: dict, root: Path) -> tuple[str, int]:
    """The hub's slide links point at OneDrive paths that moved; link the vault's own copy instead."""
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        path = Path(urllib.parse.unquote(m.group(1)))
        if path.exists() or not (root / "slides" / path.name).exists():
            return m.group(0)
        n += 1
        return f"[[units/{u['id']}/slides/{path.name}\\|pdf]]"
    return HUB_SLIDE.sub(sub, text), n


def extend_hub(text: str, u: dict, available: set[str]) -> tuple[str, int]:
    """Add a Working files line for each standard note the hub doesn't link yet, if everything the line links to
    exists. `available`: paths inside the unit folder after the moves, without .md."""
    nl = "\r\n" if "\r\n" in text else "\n"
    base = f"units/{u['id']}/"
    missing = []
    for _, line in hub_entries(u):
        targets = re.findall(r"\[\[([^|\]]+)", line)
        ready = all(t.removeprefix(base) in available for t in targets)
        if ready and not re.search(re.escape(targets[0]) + r"(?:\.md)?[|\]#\\]", text):
            missing.append(line)
    head = f"## Working files{nl}"
    if not missing or head not in text:
        return text, 0
    start = text.index(head) + len(head)
    nxt = re.search(r"(?m)^(?:\r?\n)*## ", text[start:])
    at = start + (nxt.start() if nxt else len(text) - start)
    return text[:at] + "".join(l + nl for l in missing) + text[at:], len(missing)


def plan_edits(u: dict, root: Path, moves: dict[str, str]) -> tuple[dict[Path, str], list[str]]:
    """{file at its CURRENT path: new text} and a summary. Nothing is written."""
    mapping, code = link_map(u, moves), u["code"]
    edits, notes = {}, []
    moved_u = dict(u, vault_layout=CURRENT_LAYOUT)
    current = {s.name: file_uri(s) for kind in ("practice", "lectures") for s in find_sources(u, kind)}
    files = [p for p in root.rglob("*.md") if not {"_archive", "slides"} & set(p.relative_to(root).parts)]
    for p in sorted(files):
        rel = p.relative_to(root).as_posix()
        final = moves.get(rel, rel)
        if final.startswith("_archive/"):
            continue
        text = new = read(p)
        new, links = rewrite_links(new, mapping)
        new, added = add_properties(new, code, note_type(final))
        extra = []
        if rel == "_unit.md":
            new, slides = repair_hub_slides(new, u, root)
            after = {moves.get(r, r) for r in (x.relative_to(root).as_posix() for x in root.rglob("*") if x.is_file())}
            new, entries = extend_hub(new, moved_u, {r.removesuffix(".md") for r in after})
            extra = [f"{slides} slide link(s) repaired", f"{entries} hub entries added"]
        new, fixed = repair_file_links(new, current)
        if fixed:
            extra.append(f"{fixed} dead file link(s) repaired")
        if new != text:
            edits[p] = new
            what = ([f"{links} link(s)"] if links else []) + ([f"added {', '.join(added)}"] if added else []) + extra
            notes.append(f"{rel}: {'; '.join(what)}")
    dash = VAULT / "00_dashboard"
    for p in sorted(dash.glob("*.md")) if dash.exists() else []:
        text = read(p)
        new, links = rewrite_links(text, {k: v for k, v in mapping.items() if k.startswith("units/")})
        if links:
            edits[p] = new
            notes.append(f"00_dashboard/{p.name}: {links} link(s)")
    return edits, notes


def set_config(u: dict, on: bool) -> None:
    p = CONFIG / "units" / f"{u['id']}.yaml"
    text = read(p)
    nl = "\r\n" if "\r\n" in text else "\n"
    text = re.sub(r"(?m)^vault_layout:.*(?:\r?\n)", "", text)
    if on:
        line = f"vault_layout: {CURRENT_LAYOUT}           # folder standard: docs/VAULT_STANDARD.md (scripts/migrate_vault.py){nl}"
        text = re.sub(r"(?m)^(session:.*\r?\n)", lambda m: m.group(1) + line, text, count=1)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def set_obsidian_filter(u: dict, on: bool) -> None:
    """Listed under Settings > Files and links > Excluded files: Obsidian ranks these down in search and suggestions."""
    p = VAULT / ".obsidian" / "app.json"
    if not p.parent.exists():
        return
    cfg = json.loads(p.read_text("utf-8")) if p.exists() else {}
    entry, filters = f"units/{u['id']}/_archive/", cfg.get("userIgnoreFilters") or []
    filters = [f for f in filters if f != entry] + ([entry] if on else [])
    cfg["userIgnoreFilters"] = filters
    if not filters:
        del cfg["userIgnoreFilters"]
    p.write_bytes(json.dumps(cfg, indent=2).encode("utf-8"))      # Obsidian's own format: LF, no final newline


def record(u: dict, moves: dict[str, str], notes: list[str], backup: Path, when: datetime) -> str:
    L = ["---", f"unit: {u['code']}", "type: migration record", "source: pipeline_academic",
         "generator: pipeline_academic/scripts/migrate_vault.py", f"date: {when:%Y-%m-%d}", "---", "",
         f"# {u['code']}: moved to the standard folder layout", "",
         f"Done {when:%Y-%m-%d %H:%M} by `migrate_vault.py`. Standard: `docs/VAULT_STANDARD.md` in pipeline_academic.",
         f"Nothing was deleted. Backup of the folder as it was: `{backup}`.", "",
         "## Moved", "| From | To |", "|---|---|"]
    L += [f"| `{a}` | `{b}` |" for a, b in sorted(moves.items())]
    L += ["", "## Edited in place (links and properties only)"] + [f"- {n}" for n in notes]
    return "\n".join(L) + "\n"


def apply(u: dict, root: Path, moves: dict[str, str], edits: dict[Path, str], notes: list[str]) -> Path:
    when = datetime.now(TZ)
    stamp = f"{when:%Y%m%d-%H%M%S}"
    backup = BACKUP_DIR / f"{u['id']}_{stamp}"
    shutil.copytree(root, backup, ignore=shutil.ignore_patterns("slides"), copy_function=shutil.copy2)
    done: list[tuple[Path, Path]] = []
    written: list[Path] = []
    originals = {p: p.read_bytes() for p in edits}
    try:
        for a, b in moves.items():
            src, dst = root / a, root / b
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                raise FileExistsError(f"{b} already exists")
            os.replace(src, dst)
            done.append((src, dst))
        here = {root / a: root / b for a, b in moves.items()}
        journal_edits = {}
        for p, text in edits.items():
            target = here.get(p, p)
            write_keep_time(target, text)
            written.append(target)
            journal_edits[target.relative_to(VAULT).as_posix()] = sha(target.read_bytes())
    except OSError as e:
        for p in written:
            original = next(k for k in edits if here.get(k, k) == p)
            with open(p, "wb") as f:
                f.write(originals[original])
        for src, dst in reversed(done):
            os.replace(dst, src)
        sys.exit(f"stopped and undone: {e}. Close anything holding these files and run it again.")
    log = root / "_archive" / f"migration_{when:%Y-%m-%d}.md"
    log.parent.mkdir(exist_ok=True)
    log.write_text(record(u, moves, notes, backup, when), "utf-8")
    for folder in ("recall", "practice", "sources", "reference", "exam_room"):
        (root / folder).mkdir(exist_ok=True)
    set_config(u, True)
    set_obsidian_filter(u, True)
    MIGRATION_DIR.mkdir(parents=True, exist_ok=True)
    journal = MIGRATION_DIR / f"{u['id']}_{stamp}.json"
    journal.write_text(json.dumps({"unit": u["code"], "id": u["id"], "backup": str(backup), "log": str(log),
                                   "moves": moves, "edited": journal_edits}, indent=2), "utf-8")
    return journal


def rollback(u: dict, force: bool) -> None:
    journals = sorted(MIGRATION_DIR.glob(f"{u['id']}_*.json"))
    if not journals:
        sys.exit(f"{u['code']}: no migration to undo")
    j = json.loads(journals[-1].read_text("utf-8"))
    root, backup = vault_unit_dir(u), Path(j["backup"])
    inside = f"units/{u['id']}/"
    changed = [rel for rel, h in j["edited"].items() if (VAULT / rel).exists() and sha((VAULT / rel).read_bytes()) != h]
    changed += [b for a, b in j["moves"].items() if inside + b not in j["edited"] and (root / b).exists()
                and sha((root / b).read_bytes()) != sha((backup / a).read_bytes())]
    if changed and not force:
        sys.exit("these files changed after the migration, so undoing it would lose your edits (--force to accept):\n  "
                 + "\n  ".join(changed))
    for a, b in j["moves"].items():
        if (root / b).exists():
            (root / a).parent.mkdir(parents=True, exist_ok=True)
            os.replace(root / b, root / a)
    inverse = {inside + b: inside + a for a, b in j["moves"].items()}
    links_back = {inside + b.removesuffix(".md"): inside + a.removesuffix(".md") for a, b in j["moves"].items()}
    for rel in j["edited"]:
        original = inverse.get(rel, rel)
        if original.startswith(inside) and (backup / original[len(inside):]).exists():
            shutil.copy2(backup / original[len(inside):], VAULT / original)
        elif (VAULT / original).exists():                 # a dashboard page: only its links changed
            write_keep_time(VAULT / original, rewrite_links(read(VAULT / original), links_back)[0])
    Path(j["log"]).unlink(missing_ok=True)
    for folder in ("_archive", "reference", "exam_room"):
        if (root / folder).is_dir() and not any((root / folder).iterdir()):
            (root / folder).rmdir()
    set_config(u, False)
    set_obsidian_filter(u, False)
    journals[-1].rename(journals[-1].with_suffix(".undone"))
    print(f"  ok {u['code']}: migration undone; the backup stays at {backup}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--force", action="store_true", help="with --rollback: undo even if files changed since")
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    a = ap.parse_args()
    units = work_units([a.unit], a.allow_sat)
    if not units:
        known = load_units([a.unit])
        sys.exit(f"{a.unit}: " + ("frozen, nothing changed" if known else "no such unit"))
    u = units[0]
    if a.rollback:
        return rollback(u, a.force)
    if layout(u) >= CURRENT_LAYOUT:
        sys.exit(f"{u['code']} is already on layout {layout(u)}")
    root = vault_unit_dir(u)
    moves, unplaced = plan_moves(u, root)
    clashes = [b for b in moves.values() if (root / b).exists()]
    edits, notes = plan_edits(u, root, moves)
    print(f"{u['code']}: {len(moves)} move(s), {len(edits)} file(s) edited, {len(unplaced)} with no rule")
    for x, y in sorted(moves.items()):
        print(f"  move {x} -> {y}")
    for n in notes:
        print(f"  edit {n}")
    for n in unplaced:
        print(f"  LEFT IN PLACE (no rule): {n}")
    if clashes:
        sys.exit("target already exists: " + ", ".join(clashes))
    if not a.apply:
        print("Dry run: nothing changed. Add --apply to do it.")
        return
    journal = apply(u, root, moves, edits, notes)
    print(f"  ok applied. Hub: {unit_link(dict(u, vault_layout=CURRENT_LAYOUT), 'hub', 'unit hub')}\n  undo: "
          f"python scripts/migrate_vault.py --unit {u['code']} --rollback  (journal {journal.name})")


if __name__ == "__main__":
    main()
