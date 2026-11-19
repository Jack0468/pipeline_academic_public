"""Exam handbook per unit: concepts, formulas, constants, methods and YOUR traps, in exam-lookup form.

    python scripts/build_handbook.py --unit ABCD1001 --draft    # one AI call → units/<id>/handbook.md
    python scripts/build_handbook.py --unit ABCD1001 --pdf      # handbook.md → print PDF (no AI)

--draft builds from:
  - a formula sheet in units/<id>/sources/*formulas*.md (e.g. the converted shared sheet), the backbone;
  - each recall file's "Notes-sheet candidates" and "Common traps" (fills lectures the sheet misses);
  - your error_log, recall-check misconceptions and practice-check habits → "Trap (you)" lines;
  - the unit outline (coverage check); the exam format, materials and pace (min/mark).
The handbook is a draft once, then YOURS: --draft never overwrites an existing handbook.md (it writes
handbook_draft_<date>.md next to it instead, to compare or merge by hand).

--pdf renders whatever handbook.md says now (your edits included) as a small exam textbook, with pandoc
+ xelatex + PyMuPDF, to handbook.pdf next to handbook.md in the vault (--with-worked-examples: a separate combined PDF):
  handbook (two columns; a "Find it" line under the title gives the index, figure and concepts pages; each
  section points to its figures). Design and its change log: docs/HANDBOOK_DESIGN.md
  → slide-figure appendix (every cited slide, 6 per page) → A-Z index (book_index.py) with page numbers
  across both; a running header and an edge tab (config print.tabs) on every page.
Length budget follows the exam: open book → complete; "N x A4" → must fit N sheets double-sided.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import book_index
import gemini
from common import ALLOW_SAT_HELP, work_units
import pdf_tools
from common import PROMPTS, TZ, fill, intel_block, load_units, md_section, no_emoji, unit_path
from pace_recall import pace


section = md_section


def inputs(u) -> tuple[list[str], str]:
    texts = []
    sheets = sorted(unit_path(u, "sources").glob("*formulas*.md"))
    for s in sheets:
        texts.append(f"FORMULA SHEET ({s.stem}):\n\n{s.read_text('utf-8')}")
    covered = set()
    for s in sheets:
        txt = s.read_text("utf-8")
        m = re.search(r"Lectures?\s*(\d+)\s*[-–]\s*(\d+)", txt)
        if m:
            covered |= set(range(int(m.group(1)), int(m.group(2)) + 1))
        for tag in re.findall(r"^## \d+\..*\(([^)]*)\)\s*$", txt, re.M):     # "## 4. IPv4 ... (L5)"
            if not re.search(r"L\d+\s*[-–]\s*L?\d+", tag):               # a range such as (L1-L12) is a table, not coverage
                covered |= {int(x) for x in re.findall(r"L(\d+)", tag)}
    notes = []
    for p in sorted(unit_path(u, "recall").glob("*.md")):
        md = p.read_text("utf-8")
        body = "\n".join(x for x in (section(md, "Notes-sheet candidates"), section(md, "Common traps")) if x)
        if body:
            notes.append(f"### {p.stem}\n{re.sub(r'\[\[[^|\]]*\|([^\]]*)\]\]', r'\1', body)}")
    texts.append("RECALL NOTES (per lecture):\n\n" + "\n\n".join(notes))
    errs = []
    log = unit_path(u, "error_log")
    if log.exists():
        errs.append("ERROR LOG:\n" + re.sub(r"<!--.*?-->", "", log.read_text("utf-8")))
    for p in sorted(unit_path(u, "recall_checks").glob("*.md")):
        errs.append(f"RECALL CHECK {p.stem} misconceptions:\n" + section(p.read_text("utf-8"), "Misconceptions to fix"))
    for p in sorted(unit_path(u, "practice_checks").glob("*.md")):
        md = p.read_text("utf-8")
        errs.append(f"PRACTICE CHECK {p.stem} habits:\n" + section(md, "Habits to fix")
                    + "\nerrors:\n" + "\n".join(re.findall(r"^### .*$", section(md, "Where it went wrong"), re.M)))
    texts.append("STUDENT ERRORS:\n\n" + "\n\n".join(errs))
    outline = unit_path(u, "outline")
    if outline.exists():
        texts.append("UNIT OUTLINE:\n\n" + outline.read_text("utf-8"))
    intel = intel_block(u, "handbook")          # word on the street (ingest_intel.py): what gets full detail
    if intel:
        texts.append(intel)
    nums = sorted({int(m.group(1)) for p in unit_path(u, "recall").glob("*.md")          # the unit's real lecture numbers
                   if (m := re.search(r"_L(\d+)[a-z]?$", p.stem))} - {0})          # L0 is usually the unit outline
    gap = ("lectures " + ", ".join(f"L{i}" for i in nums if i not in covered)) if covered else "none known"
    return texts, gap


def budget(u) -> str:
    sheet = str(u["exam"].get("notes_sheet", "")).lower()
    m = re.search(r"(\d+)\s*x\s*a4", sheet)
    if "open book" in str(u["exam"].get("conditions", "")).lower() and not m:
        return "open book: be complete, but every line must be usable in the exam; index it well"
    if m:
        return (f"must fit {m.group(1)} A4 sheet(s) double-sided at 8-9 pt in two columns "
                f"(about {int(m.group(1)) * 2 * 1100} words): rank by exam value and cut the rest")
    return "compact: a few pages"


SECTION = """Each section, exactly:
## <n>. <problem type> (<lectures>)
**Use when:** <triggers, comma-separated>
$$<formula>$$  <units / condition>   (one formula per display line; keep each line short enough for a narrow column)
**Steps:** 1. ... 2. ... (only for multi-step problem types; terse imperatives)
**Trap (you):** <the student's own error and the fix> [<its source: error log <date> / recall check L<n> / practice check <name> / marker: <assessment>>]
**Trap:** <common trap from the recall notes>"""


def parts(sheet_sections: int, gap: str) -> list[str]:
    """Split the handbook into calls small enough for the model's output limit."""
    n = max(sheet_sections, 3)
    a, b = n // 3, 2 * n // 3
    rng = [(1, a), (a + 1, b), (b + 1, n)]
    out = []
    for i, (lo, hi) in enumerate(rng):
        extra = (f" Then add new sections numbered from {n + 1} for topics the formula sheet does not cover "
                 f"({gap}), using the recall notes.") if i == len(rng) - 1 and gap != "none known" else ""
        out.append(f"Write ONLY sections {lo} to {hi}: one handbook section per FORMULA SHEET section {lo}-{hi}, "
                   f"same numbers, titles and order, compressed to the style above.{extra}\n\n{SECTION}")
    return out


