"""Put a unit's tutorials in the vault, each next to your own working for it. Zero AI calls; refresh.ps1 runs it.

  1. copies the tutorial sheets (`sources.tutorials`, else the `sources.practice` files with "tutorial" in the name)
     and the handwritten PDFs your tutorial working is in (e.g. the OneNote "tutorial" section export) into
     <vault>/units/<id>/tutorials/. Only new or changed files are copied; the folder is gitignored and safe to
     delete. OneDrive and OneNote stay the source of truth.
  2. copies the course's tutorial solutions (`sources.tutorial_solutions`; common.tutorial_solutions keeps only files
     that hold answers and finds the page they start on) into the same folder.
  3. writes units/<id>/practice/tutorials.md: per tutorial, the sheet, every page of your handwritten working for it
     (each linked to that page of the PDF and to its transcription), then the solutions, linked to the page where
     the answers start and listed last: attempt first, then check.

Which pages belong to which tutorial comes from the transcriptions (sources/handwritten_*.md, extract_handwriting.py):
a page heading that names a tutorial ("Week 3 Tutorial ...", "Tutorial 9 question 2 ...") sets it. In a transcription
of tutorial notes, an unlabelled page belongs to the tutorial before it; those pages are marked "(follows)" because
the assignment is inferred. Re-run after a new transcription (nightly_ai.ps1 does).

tutorials.md is generated. If it was edited since the last run, the edited copy is moved to _archive/ first.

Usage:
    python scripts/link_tutorials.py [--unit ABCD1001]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

from common import (
    ALLOW_SAT_HELP,
    TZ,
    VAULT,
    Manifest,
    find_sources,
    no_emoji,
    prune_cache,
    tutorial_solutions,
    unit_path,
    work_units,
)

TUT_FILE = re.compile(r"(?i)tut(?:orial)?\D{0,3}(\d{1,2})(?!\d)")
TUT_HEADING = re.compile(r"(?i)\bweek\s*(\d{1,2})\s*tutorial\b|\btut(?:orial)?\s*(\d{1,2})(?!\d)")
PAGE_HEADING = re.compile(r"(?m)^### p\. (\d+) · (.+?)\s*$")
ORIGINAL = re.compile(r'(?m)^original:\s*"?file:///([^"\n]+)"?')
GITIGNORE_LINE = "units/*/tutorials/"


def copy_in(src: Path, dst_dir: Path) -> Path:
    dst = dst_dir / src.name
    if not dst.exists() or dst.stat().st_size != src.stat().st_size or dst.stat().st_mtime < src.stat().st_mtime:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dst


def tutorial_sheets(u: dict, solutions: set[Path] = frozenset()) -> dict[int, list[Path]]:
    """{tutorial number: sheet PDFs}. A sheet without a number is skipped (it can't be matched to notes). Files that
    hold solutions are listed as solutions instead, even when the same glob finds them (a Canvas folder has both)."""
    files = find_sources(u, "tutorials") if (u.get("sources") or {}).get("tutorials") else \
        [p for p in find_sources(u, "practice") if re.search(r"(?i)tut", p.name)]
    out: dict[int, list[Path]] = {}
    for p in files:
        if p in solutions:
            continue
        m = TUT_FILE.search(p.stem)
        if m:
            out.setdefault(int(m.group(1)), []).append(p)
    return out


def solutions_by_tutorial(u: dict) -> dict[int, list[tuple[Path, int]]]:
    """{tutorial number: [(solutions PDF, first answer page)]}; a file without a number is skipped."""
    out: dict[int, list[tuple[Path, int]]] = {}
    for p, start in tutorial_solutions(u):
        m = TUT_FILE.search(p.stem)
        if m:
            out.setdefault(int(m.group(1)), []).append((p, start))
    return out


def tutorial_of(title: str) -> int | None:
    m = TUT_HEADING.search(title)
    return int(m.group(1) or m.group(2)) if m else None


def notes_pages(u: dict) -> tuple[dict[int, list[tuple]], list[Path]]:
    """({tutorial number: [(transcription, source PDF, page, title, inferred)]}, source PDFs used)."""
    pdfs = {p.name: p for p in find_sources(u, "handwritten")}
    out: dict[int, list[tuple]] = {}
    used: list[Path] = []
    for tr in sorted(unit_path(u, "sources").glob("handwritten_*.md")):
        md = tr.read_text("utf-8")
        m = ORIGINAL.search(md)
        name = Path(urllib.parse.unquote(m.group(1))).name if m else tr.stem.removeprefix("handwritten_") + ".pdf"
        pdf = pdfs.get(name)                                     # by name: the original path may be an old checkout
        tutorial_notes = bool(re.search(r"(?i)tut", name))       # a whole notebook section of tutorial working
        current, hits = None, 0
        for pm in PAGE_HEADING.finditer(md):
            page, title = int(pm.group(1)), pm.group(2)
            n = tutorial_of(title)
            if n is not None:
                current = n
            elif not tutorial_notes or current is None:
                continue
            out.setdefault(current, []).append((tr, pdf, page, title, n is None))
            hits += 1
        if hits and pdf and pdf not in used:
            used.append(pdf)
    return out, used


def render(u: dict, sheets: dict[int, list[Path]], pages: dict[int, list[tuple]], copies: dict[Path, Path],
           sols: dict[int, list[tuple[Path, int]]] | None = None) -> str:
    vrel = lambda p: p.relative_to(VAULT).as_posix()            # noqa: E731
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    sols = sols or {}
    nums = sorted(set(sheets) | set(pages) | set(sols))
    lines = ["---", f"unit: {u['code']}", "type: tutorial index", "source: generated",
             "generated_by: link_tutorials.py", f"generated: {today}", "---", "", f"# {u['code']} tutorials", "",
             "Each tutorial sheet with the pages of your own handwritten working for it. Page links open the PDF at "
             "that page; the transcription link has the typed version. Generated by `scripts/link_tutorials.py` "
             "(edits are moved to `_archive/` on the next run).", "",
             "Pages marked *(follows)* have no tutorial named on them: they are filed under the tutorial on the page "
             "before, so check them.", ""]
    if sols:
        lines += ["Solutions are the course's own (`sources.tutorial_solutions`), listed last on purpose: attempt the "
                  "questions first, then open them at the page where the answers start.", ""]
    for n in nums:
        lines.append(f"## Tutorial {n}")
        for s in sheets.get(n, []):
            lines.append(f"- Sheet: [[{vrel(copies[s])}|{s.name}]]")
        if not sheets.get(n):
            lines.append("- Sheet: none found in the unit's sources" + ("; the questions open the solutions file below"
                                                                        if sols.get(n) else ""))
        mine = pages.get(n, [])
        if mine:
            trs = list(dict.fromkeys(tr for tr, *_ in mine))
            links = ", ".join(f"[[{vrel(tr).removesuffix('.md')}|{tr.stem.removeprefix('handwritten_')}]]"
                              for tr in trs)
            lines.append(f"- Your working ({len(mine)} page(s); typed version: {links}):")
            for _tr, pdf, page, title, inferred in mine:
                where = f"[[{vrel(copies[pdf])}#page={page}|p. {page}]]" if pdf in copies else f"p. {page}"
                lines.append(f"    - {where} {title}{' *(follows)*' if inferred else ''}")
        else:
            lines.append("- Your working: no pages found in your handwritten notes")
        for p, start in sols.get(n, []):
            lines.append(f"- Solutions (after your attempt): [[{vrel(copies[p])}#page={start}|{p.name}, answers from p. {start}]]")
        lines.append("")
    return no_emoji("\n".join(lines))


def write_index(u: dict, text: str, manifest: Manifest) -> str:
    dst = unit_path(u, "practice") / "tutorials.md"
    key = f"{u['id']}/tutorials.md"
    if dst.exists():
        old = dst.read_text("utf-8")
        if old == text:
            return "unchanged"
        recorded = manifest.data.get(key, {}).get("hash")
        if recorded != hashlib.sha256(old.encode()).hexdigest()[:16]:        # edited by hand (or not ours): keep it
            arch = unit_path(u, "archive")
            arch.mkdir(parents=True, exist_ok=True)
            moved = arch / f"tutorials_{datetime.now(TZ):%Y-%m-%d_%H%M}_previous.md"
            dst.replace(moved)
            print(f"  {u['code']}: practice/tutorials.md was edited since the last run; kept as _archive/{moved.name}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, "utf-8")
    manifest.mark(key, hashlib.sha256(text.encode()).hexdigest()[:16])
    return "written"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    args = ap.parse_args()
    gi = VAULT / ".gitignore"
    if gi.exists() and GITIGNORE_LINE not in gi.read_text("utf-8"):
        gi.write_text(gi.read_text("utf-8").rstrip() + f"\n{GITIGNORE_LINE}\n", "utf-8")
    manifest = Manifest("tutorials")
    for u in work_units(args.unit, args.allow_sat):
        sols = solutions_by_tutorial(u)
        sol_pdfs = [p for ps in sols.values() for p, _ in ps]
        sheets = tutorial_sheets(u, set(sol_pdfs))
        pages, notes_pdfs = notes_pages(u)
        if not sheets and not pages and not sols:
            continue
        dst_dir = unit_path(u, "tutorials")
        copies = {p: copy_in(p, dst_dir) for p in [s for ss in sheets.values() for s in ss] + notes_pdfs + sol_pdfs}
        pruned = prune_cache(dst_dir, set(copies.values()))
        status = write_index(u, render(u, sheets, pages, copies, sols), manifest)
        n_pages = sum(len(v) for v in pages.values())
        inferred = sum(1 for v in pages.values() for x in v if x[4])
        n_sheets = sum(len(v) for v in sheets.values())
        print(f"  {u['code']}: {n_sheets} tutorial sheet(s), {len(sol_pdfs)} solutions PDF(s) and {len(notes_pdfs)} "
              f"notes PDF(s) in vault{f' ({len(pruned)} stale copies removed)' if pruned else ''}; "
              f"{n_pages} page(s) of your working matched ({inferred} inferred); practice/tutorials.md {status}")


if __name__ == "__main__":
    main()
