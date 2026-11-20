"""Move a unit's lectures to new source files (usually the Canvas mirror) and keep its recall files. 0 AI calls.

    python scripts/adopt_sources.py --unit ABCD1001                     # dry run: what would happen
    python scripts/adopt_sources.py --unit ABCD1001 --apply [--map L6a=L6 --map L6b=L6]
                                    [--supersede "*Lecture*.pdf" "tutorial/*.pdf"]

Run it after pointing the unit config's `sources.lectures` (and the tutorial globs) at the new files. Then run
link_slides.py and link_tutorials.py (refresh.ps1 does), which rebuild the vault copies from the new sources.

For each lecture the config now finds, with label L (common.lecture_labels):
  ADOPT   recall/<CODE>_L.md exists but was built from another file: this file is recorded as its source in the
          recall manifest (so gen_recall.py and nightly_ai.ps1 don't regenerate it), its `slides:` points here,
          and links to the old vault slide copy in the unit's notes are rewritten to the new copy.
  RENAME  no recall file for L, but --map OLD=L names one: it is renamed to L first, and "(OLD p.N)" references
          in the unit's notes become "(L p.N)". A second OLD mapped to the same L is a duplicate: moved to _archive/.
  NEW     no recall file: gen_recall.py makes one on its next run (1 AI call).
  RELINK  same bytes as the recall file's source, in a new place: only the links change.
  ok      already built from this file.
  CONFLICT this file has the same bytes as the file a DIFFERENT recall file was built from: the new names number the
          lectures differently from the old copies. --apply refuses to change anything until each conflict is mapped
          (--map) or the lecture is regenerated.
Recall files no current lecture maps to are listed as ORPHAN and left alone.

Page numbers are not renumbered. If a new file inserts or removes slides, references after that point are off: the
dry run prints both page counts, so check the pairs that differ (or regenerate that lecture with gen_recall.py --force).

--supersede GLOB...: moves old copies (globs relative to the unit's OneDrive folder) into
<unit folder>/_superseded/<same path>, one line each in _superseded/MOVED.md. A file moves only if the unit's sources
now hold identical bytes or the same lecture or tutorial number, and it is not itself a source; anything else is
listed and stays. Nothing is deleted. Every change is logged in state/logs/adopt_sources_<unit>_<time>.md.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

from common import (
    ALLOW_SAT_HELP,
    STATE,
    TZ,
    Manifest,
    file_hash,
    file_uri,
    find_sources,
    lecture_labels,
    lecture_number,
    unit_dir,
    unit_path,
    vault_unit_dir,
    work_units,
)
from link_tutorials import TUT_FILE

SLIDES = re.compile(r'(?m)^slides:\s*"?file:///([^"\n]+)"?\s*$')
SOURCE_KINDS = ("lectures", "practice", "tutorials", "tutorial_solutions", "scope", "reference", "own_work",
                "handwritten")


def pages(p: Path) -> int | None:
    import pymupdf
    try:
        with pymupdf.open(str(p)) as d:
            return d.page_count
    except Exception:
        return None


def old_source(md: str) -> str | None:
    m = SLIDES.search(md)
    return Path(urllib.parse.unquote(m.group(1))).name if m else None


def old_uri(md: str) -> str | None:
    m = SLIDES.search(md)
    return "file:///" + m.group(1) if m else None


def replace_in_notes(u: dict, old: str, new: str, log: list[str], what: str) -> None:
    """Exact text replacement in every note of the unit (links are written by common.file_uri, so they match)."""
    for n in unit_notes(u):
        t = n.read_text("utf-8")
        if old in t:
            n.write_text(t.replace(old, new), "utf-8")
            log.append(f"  - {what} in {n.relative_to(vault_unit_dir(u)).as_posix()}")


def unit_notes(u: dict) -> list[Path]:
    """Every note of the unit except archived ones and machine transcriptions."""
    root = vault_unit_dir(u)
    return [p for p in root.rglob("*.md") if "_archive" not in p.relative_to(root).parts
            and not p.name.startswith("handwritten_")]


def relabel(text: str, old: str, new: str) -> str:
    """'(L6a p.20)' -> '(L6 p.20)', 'L6a p.36' -> 'L6 p.36'; leaves 'L6ab', 'XL6a' and words alone."""
    return re.sub(rf"(?<![A-Za-z0-9]){re.escape(old)}(?= pp?\.\s?\d)", new, text)


def log_edit(text: str, line: str) -> str:
    """Add a line to a note's front-matter `edits:` list, if it keeps one (handbook, worked examples, cheat sheet)."""
    entry = f'  - "{line}"\n'
    if re.search(r"(?m)^edits: \[\]\s*$", text):
        return re.sub(r"(?m)^edits: \[\]\s*$", "edits:\n" + entry.rstrip("\n"), text, count=1)
    return re.sub(r"(?m)^(edits:\n(?:  - .*\n)*)", lambda m: m.group(1) + entry, text, count=1)


