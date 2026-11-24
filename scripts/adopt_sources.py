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
          (--map), resolved with --by-content, or the lecture is regenerated.
  RENUMBER (--by-content) the recall file built from these bytes takes this label. All renumbers happen at once
          (L5->L4 while L6->L5), with every mention relabelled: CODE_L5 links, (L5 p.N) refs, hub and progress rows.
  DISPLACED (--by-content) a recall file whose label is taken and whose own source has no counterpart: _archive/,
          its unticked progress row dropped, its hub row marked "(archived)".
Recall files no current lecture maps to are listed as ORPHAN and left alone.

Page numbers are not renumbered. If a new file inserts or removes slides, references after that point are off: the
dry run prints both page counts, so check the pairs that differ (or regenerate that lecture with gen_recall.py --force).

--supersede GLOB...: moves old copies (globs relative to the unit's OneDrive folder) into
<unit folder>/_superseded/<same path>, one line each in _superseded/MOVED.md. A file moves only if the unit's sources
now hold identical bytes, or a file with the same lecture or tutorial number, of the same kind (question sheet for a
question sheet, solutions for solutions: a course often gives a sheet and its solutions one name) and at least 90% the
same text; and it is not itself a source. Anything else is listed and stays, including scans with no text (your own
notes). Nothing is deleted. Every change is logged in state/logs/adopt_sources_<unit>_<time>.md.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import progress
from common import (
    ALLOW_SAT_HELP,
    STATE,
    TZ,
    Manifest,
    doc_profile,
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


def plan(u: dict, maps: dict[str, list[str]], by_content: bool = False) -> list[dict]:
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
            if by_content:                                       # the file built from these bytes takes the label
                row.update(action="RENUMBER", owner=owner, source=rec_dir / f"{u['code']}_{owner}.md")
            else:
                row.update(action="CONFLICT", owner=owner)       # same bytes as another lecture's recall source
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
    moving = {r["owner"] for r in rows if r["action"] == "RENUMBER"}
    for r in [x for x in rows if x["action"] == "RENUMBER"]:   # its label's own file isn't moving: it is displaced
        if r["recall"].exists() and r["label"] not in moving:
            rows.append({"action": "DISPLACED", "recall": r["recall"], "label": r["label"], "by": r["owner"],
                         "dups": []})
    claimed = {r["recall"] for r in rows if r["action"] != "RENUMBER"} | {r.get("source") for r in rows} \
        | {d for r in rows for d in r["dups"]}
    for p in sorted(rec_dir.glob(f"{u['code']}_*.md")):
        if p not in claimed:
            rows.append({"action": "ORPHAN", "recall": p, "label": p.stem.removeprefix(f"{u['code']}_"), "dups": []})
    return rows


LABEL_CELLS = ("(?m)^(- \\[[ xX]\\] ){old}(?= · \\[\\[{code}_{old}\\|)",   # progress rows: this unit's only (one
                                                                         # progress.md holds every unit's lectures)
               "(?m)^(\\| ){old}(?= \\|)",              # hub table rows
               "(?m)^(lecture: ){old}(?=\\s*$)",        # a recall file's front matter
               "(?m)^(# {code} ){old}(?=:)")            # its title


def renumber_text(text: str, code: str, mapping: dict[str, str]) -> str:
    """Relabel lectures all at once ({'L5': 'L4', 'L6': 'L5'} can't chain): CODE_L5 links, '(L5 p.N)' references,
    and the label cells of progress rows, hub rows and recall files. Other text ('L5' in a sentence) is left alone."""
    new = text
    for i, old in enumerate(mapping):
        o, ph = re.escape(old), f"\x00{i}\x00"
        for pat in LABEL_CELLS:                            # first: the progress pattern looks for the CODE_old link
            new = re.sub(pat.replace("{code}", re.escape(code)).replace("{old}", o), rf"\g<1>{ph}", new)
        new = re.sub(rf"(?<![A-Za-z0-9]){re.escape(code)}_{o}(?![A-Za-z0-9])", f"{code}_{ph}", new)
        new = re.sub(rf"(?<![A-Za-z0-9]){o}(?= pp?\.\s?\d)", ph, new)
    for i, target in enumerate(mapping.values()):
        new = new.replace(f"\x00{i}\x00", target)
    return new


def renumber(u: dict, rows: list[dict], log: list[str]) -> None:
    """Move recall files to the labels their content has under the new names, all at once (L5->L4 while L6->L5),
    and relabel every mention: file links (CODE_L5), slide refs (L5 p.N), hub and progress label cells. A recall file
    whose label is taken and that is not moving itself is DISPLACED: moved to _archive/, its progress row dropped if
    unticked."""
    code = u["code"]
    ren = [r for r in rows if r["action"] == "RENUMBER"]
    if not ren:
        return
    arch = unit_path(u, "archive")
    targets = [progress.PAGE] if progress.PAGE.exists() else []
    notes = unit_notes(u) + targets
    for r in [x for x in rows if x["action"] == "DISPLACED"]:
        arch.mkdir(parents=True, exist_ok=True)
        dst = arch / f"{r['recall'].stem}_displaced_by_{r['by']}.md"
        r["recall"].replace(dst)
        log.append(f"- {r['recall'].name}: its label now belongs to {r['by']}'s content; moved to _archive/{dst.name}")
        for n in notes:                                    # its own rows and links: point at the archive, or drop
            if not n.exists():                             # a displaced recall file itself, just moved
                continue
            t = n.read_text("utf-8")
            lab, c = re.escape(r["label"]), re.escape(code)
            new = re.sub(rf"(?m)^- \[ \] {lab} · \[\[{c}_{lab}\|recall\]\]\n", "", t)      # unticked: nothing lost
            new = re.sub(rf"(?m)^(\| ){lab}(?= \|)", rf"\g<1>{r['label']} (archived)", new)
            new = re.sub(rf"recall/{c}_{lab}(?![A-Za-z0-9])", f"_archive/{dst.stem}", new)
            if new != t:
                n.write_text(new, "utf-8")
                log.append(f"  - {n.name}: rows for the displaced {r['label']} archived or dropped")
    for i, r in enumerate(ren):                            # two steps, so a chain or a swap can't collide
        r["tmp"] = r["source"].with_name(f"__renumber_{i}.md")
        r["source"].replace(r["tmp"])
    for r in ren:
        r["tmp"].replace(r["recall"])
        log.append(f"- renumbered {r['source'].name} -> {r['recall'].name} (its content is {r['pdf'].name})")
    mapping = {r["owner"]: r["label"] for r in ren}
    for n in unit_notes(u) + targets:                      # re-listed: the recall files have their new names now
        t = n.read_text("utf-8")
        new = renumber_text(t, code, mapping)
        if new != t:
            n.write_text(log_edit(new, f"{datetime.now(TZ):%Y-%m-%d} adopt_sources.py: lecture labels renumbered to "
                                       "the source's numbering"), "utf-8")
            log.append(f"  - {n.name}: labels renumbered")
    for r in ren:
        r["action"] = "ADOPT"                              # the rest is an ordinary adoption


def apply(u: dict, rows: list[dict], log: list[str]) -> None:
    renumber(u, rows, log)
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


SAME_TEXT = 0.9          # question or slide text at least this similar counts as the same document (a re-upload)


def profile(p: Path) -> tuple[str, str]:
    """(kind, text) of a document: common.doc_profile, the one rule for sheets vs solutions."""
    return doc_profile(p)


def best_match(old: tuple[str, str], candidates: dict[Path, tuple[str, str]]) -> tuple[Path | None, float]:
    """The candidate of the same kind whose text is most similar (questions for questions, solutions for solutions:
    a course often reuses one file name for a sheet and its solutions)."""
    import difflib
    kind, text = old
    best, score = None, 0.0
    if not text:
        return None, 0.0
    for p, (k, t) in candidates.items():
        if k != kind or not t:
            continue
        r = difflib.SequenceMatcher(None, text[:12000], t[:12000]).ratio()
        if r > score:
            best, score = p, r
    return best, score


def supersede(u: dict, globs: list[str], do: bool, log: list[str]) -> None:
    import glob as globlib
    base = unit_dir(u)
    current = {p.resolve() for k in SOURCE_KINDS for p in find_sources(u, k)}
    hashes = {file_hash(p): p for p in current}
    lec_nums: dict[int, list[Path]] = {}
    for p in find_sources(u, "lectures"):
        if lecture_number(p) is not None:
            lec_nums.setdefault(lecture_number(p), []).append(p)
    tut_nums: dict[int, list[Path]] = {}
    for k in ("practice", "tutorials", "tutorial_solutions"):
        for p in find_sources(u, k):
            m = TUT_FILE.search(p.stem)
            if m and p not in tut_nums.get(int(m.group(1)), []):
                tut_nums.setdefault(int(m.group(1)), []).append(p)
    profiles: dict[Path, tuple[str, str]] = {}
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
            if h in hashes:
                why, repl = "identical", hashes[h]
            else:                                  # same number AND same kind AND the same text, or it stays
                m = TUT_FILE.search(p.stem)
                pool = (tut_nums.get(int(m.group(1)), []) if m
                        else lec_nums.get(lecture_number(p), []) if re.search(r"(?i)lecture", p.name) else [])
                if p.suffix.lower() != ".pdf" or not pool:
                    print(f"    keep {rel.as_posix()}: nothing in the sources replaces it")
                    continue
                for c in pool:
                    profiles.setdefault(c, profile(c))
                mine = profile(p)
                repl, score = best_match(mine, {c: profiles[c] for c in pool})
                if not mine[1]:
                    print(f"    keep {rel.as_posix()}: no text to compare (a scan or your own notes?)")
                    continue
                if not repl or score < SAME_TEXT:
                    print(f"    keep {rel.as_posix()}: no {mine[0]} in the sources with the same text "
                          f"(best {score:.2f}); not a copy")
                    continue
                why = f"same {'tutorial' if m else 'lecture'} {mine[0]}, {score:.0%} the same text"
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
    ap.add_argument("--by-content", action="store_true",
                    help="resolve CONFLICTs by content: each recall file takes the label its slides have under the new "
                         "names (renumbered all at once); a recall file whose label is taken is moved to _archive/")
    a = ap.parse_args()
    maps: dict[str, list[str]] = {}
    for m in a.map:
        old, _, new = m.partition("=")
        maps.setdefault(new.strip(), []).append(old.strip())
    for u in work_units(a.unit, a.allow_sat):
        rows = plan(u, maps, a.by_content)
        print(f"  {u['code']}: {'applying' if a.apply else 'dry run'}")
        for r in rows:
            if r["action"] == "ORPHAN":
                print(f"    ORPHAN {r['recall'].name}: no current lecture maps to it (left alone)")
                continue
            if r["action"] == "DISPLACED":
                print(f"    DISPLACED {r['recall'].name}: {r['by']}'s content takes {r['label']}; this file goes to "
                      "_archive/ (its source has no counterpart in the new files)")
                continue
            old = r.get("old_name")
            pp = f"{pages(r['pdf'])}p" + (f", was {old}" if old and old != r["pdf"].name else "")
            extra = f" <- {r['source'].name}" if r["action"] in ("RENAME", "RENUMBER") else ""
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