HEAD_PART = """Write ONLY the front of the handbook, in this structure:

# {code} exam handbook

**Exam:** <one line: conditions, materials, format, pace (min/mark)>

## Constants and conversions
| Quantity | Value | Use |
|---|---|---|

Then at the very end:

## Concepts (multiple choice)
| Term | Meaning (one line) |
|---|---|
(definitions and conceptual facts the multiple-choice section tests; include every misconception from the
student's checks, stated correctly)

Then, after the concepts table:

{phrases}

Mark the end of the front matter (after the constants table) with a line containing only ===BODY===, so the
concepts table can be placed after the sections."""

PHRASES_PART = """## Index phrases
_Not printed: these feed the A-Z index, so an exam phrasing finds its section._
| Asked for | Go to |
|---|---|
| <exam phrasing, e.g. "far-field distance", "polar form of Z", "receiver accepts or discards"> | <section number and title> |
(30 to 60 rows: the different ways an exam question could ask for each problem type, each at most 5 words,
including synonyms and the reverse direction; use ONLY these section titles: {titles})"""


def draft(u) -> Path:
    from concurrent.futures import ThreadPoolExecutor
    texts, gap = inputs(u)
    ex = u["exam"]
    base = fill((PROMPTS / "handbook.md").read_text("utf-8"), unit_code=u["code"], unit_name=u["name"],
                conditions=ex.get("conditions", ""), materials="; ".join(ex.get("materials") or []),
                format=" ".join(str(ex.get("format") or "not published").split()), pace=pace(u)[1],
                budget=budget(u), gap=gap)
    sheet = next((t for t in texts if t.startswith("FORMULA SHEET")), "")
    n_sheet = len(re.findall(r"^## \d+\.", sheet, re.M)) or 12
    jobs = parts(n_sheet, gap)
    print(f"  {u['code']}: drafting handbook in {len(jobs) + 1} parts ({len(texts)} source blocks; "
          f"sheet: {n_sheet} sections; gap: {gap})", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:      # agy handles a few parallel runs fine
        bodies = list(pool.map(lambda pt: no_emoji(gemini.generate([], fill(base, part=pt), texts=texts,
                                                                    temperature=0.2)), jobs))
    sections = "\n\n".join(b.strip() for b in bodies)
    titles = "; ".join(re.findall(r"^## (\d+\..*)$", sections, re.M))
    head = no_emoji(gemini.generate([], fill(base, part=HEAD_PART.replace("{code}", u["code"])
                                              .replace("{phrases}", PHRASES_PART).replace("{titles}", titles)),
                                    texts=texts, temperature=0.2))
    front, _, concepts = head.partition("===BODY===")
    body = front.strip() + "\n\n" + sections + "\n\n" + concepts.strip()
    out = unit_path(u, "handbook")
    if out.exists():
        out = out.with_name(f"handbook_draft_{datetime.now(TZ):%Y-%m-%d}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    front = ["---", f"unit: {u['code']}", "type: handbook", "source: handbook", f"drafted: {datetime.now(TZ):%Y-%m-%d}",
             f"model: {gemini.used_model()}", "owner: you (the pipeline never overwrites this file)", "---", ""]
    body, relabelled = check_own_traps(body)
    if relabelled:
        print(f"  {relabelled} 'Trap (you)' line(s) named no source in your record: relabelled as plain traps or dropped",
              flush=True)
    out.write_text("\n".join(front) + body.strip() + "\n", "utf-8")
    return out


OWN_SOURCE = re.compile(r"\[(?:error log|recall check|practice check|marker)[^\]]*\]\s*$", re.I)


def check_own_traps(md: str) -> tuple[str, int]:
    """A 'Trap (you)' line claims the student made that mistake, so it must end with where it was recorded
    ([error log ...], [recall check ...], [practice check ...], [marker: ...]). A line with no source becomes a plain
    trap; one saying nothing was logged is dropped. The handbook never attributes an invented mistake to the student."""
    out, n = [], 0
    for line in md.splitlines():
        if line.startswith("**Trap (you):**"):
            rest = line[len("**Trap (you):**"):].strip()
            if re.fullmatch(r"(?i)\(?none( logged)?\.?\)?|n/?a\.?|-|", rest):
                n += 1
                continue
            if not OWN_SOURCE.search(rest):
                n += 1
                line = "**Trap:** " + rest
        out.append(line)
    return "\n".join(out), n


TEXT = re.compile(r"\\text\{([^{}]*)\}")


SPACING = r"(?:\\[ ,;:!]|\\q?quad\b|\\[ ,]|[\s,;])"


def trim_space(seg: str) -> str:
    """A maths piece without LaTeX spacing at either end ("G\\ " would leave "$G\\$", an escaped dollar)."""
    seg = re.sub(rf"^{SPACING}+", "", seg)
    return re.sub(rf"{SPACING}+$", "", seg).strip()


def formula_line(f: str, units: str) -> str:
    """One handbook formula line for print. Mostly words → text with inline maths (it can wrap);
    otherwise \\fitmath{F} (shrunk to the column if too wide), a trailing (\\text{...}) note moved out of the
    maths into the italic units."""
    f, units = f.strip(), units.strip().strip("*")
    if "\\dagger" in f:     # a textbook-only marker, not part of the maths: it goes first in the note
        f = re.sub(r"\s*\^\s*\{?\\dagger\}?|\s*\\dagger", "", f).strip()
        units = "†" + (f" {units}" if units else "")

    def depth_at(i):
        return f[:i].count("{") - f[:i].count("}")
    top = [m for m in TEXT.finditer(f) if depth_at(m.start()) == 0]     # words inside \frac{..} stay maths
    words = sum(len(m.group(1)) for m in top)
    maths = len(re.sub(r"\\[A-Za-z]+|[{}\s^_]", "", TEXT.sub("", f)))
    if words > maths:
        parts, pos = [], 0
        for m in top:
            seg = trim_space(f[pos:m.start()])
            if seg:
                parts.append(f"${seg}$")
            parts.append(m.group(1).strip())
            pos = m.end()
        if trim_space(f[pos:]):
            parts.append(f"${trim_space(f[pos:])}$")
        body = re.sub(r"\s+", " ", " ".join(p for p in parts if p))
        return f"\\noindent {body}" + (f"   *{units}*" if units else "")
    m = re.match(r"^(.*?)(?:\\[ ,;]|\s)*\(\\text\{([^{}]*)\}\)(\^\\dagger)?\s*$", f)
    if m and m.group(1).strip():
        f, note = m.group(1).strip() + (m.group(3) or ""), m.group(2).strip()
        units = f"{note}; {units}" if units else note
    return f"\\fitmath{{{f}}}" + (f"   *{units}*" if units else "")


WORKLIST = re.compile(r"^## (to add|to do|todo)\b", re.I)
# Exam phrasings -> section. Never printed: the front Lookup table was dropped on 2026-11-18 because the A-Z index
# does the job better. The index still reads them, so "polar form of Z" finds its section. Older handbooks call
# the section "Lookup". docs/HANDBOOK_DESIGN.md
PHRASES = re.compile(r"^## (lookup|index phrases)\b", re.I)


def drop_worklists(md: str, pattern: re.Pattern = WORKLIST) -> str:
    """Your working lists ("## To add (requests from your notes)", "## To do") stay in handbook.md but are
    not exam content, so they are blanked for print (blank lines keep the line numbers for the page map)."""
    out, skip = [], False
    for ln in md.split("\n"):
        if ln.startswith("## "):
            skip = bool(pattern.match(ln))
        out.append("" if skip else ln)
    return "\n".join(out)


def math_outside(md: str) -> list[str]:
    """Lines with LaTeX commands or sub/superscripts outside $...$ (e.g. a condition written as 46 \\le L_{data}):
    xelatex stops on them."""
    out = []
    for i, ln in enumerate(md.splitlines(), 1):
        if ln.startswith(("|", "---")):
            continue
        plain = re.sub(r"\$\$.*?\$\$|\$[^$]*\$|`[^`]*`", "", ln)
        m = re.search(r"\\[A-Za-z]+|[_^]\{", plain)
        if m:
            out.append(f"line {i}: '{plain[max(0, m.start() - 30):m.end() + 30].strip()}': wrap the maths in $...$")
    return out


def lint(md: str, max_words: int = 40) -> list[str]:
    """Conciseness check (report only, the handbook is yours): wordy lines and near-duplicate lines in the
    same section. Formulas, tables and headings are not counted."""
    out, seen, cur = [], [], ""
    for i, ln in enumerate(md.splitlines(), 1):
        if ln.startswith("## "):
            cur, seen = ln[3:40], []
            continue
        if not ln.strip() or ln.startswith(("$$", "|", "#", "---")):
            continue
        prose = re.sub(r"\$[^$]*\$", " x ", re.sub(r"\([^)]*L\d+[^)]*\)", "", ln))
        n = len(re.findall(r"[A-Za-z]{2,}", prose))
        if n > max_words:
            out.append(f"line {i} ({cur}): {n} words, cut to the action and the fact")
        words = set(re.findall(r"[a-z]{4,}", prose.lower()))
        for j, w in seen:
            if words and len(words & w) / len(words | w) >= 0.7:
                out.append(f"line {i} ({cur}): repeats line {j}")
                break
        seen.append((i, words))
    return out


def overfull(log: Path, min_pt: float = 3.0) -> list[str]:
    """Lines LaTeX set wider than their column (what sticks out on the page), with a text snippet."""
    out, lines = [], log.read_text("utf-8", errors="replace").splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\)", ln)
        if m and float(m.group(1)) >= min_pt:
            snippet = re.sub(r"\s+", " ", " ".join(lines[i + 1:i + 3]))[:90]
            out.append(f"{float(m.group(1)):.0f}pt: {snippet}")
    return out