def built_from(u: dict) -> dict[str, str]:
    """{hash of the file a recall file was built from: its label}, for the source files that still exist (in place
    or already moved to _superseded/). Used to catch a new file whose bytes belong to a different recall file."""
    out = {}
    base = unit_dir(u)
    for p in unit_path(u, "recall").glob(f"{u['code']}_*.md"):
        m = SLIDES.search(p.read_text("utf-8"))
        if not m:
            continue
        src = Path(urllib.parse.unquote(m.group(1)))
        if not src.exists() and src.is_relative_to(base):
            src = base / "_superseded" / src.relative_to(base)
        if src.exists():
            out.setdefault(file_hash(src), p.stem.removeprefix(f"{u['code']}_"))
    return out


def plan(u: dict, maps: dict[str, list[str]]) -> list[dict]:
    rec_dir = unit_path(u, "recall")
    manifest = Manifest("recall")
    lectures = find_sources(u, "lectures")
    labels = lecture_labels(lectures)
    origin = built_from(u)
    rows = []
    for pdf in lectures:
        lab = labels[pdf]
        out = rec_dir / f"{u['code']}_{lab}.md"
        row = {"pdf": pdf, "label": lab, "recall": out, "dups": []}
        owner = origin.get(file_hash(pdf))
        if owner and owner != lab and owner not in maps.get(lab, []):
            row.update(action="CONFLICT", owner=owner)           # same bytes as another lecture's recall source
        elif not out.exists():
            olds = [o for o in maps.get(lab, []) if (rec_dir / f"{u['code']}_{o}.md").exists()]
            if olds:
                row.update(action="RENAME", source=rec_dir / f"{u['code']}_{olds[0]}.md", olds=maps[lab],
                           dups=[rec_dir / f"{u['code']}_{o}.md" for o in olds[1:]])
            else:
                row["action"] = "NEW"
        elif manifest.done(f"{u['code']}:{pdf.name}", file_hash(pdf)):
            row["action"] = "ok"
        else:
            row["action"] = "ADOPT"
        src_md = row.get("source", out)
        md = src_md.read_text("utf-8") if src_md.exists() else ""
        row["old_name"], row["old_uri"] = old_source(md), old_uri(md)
        if row["action"] == "ok" and row["old_uri"] and row["old_uri"] != file_uri(pdf):
            row["action"] = "RELINK"            # same bytes, new place: only the links change
        rows.append(row)
    claimed = {r["recall"] for r in rows} | {r.get("source") for r in rows} | {d for r in rows for d in r["dups"]}
    for p in sorted(rec_dir.glob(f"{u['code']}_*.md")):
        if p not in claimed:
            rows.append({"action": "ORPHAN", "recall": p, "label": p.stem.removeprefix(f"{u['code']}_"), "dups": []})
    return rows


