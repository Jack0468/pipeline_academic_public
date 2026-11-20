"""Notes sheet ("cheat sheet") for a restricted exam: N x A4, double-sided. Any unit, only on request.

    python scripts/build_cheatsheet.py --unit ABCD1003 --draft   # one AI call -> the unit's cheatsheet.md
    python scripts/build_cheatsheet.py --unit ABCD1003 --pdf     # cheatsheet.md -> cheatsheet.pdf (no AI)

The cheat-sheet mode of docs/HANDBOOK_DESIGN.md. The page budget comes from the unit config's exam.notes_sheet
("1 x A4 double-sided" = 2 sides). --draft condenses, in one call:
  - your own notes_sheet.md draft (kept: it outranks everything);
  - the unit's handbook.md (build_handbook.py --draft first): the content source;
  - worked_examples.md, if there is one: "Recognise it" and the method per question type;
  - your error log and check misconceptions -> "Trap (you)" lines (relabelled if they name no source);
  - exam.scope and the exam intel note: depth and order.
It writes a method chooser ("the question says X -> method, section n") and one compact block per problem type. The
draft is then YOURS: --draft never overwrites cheatsheet.md (it writes cheatsheet_draft_<date>.md beside it).

--pdf sets whatever cheatsheet.md says now in three columns, trying font sizes from 7 pt down to 5.5 pt and keeping
the largest that fits the budget. It FAILS (exit 1, no PDF written) if even 5.5 pt runs over: cut lines and re-run.
No index, no figures, no edge tabs: every millimetre is content. Sat units are frozen (--allow-sat); --unit is required.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import gemini
import pdf_tools
from build_handbook import check_own_traps, math_outside
from common import ALLOW_SAT_HELP, PROMPTS, TZ, fill, intel_block, md_section, no_emoji, unit_path, work_units
from pace_recall import pace

WORDS_PER_SIDE = 1800          # three columns at 6.5 pt with inline maths (measured on the first sheet, 2026-11-20)
SIZES = (7.0, 6.5, 6.0, 5.5)   # pt, tried largest first; below 5.5 pt a printed sheet stops being readable


def sides(u) -> int:
    """Printed sides allowed: 'N x A4' double-sided -> 2N (single-sided if the config says so)."""
    sheet = str(u["exam"].get("notes_sheet", "")).lower()
    m = re.search(r"(\d+)\s*x\s*a4", sheet)
    if not m:
        sys.exit(f"{u['code']}: exam.notes_sheet is '{sheet}': the cheat sheet needs 'N x A4' (an open-book exam "
                 "gets the handbook, build_handbook.py)")
    return int(m.group(1)) * (1 if "single" in sheet else 2)


def inputs(u) -> list[str]:
    texts = []
    own = unit_path(u, "notes_sheet")
    if own.exists():
        own_md = re.sub(r"\A---\n.*?\n---\n", "", own.read_text("utf-8"), flags=re.S)
        texts.append("STUDENT'S OWN NOTES SHEET DRAFT:\n\n" + own_md)
    hb = unit_path(u, "handbook")
    if not hb.exists():
        sys.exit(f"{u['code']}: {hb} not found: draft the handbook first "
                 f"(build_handbook.py --unit {u['code']} --draft)")
    md = re.sub(r"(?ms)^## (Index phrases|Lookup|To add|To do)\b.*?(?=^## |\Z)", "", hb.read_text("utf-8"))
    texts.append("HANDBOOK:\n\n" + md)
    we = unit_path(u, "worked_examples")
    if we.exists():
        keep = []
        for m in re.finditer(r"^## (WE\d+\..*)$(.*?)(?=^## |\Z)", we.read_text("utf-8"), re.M | re.S):
            body = m.group(2)
            rec = re.search(r"^\*\*Recognise it:\*\*.*$", body, re.M)
            meth = re.search(r"^\*\*Method:\*\*.*?(?=^\*\*Worked example)", body, re.M | re.S)
            keep.append(f"### {m.group(1)}\n{rec.group(0) if rec else ''}\n{meth.group(0).strip() if meth else ''}")
        texts.append("WORKED EXAMPLES (recognition and method only):\n\n" + "\n\n".join(keep))
    errs = []
    log = unit_path(u, "error_log")
    if log.exists():
        errs.append("ERROR LOG:\n" + re.sub(r"<!--.*?-->", "", log.read_text("utf-8")))
    for p in sorted(unit_path(u, "recall_checks").glob("*.md")):
        errs.append(f"RECALL CHECK {p.stem} misconceptions:\n"
                    + md_section(p.read_text("utf-8"), "Misconceptions to fix"))
    for p in sorted(unit_path(u, "practice_checks").glob("*.md")):
        errs.append(f"PRACTICE CHECK {p.stem} habits:\n" + md_section(p.read_text("utf-8"), "Habits to fix"))
    texts.append("STUDENT ERRORS:\n\n" + ("\n\n".join(errs) or "(none recorded)"))
    scope = " ".join(str(u["exam"].get("scope") or "").split())
    if scope:
        texts.append("EXAM SCOPE (config):\n\n" + scope)
    intel = intel_block(u, "handbook")
    if intel:
        texts.append(intel)
    return texts


def draft(u) -> Path:
    n = sides(u)
    ex = u["exam"]
    prompt = fill((PROMPTS / "cheatsheet.md").read_text("utf-8"), unit_code=u["code"], unit_name=u["name"],
                  conditions=ex.get("conditions", ""), materials="; ".join(ex.get("materials") or []),
                  format=" ".join(str(ex.get("format") or "not published").split()), pace=pace(u)[1],
                  sheets=(n + 1) // 2, sides=n, words=n * WORDS_PER_SIDE)
    texts = inputs(u)
    print(f"  {u['code']}: drafting the notes sheet ({n} sides, about {n * WORDS_PER_SIDE} words; "
          f"{len(texts)} source blocks)", flush=True)
    body = no_emoji(gemini.generate([], prompt, texts=texts, temperature=0.2)).strip()
    body = re.sub(r"\A```(?:markdown)?\n|\n```\Z", "", body)
    body, relabelled = check_own_traps(body)
    if relabelled:
        print(f"  {relabelled} 'Trap (you)' line(s) named no source in your record: relabelled or dropped", flush=True)
    out = unit_path(u, "cheatsheet")
    if out.exists():
        out = out.with_name(f"cheatsheet_draft_{datetime.now(TZ):%Y-%m-%d}.md")
    hb = unit_path(u, "handbook")
    front = ["---", f"unit: {u['code']}", "type: cheat sheet", "source: cheatsheet", "share: never",
             f"drafted: {datetime.now(TZ):%Y-%m-%d}", f"model: {gemini.used_model()}",
             f"from: \"{hb.name} ({datetime.fromtimestamp(hb.stat().st_mtime, TZ):%Y-%m-%d %H:%M})\"",
             f"budget: {n} sides",
             "owner: you (the pipeline never overwrites this file)", "edits: []", "---", ""]
    out.write_text("\n".join(front) + body + "\n", "utf-8")
    words = len(re.findall(r"[A-Za-z]{2,}", re.sub(r"\$[^$]*\$", " ", body)))
    print(f"  {u['code']}: {words} words outside maths (budget about {n * WORDS_PER_SIDE})")
    return out


TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}")


def prepare(md: str) -> str:
    """cheatsheet.md -> pandoc Markdown for print: no front matter, tables become lines (tables can't sit in
    columns), display maths becomes inline, and each bold label starts its own paragraph."""
    md = re.sub(r"\A---\n.*?\n---\n", "", md, flags=re.S)
    md = re.sub(r"\[\[[^|\]\n]*\|([^\]\n]*)\]\]", r"\1", md)
    md = re.sub(r"\[\[([^\]\n]*)\]\]", r"\1", md)
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    md = re.sub(r"^> \[!(\w+)\][+-]? ?", lambda m: f"> **{m.group(1).capitalize()}:** ", md, flags=re.M)
    out = []
    for ln in md.splitlines():
        if ln.startswith("|"):
            if TABLE_SEP.match(ln.strip("| ")) or TABLE_SEP.match(ln):
                continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))]
            ln = "- " + " · ".join(c for c in cells if c)
        ln = re.sub(r"\$\$(.+?)\$\$", r"$\1$", ln)
        if ln.startswith("**") and out and out[-1].strip() and not out[-1].startswith("#"):
            out.append("")
        out.append(ln)
    return no_emoji("\n".join(out))


TEX = r"""\documentclass{article}
\usepackage[a4paper,margin=5mm,top=5mm,bottom=7mm,footskip=3mm]{geometry}
\usepackage{fontspec}\setmainfont{Calibri}
\usepackage{amsmath,amssymb}
\usepackage{multicol}
\usepackage{enumitem}\setlist{nosep,leftmargin=0.9em,label=-}
\usepackage{titlesec}
\usepackage{fancyhdr}\usepackage{lastpage}
\usepackage{xcolor}
\usepackage[hidelinks]{hyperref}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\pagestyle{fancy}\fancyhf{}\renewcommand{\headrulewidth}{0pt}
\fancyfoot[C]{\fontsize{5}{5}\selectfont __TITLE__ \quad side \thepage\ of \pageref{LastPage}}
\titleformat{\section}{\fontsize{__H1__}{__H1__}\selectfont\bfseries}{}{0pt}{}
\titleformat{\subsection}{\fontsize{__SIZE__}{__LEAD__}\selectfont\bfseries\color{black!80!blue}}{}{0pt}{}
\titlespacing*{\section}{0pt}{0pt}{1pt}
\titlespacing*{\subsection}{0pt}{2.5pt}{0.5pt}
\setlength{\parindent}{0pt}\setlength{\parskip}{0.6pt}
\setlength{\columnsep}{2.5mm}\setlength{\columnseprule}{0.2pt}
\begin{document}
\fontsize{__SIZE__}{__LEAD__}\selectfont\sloppy
\begin{multicols*}{3}
__BODY__
\end{multicols*}
\end{document}
"""


def render(tmp: Path, body_tex: str, title: str, size: float) -> tuple[Path, int]:
    import pymupdf
    tex = (TEX.replace("__BODY__", body_tex).replace("__TITLE__", title)
           .replace("__SIZE__", f"{size}pt").replace("__LEAD__", f"{size * 1.15:.2f}pt")
           .replace("__H1__", f"{size + 1.5}pt"))
    name = f"sheet_{str(size).replace('.', '_')}"
    (tmp / f"{name}.tex").write_text(tex, "utf-8")
    for _ in range(2):                                   # the second pass settles "side N of M"
        r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"], cwd=tmp,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode:
            sys.exit("xelatex failed:\n" + r.stdout[-2500:])
    pdf = tmp / f"{name}.pdf"
    with pymupdf.open(str(pdf)) as d:
        return pdf, d.page_count


def pdf(u) -> Path:
    src = unit_path(u, "cheatsheet")
    if not src.exists():
        sys.exit(f"{src} not found: run --draft first")
    n = sides(u)
    md = prepare(src.read_text("utf-8"))
    broken = math_outside(md)
    if broken:
        print(f"  {u['code']}: FIX FIRST, LaTeX outside $...$ in {src.name} (the build would fail):")
        for x in broken[:15]:
            print(f"    {x}")
        raise SystemExit(1)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "s.md").write_text(md, "utf-8")
        r = subprocess.run(["pandoc", "s.md", "-o", "s.tex",
                            "-f", "markdown+tex_math_dollars+hard_line_breaks-auto_identifiers"],
                           cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            sys.exit("pandoc failed:\n" + r.stderr[-2500:])
        body = (tmp / "s.tex").read_text("utf-8")
        title = f"{u['code']} notes sheet"
        tried = []
        for size in SIZES:
            built, pages = render(tmp, body, title, size)
            tried.append(f"{size} pt: {pages} side(s)")
            if pages <= n:
                out = pdf_tools.publish(built, unit_path(u, "cheatsheet_pdf"))
                print(f"  {u['code']}: {pages} of {n} side(s) at {size} pt ({'; '.join(tried)})")
                return out
        print(f"  {u['code']}: OVER BUDGET, {n} side(s) allowed ({'; '.join(tried)}). Cut lines in {src} and re-run; "
              "nothing was written.")
        raise SystemExit(1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--draft", action="store_true", help="one AI call: condense the handbook into cheatsheet.md")
    ap.add_argument("--pdf", action="store_true", help="print cheatsheet.md to the page budget (no AI)")
    a = ap.parse_args()
    if not (a.draft or a.pdf):
        sys.exit("choose --draft and/or --pdf")
    if not a.unit:
        sys.exit("name the subject: --unit <CODE> (the cheat sheet is built only on request, per subject)")
    for u in work_units(a.unit, a.allow_sat):
        if a.draft:
            print(f"  ok {draft(u)}")
        if a.pdf:
            print(f"  ok {pdf(u)}")


if __name__ == "__main__":
    main()