def to_pandoc(md: str, tag: bool = False, sec_figs: dict[str, list[int]] | None = None,
              n_figpages: int = 0, sec_we: dict[str, list[tuple[int, int]]] | None = None, worked: bool = False) -> str:
    """handbook.md → pandoc Markdown. With tag=True every content line carries \\label{gl<line>} (its
    printed page, for the index and headers), Lookup rows get the target section's page number, and each
    section gets a pointer to its slide-figure pages."""
    m = re.match(r"\A---\n.*?\n---\n", md, flags=re.S)                     # frontmatter → blank lines,
    if m:                                                                   # so line numbers still match
        md = "\n" * m.group(0).count("\n") + md[m.end():]
    md = re.sub(r"\[\[[^|\]\n]*\|([^\]\n]*)\]\]", r"\1", md)                  # wikilinks → label
    md = re.sub(r"\[\[([^\]\n]*)\]\]", r"\1", md)
    md = re.sub(r"<!--.*?-->", "", md)                                       # request markers
    md = re.sub(r"^> \[!(\w+)\][+-]? ?", lambda m: f"> **{m.group(1).capitalize()}:** ", md, flags=re.M)
    # the print maths font has no upright mu: move \mu out of \mathrm{...} / \text{...} groups
    for _ in range(3):
        md = re.sub(r"\\(mathrm|text)\{([^{}\n]*?)\\mu\s*([^{}\n]*)\}", r"\\\1{\2}\\mu\\\1{\3}", md)
    md = md.replace("\u00b5", "$\\mu$")
    # print layout: "$$F$$  (units)" → one line, formula then its units in italics. A formula can't break
    # across lines, so a wide one is shrunk to the column (\fitmath); one that is mostly words is set as text.
    md = re.sub(r"^\$\$(.+?)\$\$[ \t]*(\S.*)?$", lambda m: formula_line(m.group(1), m.group(2) or ""), md, flags=re.M)
    src = md.splitlines()
    secs = book_index.sections(src)
    head = {s["key"]: s["line"] for s in secs}
    concepts = next((s["line"] for s in secs if s["title"].lower().startswith("concepts")), None)
    out, cur = [], ""
    for i, line in enumerate(src):
        # each formula line and each bold label (Use when / Steps / Trap) is its own paragraph
        if re.match(r"^(\$|\*\*|\\fitmath|\\noindent)", line) and out and out[-1].strip() and not out[-1].startswith("|"):
            out.append("")
        h = re.match(r"^## (.*?)\s*$", line)
        if h:
            cur = h.group(1)
        if not tag or not line.strip() or (line.startswith("#") and not h):
            out.append(line)
            if line.startswith("# ") and tag:          # the way in is the index: say where it (and the rest) starts
                find = ([f"concepts \\lookuppage{{gl{concepts}}}"] if concepts is not None else []) \
                    + (["worked examples \\lookupnum{\\wepage{1}}"] if worked else []) \
                    + (["slide figures \\lookupnum{\\figpage{1}}"] if n_figpages else []) \
                    + [f"**A-Z index \\lookupnum{{\\figpage{{{n_figpages + 1}}}}}**"]
                out += ["", "**Find it:** " + " · ".join(reversed(find)) + ". \"(L3 p.12)\" = lecture 3, slide 12; "
                        "\"(Notes p.N)\" = page N of the unit's reference notes."]
            if line.startswith("# ") and "†" in md:
                out += ["", "*† = textbook formula, not on the lecture slides.*"]
            continue
        lab = f"\\glmark{{{i}}}"
        nxt = src[i + 1] if i + 1 < len(src) else ""
        if h:
            out.append(f"## {h.group(1)} {{#gl{i}}}")
            n = book_index.NUM.match(h.group(1))
            f = (sec_figs or {}).get(n.group(1) if n else "", [])
            if f:
                out += ["", f"\\glfigs{{{f[0]}}}{{{f[-1]}}}"]
            w = (sec_we or {}).get(n.group(1) if n else "", [])
            if w:
                out += ["", "\\glwe{" + ", ".join(f"WE{k} p.~\\wepage{{{r}}}" for k, r in w) + "}"]
        elif line.startswith("|"):
            if book_index.is_sep(line) or book_index.is_sep(nxt):           # header rows repeat per page
                out.append(line)
                continue
            cut = line.index("|", line.index("|") + 1)                     # label at the end of cell 1
            row = line[:cut].rstrip() + " " + lab + " " + line[cut:]
            if cur.lower().startswith("lookup"):
                c = book_index.cells(line)
                n = re.match(r"(\d+)\.", c[1]) if len(c) > 1 else None
                if n and n.group(1) in head:
                    row = row.rstrip().rstrip("|").rstrip() + f" \\lookuppage{{gl{head[n.group(1)]}}} |"
            out.append(row)
            if cur.lower().startswith("lookup") and not nxt.startswith("|"):
                if concepts is not None:
                    out.append(f"| Multiple-choice concepts (definitions, A to Z by topic) | Concepts "
                               f"\\lookuppage{{gl{concepts}}} |")
                if n_figpages:
                    out.append("| Slide figures (every cited slide, by section) | Figures \\lookupnum{\\figpage{1}} |")
                out.append(f"| Index (A to Z: terms, names, symbols) | Index \\lookupnum{{\\figpage{{{n_figpages + 1}}}}} |")
        elif line.startswith("$$"):
            out.append(line)                                                # display maths: label above it
        elif line.startswith(">"):
            out.append(re.sub(r"^>\s?", "> " + lab.replace("\\", "\\\\"), line, count=1))
        else:
            lm = re.match(r"^(\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?)", line)
            out.append(lm.group(1) + lab + line[lm.end():] if lm and not line.startswith("**") else lab + line)
    return no_emoji("\n".join(out))