def apply(u: dict, rows: list[dict], log: list[str]) -> None:
    manifest = Manifest("recall")
    notes = unit_notes(u)
    stamp = f"{datetime.now(TZ):%Y-%m-%d} adopt_sources.py"
    arch = unit_path(u, "archive")
    for r in rows:
        if r["action"] not in ("ADOPT", "RENAME", "RELINK"):
            continue
        pdf, out = r["pdf"], r["recall"]
        if r["action"] == "RENAME":
            r["source"].replace(out)
            log.append(f"- renamed {r['source'].name} -> {out.name}")
            for d in r["dups"]:
                arch.mkdir(parents=True, exist_ok=True)
                d.replace(arch / f"{d.stem}_duplicate_of_{r['label']}.md")
                log.append(f"- {d.name}: a duplicate of the same lecture, moved to _archive/")
            notes = unit_notes(u)
            for n in notes:
                t = n.read_text("utf-8")
                new = t
                for old in r["olds"]:
                    new = relabel(new, old, r["label"])
                if new != t:
                    n.write_text(log_edit(new, f"{stamp}: slide references {'/'.join(r['olds'])} relabelled "
                                               f"{r['label']} (one lecture file on the new source)"), "utf-8")
                    log.append(f"- {n.relative_to(vault_unit_dir(u)).as_posix()}: references "
                               f"{'/'.join(r['olds'])} -> {r['label']}")
        if r.get("old_uri") and r["old_uri"] != file_uri(pdf):     # slides: front matter and every link to the old file
            replace_in_notes(u, r["old_uri"], file_uri(pdf), log, f"links to {r['old_name']} -> {pdf.name}")
        md = out.read_text("utf-8")
        if not SLIDES.search(md):
            log.append(f"  - {out.name} has no slides: line; left as it is")
        if r["action"] != "RELINK":
            built = r.get("old_name") or "an older copy"
            manifest.mark(f"{u['code']}:{pdf.name}", file_hash(pdf), out=str(out),
                          model=f"adopted {datetime.now(TZ):%Y-%m-%d} (built from {built})")
        log.append(f"- {out.name}: source is now {pdf.name} (was {r.get('old_name')}; {r['action']})")
        old = r.get("old_name")
        if old and old != pdf.name:                                  # links to the vault copy (slides/<file>.pdf)
            replace_in_notes(u, f"/slides/{old}", f"/slides/{pdf.name}", log, f"slides/{old} -> slides/{pdf.name}")