LAYOUTS = {
    # compact: most per page. roomy: line and paragraph spacing, column gap and margins leave room to
    # annotate by hand between lines and around entries (no boxes).
    "compact": dict(margin="9mm", fontsize="9pt", columnsep="4mm", linespread="1.0", parskip="1pt",
                    sec_before="4pt", sec_after="1pt", disp="2pt", arraystretch="1.05"),
    "roomy": dict(margin="14mm", fontsize="9pt", columnsep="10mm", linespread="1.45", parskip="5pt",
                  sec_before="12pt", sec_after="4pt", disp="5pt", arraystretch="1.6"),
    # combined: the handbook plus worked examples, as few pages as stays readable; clear of the header and edge tabs
    "combined": dict(margin="11mm,top=13mm,bottom=13mm,footskip=6mm", fontsize="9pt", columnsep="5mm", linespread="1.0",
                     parskip="1.5pt", sec_before="5pt", sec_after="1pt", disp="2pt", arraystretch="1.1"),
}

HEADER = r"""
\usepackage{multicol}
\usepackage{fancyhdr}
\usepackage{refcount}
\usepackage{lastpage}
\setlength{\columnsep}{__COLUMNSEP__}
\setlength{\parskip}{__PARSKIP__}
\setlength{\parindent}{0pt}
\linespread{__LINESPREAD__}
\renewcommand{\arraystretch}{__ARRAYSTRETCH__}
\pagestyle{fancy}\fancyhf{}\renewcommand{\headrulewidth}{0pt}
\fancyfoot[C]{\scriptsize __TITLE__ \quad \thepage}
\usepackage{titlesec}
\titleformat{\section}{\normalsize\bfseries}{}{0pt}{}
\titleformat{\subsection}{\small\bfseries}{}{0pt}{}
\titlespacing*{\section}{0pt}{__SEC_BEFORE__}{__SEC_AFTER__}
\titlespacing*{\subsection}{0pt}{__SEC_BEFORE__}{__SEC_AFTER__}
% page numbers of this book: figures follow the handbook, then the index
\newcommand{\figpage}[1]{\number\numexpr\getpagerefnumber{LastPage}+__AFTER_BODY__+#1\relax}
\newcommand{\wepage}[1]{\number\numexpr\getpagerefnumber{LastPage}+#1\relax}
\newcommand{\glwe}[1]{{\scriptsize\itshape Worked examples: #1\par}}
\newcommand{\lookupnum}[1]{\mbox{\textbf{p.~#1}}}
\newcommand{\lookuppage}[1]{\lookupnum{\pageref{#1}}}
\newcommand{\glfigs}[2]{{\scriptsize\itshape Slide figures: p.~\figpage{#1}\ifnum#1=#2\else--\figpage{#2}\fi}\par}
% \glmark{n}: page of handbook line n, written to the .aux at shipout (read by book_index.label_pages)
\makeatletter
\newcommand{\glmark}[1]{\@bsphack\protected@write\@auxout{}{\string\glpage{#1}{\thepage}}\@esphack}
\newcommand{\glpage}[2]{}
\makeatother
% \fitmath{F}: a formula line; shrunk to the column width if it is wider (a formula can't break across lines)
\usepackage{graphicx}
\newsavebox{\glfitbox}
\newcommand{\fitmath}[1]{\sbox{\glfitbox}{$\displaystyle #1$}%
  \ifdim\wd\glfitbox>\linewidth\typeout{GLFIT \the\numexpr\linewidth*100/\wd\glfitbox\relax}%
    \resizebox{\linewidth}{!}{\usebox{\glfitbox}}\else\usebox{\glfitbox}\fi}
\newcommand{\glbegin}{\begin{multicols}{2}\raggedcolumns}
\AtBeginDocument{\setlength{\abovedisplayskip}{__DISP__}\setlength{\belowdisplayskip}{__DISP__}%
  \setlength{\abovedisplayshortskip}{__DISP__}\setlength{\belowdisplayshortskip}{__DISP__}%
  \glbegin}
\AtEndDocument{\end{multicols}}
"""

INDEX_TEX = r"""
\renewcommand{\glbegin}{\section*{Index}{\scriptsize\textbf{Bold}: main entry. \emph{Italic}: slide-figure
  page. Plain: other mentions. ``L3 p.~12'' in the text is a lecture slide page, not a page of this book.\par}
  \begin{multicols}{3}\raggedcolumns\raggedright\footnotesize\linespread{1.08}\selectfont\setlength{\parskip}{0pt}}
\newcommand{\idxletter}[1]{\par\vspace{5pt}{\small\bfseries #1}\par\nopagebreak\vspace{1pt}}
\newcommand{\idxentry}[2]{\par\hangindent=1.2em\hangafter=1\noindent #1,\nobreak\ #2}
\begin{document}
\setcounter{page}{__START__}
__BODY__
\end{document}
"""