def supersede(u: dict, globs: list[str], do: bool, log: list[str]) -> None:
    import glob as globlib
    base = unit_dir(u)
    current = {p.resolve() for k in SOURCE_KINDS for p in find_sources(u, k)}
    hashes = {file_hash(p): p for p in current}
    lec_nums = {lecture_number(p): p for p in find_sources(u, "lectures") if lecture_number(p) is not None}
    tut_nums = {}
    for k in ("practice", "tutorials", "tutorial_solutions"):
        for p in find_sources(u, k):
            m = TUT_FILE.search(p.stem)
            if m:
                tut_nums.setdefault(int(m.group(1)), p)
    moved_log = base / "_superseded" / "MOVED.md"
    for pat in globs:
        for s in sorted(globlib.glob(str(base / pat), recursive=True)):
            p = Path(s)
            rel = p.relative_to(base)
            if not p.is_file() or rel.parts[0] in ("canvas", "_superseded") or p.resolve() in current:
                if p.is_file() and p.resolve() in current:
                    print(f"    keep {rel.as_posix()}: still a source")
                continue
            h = file_hash(p)
            m = TUT_FILE.search(p.stem)
            by = (hashes.get(h) and ("identical", hashes[h])) \
                or (re.search(r"(?i)lecture", p.name) and lecture_number(p) in lec_nums
                    and ("same lecture", lec_nums[lecture_number(p)])) \
                or (m and int(m.group(1)) in tut_nums and ("same tutorial", tut_nums[int(m.group(1))]))
            if not by:
                print(f"    keep {rel.as_posix()}: nothing in the sources replaces it")
                continue
            why, repl = by
            if not do:
                print(f"    would move {rel.as_posix()} -> _superseded/ ({why}: {repl.relative_to(base).as_posix()})")
            else:
                dst = base / "_superseded" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(p), str(dst))
                except PermissionError:              # open in a PDF viewer or OneDrive: leave it, undo the half-move
                    if p.exists() and dst.exists() and file_hash(dst) == h:
                        dst.unlink()
                    print(f"    keep {rel.as_posix()}: open in another program; close it and run again")
                    continue
                print(f"    moved {rel.as_posix()} -> _superseded/ ({why}: {repl.relative_to(base).as_posix()})")
                new = not moved_log.exists()
                with open(moved_log, "a", encoding="utf-8") as f:
                    if new:
                        f.write("# Superseded copies\n\nMoved here by scripts/adopt_sources.py: the Canvas mirror "
                                "(canvas/) is the source now. Nothing was deleted.\n\n| When | File | Replaced by |\n"
                                "|---|---|---|\n")
                    f.write(f"| {datetime.now(TZ):%Y-%m-%d %H:%M} | {rel.as_posix()} | "
                            f"{repl.relative_to(base).as_posix()} ({why}) |\n")
                log.append(f"- moved {rel.as_posix()} -> _superseded/ ({why}: {repl.relative_to(base).as_posix()})")
                replace_in_notes(u, file_uri(p), file_uri(dst), log,
                                 f"links to {rel.as_posix()} follow it to _superseded/")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="+", required=True)
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    ap.add_argument("--map", action="append", default=[], metavar="OLD=NEW",
                    help="recall label OLD becomes NEW (e.g. L6a=L6 when two copies of one lecture became one)")
    ap.add_argument("--supersede", nargs="*", default=[], metavar="GLOB",
                    help="old copies to move into _superseded/ (relative to the unit's OneDrive folder)")
    a = ap.parse_args()
    maps: dict[str, list[str]] = {}
    for m in a.map:
        old, _, new = m.partition("=")
        maps.setdefault(new.strip(), []).append(old.strip())
    for u in work_units(a.unit, a.allow_sat):
        rows = plan(u, maps)
        print(f"  {u['code']}: {'applying' if a.apply else 'dry run'}")
        for r in rows:
            if r["action"] == "ORPHAN":
                print(f"    ORPHAN {r['recall'].name}: no current lecture maps to it (left alone)")
                continue
            old = r.get("old_name")
            pp = f"{pages(r['pdf'])}p" + (f", was {old}" if old and old != r["pdf"].name else "")
            extra = f" <- {r['source'].name}" if r["action"] == "RENAME" else ""
            extra += "".join(f"; {d.name} -> _archive/" for d in r["dups"])
            if r["action"] == "CONFLICT":
                extra = (f": identical to the file recall {r['owner']} was built from. The new names number the "
                         f"lectures differently; map it (--map {r['owner']}={r['label']}) or regenerate")
            print(f"    {r['action']:6} {r['label']:28} {r['pdf'].name} ({pp}){extra}")
        conflicts = [r for r in rows if r["action"] == "CONFLICT"]
        if conflicts and a.apply:
            print(f"  {u['code']}: NOTHING CHANGED: {len(conflicts)} conflict(s) above. Adopting by label would attach "
                  "recall files to the wrong slides.")
            continue
        log: list[str] = []
        try:
            if a.apply:
                apply(u, rows, log)
            if a.supersede:
                supersede(u, a.supersede, a.apply, log)
        finally:                                     # whatever happened is on record, even after an error
            write_log(u, log)


def write_log(u: dict, log: list[str]) -> None:
    if not log:
        return
    out = STATE / "logs" / f"adopt_sources_{u['id']}_{datetime.now(TZ):%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# adopt_sources.py, {u['code']}, {datetime.now(TZ):%Y-%m-%d %H:%M}\n\n" + "\n".join(log) + "\n",
                   "utf-8")
    print(f"  {u['code']}: {len(log)} change(s), logged in {out.relative_to(STATE.parent).as_posix()}")


if __name__ == "__main__":
    main()