REF = book_index.LREF


def header(title: str, layout: str, after_body: int = 0) -> str:
    """`after_body`: pages between the handbook text and the figures (the worked examples in the combined print)."""
    h = HEADER.replace("__TITLE__", title).replace("__AFTER_BODY__", str(after_body))
    for k, v in LAYOUTS[layout].items():
        h = h.replace(f"__{k.upper()}__", v)
    return h


def pages_of(spec: str) -> list[int]:
    return book_index._pages(spec)


plain = book_index.plain


def figure_items(u, md: str) -> list[tuple[str, str, str, int]]:
    """(section key, caption section, lecture label, page) for every slide page the handbook cites."""
    from common import find_sources, lecture_labels
    labels = {lab for lab in lecture_labels(find_sources(u, "lectures")).values()}
    lines = md.splitlines()
    secs = book_index.sections(lines)
    per = int((u.get("print") or {}).get("figures_per_section", 2))
    cited: dict[str, dict] = {}          # section key -> {(lab, page): [count, first position]}
    caps = {}
    for i, line in enumerate(lines):
        s = book_index.section_of(secs, i)
        if not (s and s["key"].isdigit()):
            continue          # only problem-type sections: Lookup, Constants and Concepts citations are lookups,
                              # not methods, and pulled in ~45 low-value slides (7 pages) for ABCD1001
        key = s["key"]
        caps[key] = f"§{s['key']} {plain(s['title'])}"
        for lab, spec in REF.findall(line):
            if lab not in labels:
                continue
            for p in pages_of(spec):
                c = cited.setdefault(key, {}).setdefault((lab, p), [0, i])
                c[0] += 1
    # per section, the `per` slides it cites most often (a fully cited handbook cites ~100 slides: the
    # appendix is for the key figure of each problem type, the rest are a click away in the vault)
    seen, items = set(), []
    for key in sorted(cited, key=int):
        ranked = sorted(cited[key].items(), key=lambda kv: (-kv[1][0], kv[1][1]))
        chosen = [lp for lp, _ in ranked if lp not in seen][:per]
        for lab, p in sorted(chosen, key=lambda lp: cited[key][lp][1]):
            seen.add((lab, p))
            items.append((key, caps[key], lab, p))
    return items


FIG_DPI = 250              # slide figures are embedded as images at this resolution
TITLE_COLOURED = 0.70      # a slide this much non-white is a title/section-divider slide: no content to show


def coloured_share(page) -> float:
    """Share of non-white pixels on a slide (title and divider slides are mostly solid colour; content
    slides about 0.1; calibrated on the ABCD1001 decks: divider 1.00, busiest content slide 0.43)."""
    import pymupdf
    pm = page.get_pixmap(dpi=20, colorspace=pymupdf.csRGB, alpha=False)
    s, n = pm.samples, pm.width * pm.height
    return sum(1 for i in range(n) if min(s[3 * i], s[3 * i + 1], s[3 * i + 2]) < 200) / max(n, 1)


NO_CONTENT_TITLE = re.compile(r"^(lecture\s+)?(outline|contents|table of contents|overview|agenda|topics|"
                              r"topics covered|in this lecture|learning outcomes|today'?s lecture)\s*:?\s*$", re.I)


def slide_title(page) -> str:
    """The largest text in the top 30% of a slide."""
    best = (0.0, "")
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                if s["text"].strip() and s["bbox"][1] < page.rect.height * 0.3 and s["size"] > best[0]:
                    best = (s["size"], s["text"].strip())
    return best[1]


def no_content(page) -> str:
    """Why a slide has nothing to show in the handbook ('' if it has): a title/divider slide (mostly solid
    colour) or an outline/agenda slide (by its title)."""
    if coloured_share(page) > TITLE_COLOURED:
        return "title slide"
    if NO_CONTENT_TITLE.match(slide_title(page)):
        return "outline slide"
    return ""


def appendix(u, md: str, out: Path) -> list[tuple[str, str, int, int]]:
    """Cited slide pages, 6 per A4 page, with captions and room around them. Each slide is embedded as an
    image (FIG_DPI): copying the slide's own fonts broke equations in some viewers, because PowerPoint
    exports reuse the same subset-font names across decks. Title/divider slides are skipped.
    Returns what was placed: (section key, lecture label, slide page, appendix page index)."""
    import pymupdf
    from common import find_sources, lecture_labels
    by_label = {lab: p for p, lab in lecture_labels(find_sources(u, "lectures")).items()}
    W, H = pymupdf.paper_size("a4")
    margin, gap, cap = 36, 22, 13
    cols, rows = 2, 3
    cw = (W - 2 * margin - gap * (cols - 1)) / cols
    ch = (H - 2 * margin - 20 - gap * (rows - 1)) / rows
    doc, srcs, placed = pymupdf.open(), {}, []
    page, skipped = None, []
    for key, section, lab, p in figure_items(u, md):
        src = srcs.setdefault(lab, pymupdf.open(str(by_label[lab])))
        if p < 1 or p > src.page_count:
            skipped.append(f"{lab} p.{p} (not a page: {lab} has {src.page_count}; fix the reference in handbook.md)")
            continue
        why = no_content(src[p - 1])
        if why:
            skipped.append(f"{lab} p.{p} ({why})")
            continue
        k = len(placed) % (cols * rows)
        if k == 0:
            page = doc.new_page(width=W, height=H)
        r, c = divmod(k, cols)
        x0 = margin + c * (cw + gap)
        y0 = margin + 10 + r * (ch + gap)
        cap_txt = section if len(section) <= 52 else section[:52].rsplit(" ", 1)[0].rstrip(",;:&") + " ..."
        page.insert_text((x0, y0 + 9), f"{cap_txt}  |  {lab} p.{p}", fontsize=7.5)
        box = pymupdf.Rect(x0, y0 + cap, x0 + cw, y0 + ch)
        sp = src[p - 1]
        zoom = min(box.width / sp.rect.width, box.height / sp.rect.height) * FIG_DPI / 72
        pm = sp.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False, annots=True)
        page.insert_image(box, stream=pm.tobytes("jpeg", jpg_quality=88), keep_proportion=True)
        page.draw_rect(box, color=(0.75, 0.75, 0.75), width=0.4)
        placed.append((key, lab, p, doc.page_count - 1))
    if skipped:
        print(f"  {u['code']}: skipped {len(skipped)} slide(s) with no content: {', '.join(skipped)}")
    if placed:
        doc.save(str(out))
    doc.close()
    for s in srcs.values():
        s.close()
    return placed


def xelatex(tmp: Path, name: str, passes: int) -> None:
    for _ in range(passes):
        r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", name], cwd=tmp,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        if r.returncode:
            sys.exit(f"xelatex failed on {name}:\n" + r.stdout[-2500:])


def page_info(body_pdf: Path, lines: list[str], line_pages: dict[int, int]) -> list[tuple[str, str]]:
    """(running header, tab key) for each handbook page: the section running at the top of the page
    through the last one that starts on it; the tab follows the section at the top."""
    import pymupdf
    secs = book_index.sections(lines)
    start = [(s, line_pages.get(s["line"])) for s in secs if line_pages.get(s["line"])]
    name = lambda s: (f"\u00a7{s['key']} {plain(s['title'])}" if s["key"].isdigit() else plain(s["title"]))
    out = []
    doc = pymupdf.open(str(body_pdf))
    for pi, page in enumerate(doc):
        p = pi + 1
        here = [s for s, sp in start if sp == p]
        before = [s for s, sp in start if sp < p]
        keys = here[:]
        if before:
            blocks = sorted((b for b in page.get_text("blocks") if b[4].strip()), key=lambda b: (b[1], b[0]))
            top = blocks[0][4].strip() if blocks else ""
            first = here[0] if here else None
            at_top = first and (top.startswith(f"{first['key']}.") or top.startswith(plain(first["title"])[:10]))
            if not at_top:
                keys = [before[-1]] + keys
        if not keys:
            out.append(("", ""))
            continue
        txt = name(keys[0]) + (f"  \u2013  \u00a7{keys[-1]['key']}" if keys[-1]["key"].isdigit() and len(keys) > 1
                               else (f"  \u2013  {plain(keys[-1]['title'])}" if len(keys) > 1 else ""))
        out.append((txt, [k["key"] for k in keys]))          # a tab for every group on the page
    doc.close()
    return out


RELATION = re.compile(r"\\(?:iff|implies|Rightarrow|Longrightarrow|Leftrightarrow|Longleftrightarrow|approx|equiv)\b|=")


def split_relations(f: str) -> list[str]:
    """A long formula cut before each top-level relation (not inside braces or \\left...\\right)."""
    if len(f) < 60:
        return [f]
    parts, start, depth, i = [], 0, 0, 0
    while i < len(f):
        if f.startswith("\\left", i) or f[i] == "{":
            depth += 1
        elif f.startswith("\\right", i) or f[i] == "}":
            depth -= 1
        m = RELATION.match(f, i)
        if m and depth == 0 and i > start and f[i - 1] != "\\":
            parts.append(f[start:i].strip())
            start = i
            i = m.end()
            continue
        i += 1
    parts.append(f[start:].strip())
    parts = [x for x in parts if x]
    return [parts[0] + " " + parts[1]] + parts[2:] if len(parts) > 1 else parts     # keep "x = ..." together


WE_DISPLAY = re.compile(r"(?m)^([ \t]*)\$\$(.+?)\$\$[ \t]*$")


def worked_pdf(u, md_path: Path, tmp: Path, name: str, title: str, layout: str, first_page: int) -> Path:
    """The worked examples set like the handbook: two flowing columns, page numbers continuing from `first_page`."""
    import worked_examples

    def display(m):
        """One display formula: units and source notes leave the maths, and a long chain gets a line per relation."""
        indent, f, tail = m.group(1), m.group(2), []
        note = re.search(r"(?:\\q?quad|\\[,;]|\s)*\(\\text\{([^{}]*)\}\)\s*$", f)
        if note:
            tail.append(f"*({note.group(1).strip()})*")
            f = f[:note.start()]
        units = re.search(r"\\q?quad\s*(\[.*\])\s*$", f)
        if units:
            tail.insert(0, f"${units.group(1)}$")
            f = f[:units.start()]
        lines = [f"\\wefit{{{part}}}" for part in split_relations(f.strip())]
        lines[-1] += ("   " + " ".join(tail)) if tail else ""
        return "\n" + "\n\n".join(indent + x for x in lines) + "\n"
    md = WE_DISPLAY.sub(display, worked_examples.print_markdown(u, md_path, flowing=True))
    src, hdr, tex = tmp / f"{name}.md", tmp / f"{name}_h.tex", tmp / f"{name}.tex"
    src.write_text(no_emoji(md), "utf-8")
    hdr.write_text(header(title, layout) + "\n\\makeatletter\\def\\verbatim@font{\\ttfamily\\scriptsize}\\makeatother\n"
                   # shrink a slightly wide formula; let a much wider one wrap as text rather than become unreadable
                   "\\newcommand{\\wefit}[1]{\\sbox{\\glfitbox}{$\\displaystyle #1$}%\n"
                   "\\ifdim\\wd\\glfitbox>1.3\\linewidth $#1$\\else\\fitmath{#1}\\fi}\n"
                   f"\\AtBeginDocument{{\\sloppy\\setcounter{{page}}{{{first_page}}}}}\n", "utf-8")   # narrow columns: wrap, never overflow
    L = LAYOUTS[layout]
    r = subprocess.run(["pandoc", str(src), "-s", "-o", str(tex), "-H", str(hdr), "--toc", "--toc-depth=2",
                        "-V", "toc-title=Worked examples by calculation type",
                        "-V", f"geometry:a4paper,margin={L['margin']}", "-V", f"fontsize={L['fontsize']}",
                        "-V", "documentclass=extarticle", "-V", "mainfont=Calibri",
                        "-f", "markdown+tex_math_dollars+pipe_tables+hard_line_breaks+lists_without_preceding_blankline"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit("pandoc failed on the worked examples:\n" + r.stderr[-2500:])
    tex.write_text(columnar(tex.read_text("utf-8")), "utf-8")
    xelatex(tmp, tex.name, 2)
    return tmp / f"{name}.pdf"


def worked_section(u, tmp: Path, title: str, layout: str) -> dict:
    """The worked examples as a section of the combined print: its page count and, per example, the page inside
    the section where it starts, its title, lectures and "Recognise it" terms."""
    import pymupdf
    md_path = unit_path(u, "worked_examples")
    if not md_path.exists():
        sys.exit(f"{md_path} not found: nothing to merge")
    md = md_path.read_text("utf-8")
    heads = list(re.finditer(r"^## WE(\d+)\. (.+?)(?: \(([^()]*)\))?\s*$", md, re.M))
    items = {}
    for i, m in enumerate(heads):
        part = md[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(md)]
        rec = re.search(r"^\*\*Recognise it:\*\*(.*)$", part, re.M)
        items[int(m.group(1))] = dict(title=m.group(2), lectures=re.findall(r"L\d+", m.group(3) or ""),
                                      terms=book_index.split_top(rec.group(1).strip(" .")) if rec else [])
    trial = worked_pdf(u, md_path, tmp, "we_trial", title, layout, 1)
    aux = (tmp / "we_trial.aux").read_text("utf-8", errors="replace")
    for k, page in re.findall(r"\\newlabel\{we(\d+)\}\{\{[^{}]*\}\{(\d+)\}", aux):
        if int(k) in items:
            items[int(k)]["page"] = int(page)
    lost = [k for k, v in items.items() if "page" not in v]
    if lost:
        sys.exit(f"could not find the page of worked example(s) {lost}")
    with pymupdf.open(str(trial)) as doc:
        n = doc.page_count
    log = (tmp / "we_trial.log").read_text("utf-8", errors="replace")
    fits = [int(x) for x in re.findall(r"^GLFIT (\d+)", log, re.M)]
    if fits:
        print(f"  {u['code']}: worked examples: {len(fits)} wide formula(s) shrunk (smallest {min(fits)}% of normal size)")
    wide = overfull(tmp / "we_trial.log")
    if wide:
        print(f"  WARN {u['code']}: {len(wide)} worked-example line(s) wider than the column")
    return dict(n=n, items=items, path=md_path)


def pdf(u, layout: str = "roomy", figures: bool = True, index: bool = True, worked: bool = False) -> Path:
    """The handbook PDF; with `worked`, the separate combined print with the worked examples as a section."""
    import pymupdf
    src = unit_path(u, "handbook")
    if not src.exists():
        sys.exit(f"{src} not found: run --draft first")
    out = unit_path(u, "handbook_complete_pdf" if worked else "handbook_pdf")
    L = LAYOUTS[layout]
    pcfg = u.get("print") or {}
    title = f"{u['code']} exam handbook"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        full = drop_worklists(src.read_text("utf-8"))         # what the index reads (index phrases included)
        raw = drop_worklists(full, PHRASES)                    # what prints; line numbers stay the same
        broken = math_outside(raw)
        if broken:                              # LaTeX in plain text stops xelatex: say where, before it runs
            print(f"  {u['code']}: FIX FIRST, LaTeX outside $...$ in handbook.md (the PDF build would fail):")
            for x in broken[:15]:
                print(f"    {x}")
            raise SystemExit(1)
        notes = lint(raw)
        if notes:
            print(f"  {u['code']}: conciseness check, {len(notes)} line(s) worth trimming in handbook.md:")
            for x in notes[:15]:
                print(f"    {x}")
        lines = raw.splitlines()
        placed = appendix(u, raw, tmp / "fig.pdf") if figures else []
        n_figpages = placed[-1][3] + 1 if placed else 0
        sec_rel = {}
        for key, _, _, pg in placed:
            sec_rel.setdefault(key, []).append(pg + 1)
        we = worked_section(u, tmp, title, layout) if worked else None
        n_we = we["n"] if we else 0
        sec_we = {}
        if we:
            for key, heading in re.findall(r"^## (\d+)\. .*\((L\d+)\)\s*$", raw, re.M):
                sec_we[key] = [(k, v["page"]) for k, v in sorted(we["items"].items()) if heading in v["lectures"]]
        md, hdr, tex = tmp / "g.md", tmp / "h.tex", tmp / "g.tex"
        md.write_text(to_pandoc(raw, tag=True, sec_figs=sec_rel, n_figpages=n_figpages, sec_we=sec_we, worked=worked), "utf-8")
        hdr.write_text(header(title, layout, n_we), "utf-8")
        cmd = ["pandoc", str(md), "-s", "-o", str(tex), "-H", str(hdr),
               "-V", f"geometry:a4paper,margin={L['margin']}", "-V", f"fontsize={L['fontsize']}",
               "-V", "documentclass=extarticle", "-V", "mainfont=Calibri",
               "-f", "markdown+tex_math_dollars+pipe_tables"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            sys.exit("pandoc failed:\n" + r.stderr[-2500:])
        tex_s = columnar(tex.read_text("utf-8"))
        tex.write_text(tex_s, "utf-8")
        xelatex(tmp, tex.name, 3)                            # labels, page refs and column balancing settle
        log = (tmp / "g.log").read_text("utf-8", errors="replace")
        fits = [int(x) for x in re.findall(r"^GLFIT (\d+)", log, re.M)]
        if fits:
            print(f"  {u['code']}: {len(fits)} wide formula(s) shrunk to the column (smallest {min(fits)}% of normal size)")
        wide = overfull(tmp / "g.log")
        if wide:
            print(f"  WARN {u['code']}: {len(wide)} line(s) still wider than the column (edit these in handbook.md):")
            for w in wide[:12]:
                print(f"    {w}")
        line_pages = book_index.label_pages(tmp / "g.aux")
        body = tmp / "g.pdf"
        with pymupdf.open(str(body)) as bd:
            n_body = bd.page_count
        info = page_info(body, lines, line_pages)
        fig_pages = {(lab, p): n_body + n_we + 1 + pg for _, lab, p, pg in placed}
        sec_abs = {k: [n_body + n_we + x for x in v] for k, v in sec_rel.items()}
        if we:
            with pymupdf.open(str(worked_pdf(u, we["path"], tmp, "we", title, layout, n_body + 1))) as d:
                if d.page_count != n_we:
                    sys.exit(f"worked examples came out at {d.page_count} pages, expected {n_we}")
        n_ix = 0
        if index:
            ix = book_index.build(full.splitlines(), line_pages, fig_pages, sec_abs, see=pcfg.get("see"), terms=pcfg.get("terms"))
            index_tex = INDEX_TEX
            if we:
                index_tex = index_tex.replace("Plain: other mentions.", "Plain: other mentions. WE~24: worked example on page 24.")
                for v in we["items"].values():
                    for term in [v["title"], *book_index.split_top(v["title"]), *v["terms"]]:
                        term = book_index.drop_parens(term)
                        if len(book_index.plain(term).split()) <= 6:
                            ix.add(term, we=[n_body + v["page"]])
            pre = tex_s[:tex_s.index("\\begin{document}")]
            (tmp / "ix.tex").write_text(pre + index_tex.replace("__START__", str(n_body + n_we + n_figpages + 1))
                                        .replace("__BODY__", book_index.render(ix)), "utf-8")
            xelatex(tmp, "ix.tex", 2)
            n_ix = len(ix.e)
        merged = pymupdf.open(str(body))
        for part in ([tmp / "we.pdf"] if we else []) + ([tmp / "fig.pdf"] if placed else []) + ([tmp / "ix.pdf"] if index else []):
            with pymupdf.open(str(part)) as pd:
                merged.insert_pdf(pd)
        n_total = merged.page_count
        merged.save(str(tmp / "merged.pdf"))
        merged.close()
        if we:
            starts = sorted((v["page"], k, v["title"]) for k, v in we["items"].items())
            for r in range(1, n_we + 1):
                on = [s for s in starts if s[0] == r]
                before = [s for s in starts if s[0] < r][-1:]
                shown = before + on
                txt = ("Worked examples: " + (f"WE{shown[0][1]} {plain(shown[0][2])}" if len(shown) == 1
                                              else f"WE{shown[0][1]} to WE{shown[-1][1]}")) if shown else "Worked examples"
                info.append((txt, "WE"))
        for pg in range(n_figpages):                         # figure pages: which sections they hold
            ks = sorted({k for k, _, _, q in placed if q == pg}, key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0))
            nums = [k for k in ks if k.isdigit()]
            txt = "Slide figures" + (f": \u00a7{nums[0]}" + (f" to \u00a7{nums[-1]}" if nums[-1] != nums[0] else "")
                                     if nums else "")
            info.append((txt, "FIG"))
        info += [("Index", "IDX")] * (n_total - len(info))
        groups = book_index.tab_groups(pcfg.get("tabs"), [s["key"] for s in book_index.sections(lines)])
        if we:
            at = next((i for i, g in enumerate(groups) if g[1] & {"FIG", "IDX"}), len(groups))
            groups.insert(at, ("Worked", {"WE"}))
        book_index.decorate(tmp / "merged.pdf", tmp / "final.pdf", info, groups, title,
                            fig_range=(n_body + n_we + 1, n_body + n_we + n_figpages) if n_figpages else None,
                            duplex=pcfg.get("duplex", True))
        pdf_tools.publish(tmp / "final.pdf", out)
    print(f"  {u['code']}: layout {layout}; {n_body} handbook + {n_we} worked-example + {n_figpages} figure + {n_total - n_body - n_we - n_figpages}"
          f" index page(s) = {n_total}; {len(placed)} slide figure(s); {n_ix} index entries; {len(groups)} edge tabs")
    return out


def columnar(tex: str) -> str:
    """Tables can't break inside two columns, so each table is set full width (and may break across pages)
    by closing the two-column block around it; the formula sections stay in two balanced columns."""
    # pandoc may wrap each table in "{\def\LTcaptype{none} ... }": take the whole group
    return re.sub(r"(?:\{\\def\\LTcaptype\{none\}[^\n]*\n)?(\\begin\{longtable\}.*?\\end\{longtable\})(?:\n\})?",
                  lambda m: "\\end{multicols}\n{\\def\\LTcaptype{none}\\small\n" + m.group(1)
                            + "\n}\n\\begin{multicols}{2}\\raggedcolumns\n",
                  tex, flags=re.S)


def index_phrases(u) -> Path:
    """(Re)write only the hidden '## Index phrases' section of an existing handbook.md (one AI call), from the
    handbook's own section titles and 'Use when' lines. Everything else in the file is left as it is; the edit is
    logged in the front matter."""
    p = unit_path(u, "handbook")
    md = p.read_text("utf-8")
    titles = "; ".join(re.findall(r"^## (\d+\..*)$", md, re.M))
    ex = u["exam"]
    base = fill((PROMPTS / "handbook.md").read_text("utf-8"), unit_code=u["code"], unit_name=u["name"],
                conditions=ex.get("conditions", ""), materials="; ".join(ex.get("materials") or []),
                format=" ".join(str(ex.get("format") or "not published").split()), pace=pace(u)[1],
                budget=budget(u), gap="none known")
    task = ("Write ONLY this section, nothing else:\n\n" + PHRASES_PART.replace("{titles}", titles))
    reply = no_emoji(gemini.generate([], fill(base, part=task), texts=["HANDBOOK (the sections to point at):\n\n" + md],
                                     temperature=0.2)).strip()
    if not reply.startswith("## Index phrases"):
        reply = "## Index phrases\n" + reply
    old = re.search(r"^## (?:Lookup|Index phrases)\b.*?(?=^## |\Z)", md, re.S | re.M | re.I)
    md = (md[:old.start()] + reply + "\n\n" + md[old.end():]) if old else md.rstrip() + "\n\n" + reply + "\n"
    note = (f'  - "{datetime.now(TZ):%Y-%m-%d} build_handbook.py --index-phrases ({gemini.used_model()}): '
            f'{"replaced" if old else "added"} the hidden Index phrases section (not printed; feeds the A-Z index)"\n')
    if re.search(r"^edits:\n", md, re.M):
        md = re.sub(r"^(edits:\n(?:  - .*\n)*)", lambda m: m.group(1) + note, md, count=1, flags=re.M)
    else:
        md = md.replace("\n---\n", "\nedits:\n" + note + "---\n", 1)
    p.write_text(md, "utf-8")
    rows = len(re.findall(r"^\|(?!\s*-)(?!\s*Asked for)", reply, re.M))
    print(f"  {u['code']}: {rows} index phrase(s)")
    return p


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--index-phrases", action="store_true",
                    help="(re)write only the hidden Index phrases section of handbook.md (1 AI call)")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--layout", choices=sorted(LAYOUTS),
                    help="roomy (default): space to annotate by hand; compact: fewest pages; "
                         "combined: the default with --with-worked-examples")
    ap.add_argument("--no-figures", action="store_true", help="skip the slide-figure appendix")
    ap.add_argument("--no-index", action="store_true", help="skip the A-Z index")
    ap.add_argument("--with-worked-examples", action="store_true",
                    help="with --pdf: write the separate combined print, with worked_examples.md as a section of the book")
    ap.add_argument("--if-stale", action="store_true",
                    help="with --pdf: only units whose handbook.md is newer than handbook.pdf (refresh.ps1)")
    a = ap.parse_args()
    if not (a.draft or a.pdf or a.index_phrases):
        sys.exit("choose --draft, --index-phrases and/or --pdf")
    if not a.unit:                       # rule since 2026-11-18: the handbook is built only when asked, per subject
        sys.exit("name the subject: --unit <CODE> (the handbook is never built for every unit at once)")
    for u in work_units(a.unit, a.allow_sat):
        if a.draft:
            print(f"  ok {draft(u)}")
        if a.index_phrases:
            print(f"  ok {index_phrases(u)}")
        if a.pdf:
            if a.if_stale:
                md, out = unit_path(u, "handbook"), unit_path(u, "handbook_pdf")
                if not md.exists() or (out.exists() and out.stat().st_mtime >= md.stat().st_mtime):
                    continue
                print(f"  {u['code']}: handbook.md changed since the last print, rebuilding the PDF")
            layout = a.layout or ("combined" if a.with_worked_examples else "roomy")
            print(f"  ok {pdf(u, layout, not a.no_figures, not a.no_index, a.with_worked_examples)}")


if __name__ == "__main__":
    main()
