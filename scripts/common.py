"""Shared helpers: config loading, paths, file discovery, hashing, the manifest.

Everything here is deterministic and costs zero AI tokens.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from paths import (CALENDAR_DIR, CONFIG, CURRENT_LAYOUT, EXAMS_ICS, LAYOUTS, ONEDRIVE_COURSEWORK, OUT, PROMPTS, REPO,
                   STATE, STUDY_ICS, VAULT, layout, unit_rel)

TZ = ZoneInfo("Australia/Sydney")


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def expand(s: str) -> str:
    return s.replace("${ONEDRIVE_COURSEWORK}", str(ONEDRIVE_COURSEWORK)).replace("${STATE}", str(STATE))


def load_units(only: list[str] | None = None) -> list[dict]:
    """All unit configs, sorted by exam start. `only` filters by code (case-insensitive)."""
    units = []
    for p in sorted((CONFIG / "units").glob("*.yaml")):
        u = load_yaml(p)
        u["id"] = p.stem                      # e.g. 2026_sc2_abcd1003
        u["exam"]["start_dt"] = datetime.fromisoformat(str(u["exam"]["start"])).replace(tzinfo=TZ)
        if only and u["code"].lower() not in {o.lower() for o in only}:
            continue
        units.append(u)
    return sorted(units, key=lambda u: u["exam"]["start_dt"])


def unit_dir(u: dict) -> Path:
    return ONEDRIVE_COURSEWORK / u["onedrive_folder"]


def exam_over(u: dict, now: datetime | None = None) -> bool:
    """True once the unit's exam has finished (start + reading + writing)."""
    ex = u["exam"]
    end = ex["start_dt"] + timedelta(minutes=int(ex.get("reading_min") or 0) + int(ex.get("writing_min") or 0))
    return (now or datetime.now(TZ)) >= end


ALLOW_SAT_HELP = "also change units whose exam is over (frozen by default, a rule since 2026-11-18)"


def work_units(only: list[str] | None = None, allow_sat: bool = False) -> list[dict]:
    """Units a script that writes course content may touch. A unit whose exam is over is FROZEN: nothing about it
    is regenerated or altered (recall files, transcriptions, handbook, mocks, Canvas data, slides) unless the
    command is run with --allow-sat for it. Rule since 2026-11-18. Read-only tools (marks, plan, share,
    unit_inputs) and reconstruct_exam.py (which exists for sat exams) use load_units() instead."""
    out = []
    for u in load_units(only):
        if exam_over(u) and not allow_sat:
            print(f"  {u['code']}: exam sat {u['exam']['start_dt']:%a %d %b}; frozen, nothing changed "
                  "(--allow-sat to change it on purpose)")
            continue
        out.append(u)
    return out


def vault_unit_dir(u: dict) -> Path:
    return VAULT / "units" / u["id"]


def unit_path(u: dict, key: str) -> Path:
    """A note or folder of a unit, by key (paths.py). The only way scripts locate unit files.

    A unit still on layout 1 may already hold a file where the standard layout puts it (e.g. reference/exam_intel.md,
    written by hand or by a newer tool). When the layout-1 place is empty and the standard one exists, that file is
    used, so readers find it and writers update it in place instead of starting a second copy."""
    p = vault_unit_dir(u) / unit_rel(u, key)
    if not p.exists() and layout(u) == 1 and key in LAYOUTS[CURRENT_LAYOUT]:
        alt = vault_unit_dir(u) / LAYOUTS[CURRENT_LAYOUT][key].replace("{code}", str(u.get("code", "")))
        if alt.exists():
            return alt
    return p


def unit_link(u: dict, key: str, label: str, table: bool = False) -> str:
    """Obsidian link to a unit note by its full vault path. table=True escapes the | for use in a table cell."""
    target = f"units/{u['id']}/{unit_rel(u, key)}".removesuffix(".md")
    sep = "\\|" if table else "|"
    return f"[[{target}{sep}{label}]]"


def md_section(md: str, title: str) -> str:
    """Body of the `## title` section of a Markdown note ('' if there is none)."""
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


_num = re.compile(r"(\d+)")


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in _num.split(p.name)]


_lec = re.compile(r"(?:lecture|week|lec|wk)\D{0,12}?(\d{1,2})(?!\d)", re.I)


def lecture_number(p: Path) -> int | None:
    """Lecture/week number from messy names: 'ABCD1003___Lecture_5-5', 'Lecture new week 8', 'Week 3 _New'."""
    m = _lec.search(p.stem)
    return int(m.group(1)) if m else None


def sort_lectures(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: (lecture_number(p) is None, lecture_number(p) or 0, p.name.lower()))


def lecture_labels(paths: list[Path]) -> dict[Path, str]:
    """Short labels L1, L2 … ; duplicates of a number get a/b suffixes; unnumbered keep their stem."""
    nums = [lecture_number(p) for p in paths]
    out, seen = {}, {}
    for p, n in zip(paths, nums):
        if n is None:
            out[p] = p.stem.replace("___", " ").replace("_", " ")
            continue
        if nums.count(n) > 1:
            seen[n] = seen.get(n, 0) + 1
            out[p] = f"L{n}{'abcdef'[seen[n] - 1]}"
        else:
            out[p] = f"L{n}"
    return out


# Folder conventions that work for every unit without config (a unit's `sources:` entry replaces the default):
#   exam_info_research/  word on the street (ingest_intel.py)
#   xtra_resources/      third-party study notes: shared formula sheets, condensed notes (mocks; handbook backbone
#                        after convert_reference.py)
DEFAULT_SOURCES = {"intel": ["exam_info_research/**/*"], "reference": ["xtra_resources/**/*.pdf"]}


def find_sources(u: dict, kind: str) -> list[Path]:
    """Resolve a unit's source globs, de-duplicated by content hash.

    Globs are relative to the unit's OneDrive folder unless they start with ${STATE} or
    ${ONEDRIVE_COURSEWORK} (an absolute path replaces the base when joined)."""
    base = unit_dir(u)
    seen_paths, seen_hash, out = set(), set(), []
    for pattern in (u.get("sources") or {}).get(kind) or DEFAULT_SOURCES.get(kind, []):
        for s in glob.glob(str(base / expand(pattern)), recursive=True):
            p = Path(s)
            if not p.is_file() or p in seen_paths:
                continue
            seen_paths.add(p)
            h = file_hash(p)
            if h in seen_hash:                # identical copy under another name
                continue
            seen_hash.add(h)
            out.append(p)
    return sort_lectures(out) if kind == "lectures" else sorted(out, key=natural_key)


# A page that starts the answers: "Solutions", "Week 2 Solutions", "Week 4 Tutorial Solutions", "Tutorial 3 Solution:"
SOLUTIONS_HEADING = re.compile(r"(?im)^\s*(?:week\s*\d{1,2}\s*)?(?:tutorial\s*\d{0,2}\s*)?solutions?\s*:?\s*$")


def solutions_start(page_texts: list[str], name: str = "") -> int | None:
    """The 1-based page where a PDF's answers start: the first page with a solutions heading, else page 1 when the
    file name says it holds solutions. None: a plain question sheet. Course solution files often repeat the questions
    first, so the answers start later."""
    for i, text in enumerate(page_texts, 1):
        if SOLUTIONS_HEADING.search(text):
            return i
    return 1 if re.search(r"(?i)solution", name) else None


def doc_profile(p: Path) -> tuple[str, str]:
    """(kind, text) of a PDF: kind "solutions" (a solutions heading, or "solution" in the name) or "sheet"; text is the
    question part (the pages before the answers start), "" for a scan with no text layer."""
    import pymupdf
    try:
        with pymupdf.open(str(p)) as d:
            pages = [pg.get_text() for pg in d]
    except Exception:
        return "unreadable", ""
    start = solutions_start(pages, p.name)
    return ("solutions" if start else "sheet"), "".join(pages[: start - 1] if start else pages).strip()


def doc_similarity(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a[:12000], b[:12000]).ratio() if a and b else 0.0


def name_key(p: Path) -> str:
    """Two files share a name when this matches: case and separators ignored, and Canvas's "-N" repeat suffixes
    ("Tutorial_6-4", "Lecture_9-7-1") dropped. The real number before them stays: Tutorial_6 and Tutorial_7 differ."""
    stem = re.sub(r"(?:-\d{1,2})+$", "", p.stem)
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def doc_relation(a: Path, b: Path) -> str:
    """What really differs between two files that share a name (the rule: never assume they are the same thing)."""
    if file_hash(a) == file_hash(b):
        return "identical copies"
    (ka, ta), (kb, tb) = doc_profile(a), doc_profile(b)
    if ka != kb and (not ta or not tb) and (ta or tb):      # a solutions file that starts with the answers
        sheet, sol = (a, b) if ka == "sheet" else (b, a)
        return f"question sheet ({sheet.name}) and its solutions ({sol.name}, which starts with the answers)"
    if not ta or not tb:
        return "cannot compare: no text layer (a scan or your own notes?)"
    r = doc_similarity(ta, tb)
    if ka != kb:
        sheet, sol = (a, b) if ka == "sheet" else (b, a)
        return (f"question sheet ({sheet.name}) and its solutions ({sol.name}), {r:.0%} the same questions"
                if r >= 0.8 else f"a sheet and a solutions file with different questions ({r:.0%}): check them")
    if r >= 0.9:
        return f"the same {ka}, another version ({r:.0%} the same text)"
    return f"different documents ({r:.0%} the same text): check which you need"


def tutorial_solutions(u: dict) -> list[tuple[Path, int]]:
    """(PDF, first answer page) for each file the unit's `sources.tutorial_solutions` globs find that holds
    solutions. The globs may be broad (e.g. every tutorial file in the Canvas mirror): question-only copies are
    skipped. Read by link_tutorials.py (vault index), unit_inputs.py and worked_examples.py."""
    import pymupdf
    out = []
    for p in find_sources(u, "tutorial_solutions"):
        try:
            with pymupdf.open(str(p)) as d:
                start = solutions_start([page.get_text() for page in d], p.name)
        except Exception as e:                  # a locked or half-synced OneDrive file: say so, keep going
            print(f"  WARN {u['code']}: can't read {p.name} ({e.__class__.__name__}); skipped")
            continue
        if start:
            out.append((p, start))
    return out


ORIGINAL_LINE = re.compile(r'(?m)^original:\s*"?file:///([^"\n]+)"?')


def transcription_for(u: dict, pdf: Path) -> Path:
    """The transcription of a handwritten PDF: the sources/handwritten_*.md whose `original:` names this PDF, so a
    transcription you renamed stays the one (no second copy appears), else handwritten_<stem>.md."""
    from urllib.parse import unquote
    folder = unit_path(u, "sources")
    for p in sorted(folder.glob("handwritten_*.md")):
        with open(p, encoding="utf-8") as f:
            m = ORIGINAL_LINE.search(f.read(4000))
        if m and Path(unquote(m.group(1))).name == pdf.name:
            return p
    return folder / f"handwritten_{pdf.stem}.md"


def transcription_content(text: str) -> str:
    """What you can change in a transcription: the open questions (ticks) and the pages (corrections). The header
    above it is the pipeline's (dates, model, source path, template), so a template upgrade isn't taken for an edit."""
    i = text.find("## Your open questions")
    return text[i:] if i >= 0 else text


def prune_cache(folder: Path, keep: set[Path]) -> list[Path]:
    """Remove PDFs from a vault cache folder (slides/, tutorials/: gitignored copies of OneDrive files, rebuilt by
    link_slides.py / link_tutorials.py) that are no longer copies of a current source. Only *.pdf directly in it."""
    if not folder.is_dir():
        return []
    keep = {p.resolve() for p in keep}
    gone = [p for p in folder.glob("*.pdf") if p.resolve() not in keep]
    for p in gone:
        try:
            p.unlink()
        except PermissionError:                  # open in Obsidian: it goes on a later run
            pass
    return gone


def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def copy_locked(src: Path) -> Path:
    """Copy a file that may be open in Excel (which blocks Python's open()) to a temp path."""
    dst = Path(tempfile.mkdtemp(prefix="pa_")) / src.name
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Copy-Item -LiteralPath '{src}' -Destination '{dst}'"],
            check=True,
        )
    return dst


def file_uri(p: Path) -> str:
    """Clickable link from Obsidian to a OneDrive file."""
    from urllib.parse import quote
    return "file:///" + quote(str(p).replace("\\", "/"), safe="/:")


class Manifest:
    """Hash-keyed record of processed inputs, so re-runs skip unchanged files (zero tokens)."""

    def __init__(self, name: str):
        folder = STATE / "manifests"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"manifest_{name}.json"
        legacy = STATE / self.path.name                # where manifests lived before 2026-11-18
        if legacy.exists() and not self.path.exists():
            legacy.replace(self.path)
        self.data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    def done(self, key: str, h: str) -> bool:
        return self.data.get(key, {}).get("hash") == h

    def mark(self, key: str, h: str, **extra):
        self.data[key] = {"hash": h, "at": datetime.now(TZ).isoformat(timespec="seconds"), **extra}
        self.path.write_text(json.dumps(self.data, indent=2), "utf-8")


# No emojis anywhere in this project (rule, 2026-11-11). Pictographs, dingbat ticks/crosses, warning
# signs, ballot boxes, stopwatches, variation selectors. Typography (→ – ≈ ° · † ≤) is fine.
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u23E9-\u23FA\u231A\u231B\u2328\u23CF"
                   "\u21BB\u21BA\uFE0F\u200D\u20E3]")
EMOJI_WORDS = [("\u2705", "correct"), ("\U0001F7E1", "partial"), ("\u274C", "wrong"), ("\u2B1C", "not attempted"),
               ("\U0001F4D2", "deferred"), ("\U0001F512", "(locked)"), ("\U0001F4CE", "Attachment:"), ("\u2605", "(A)"),
               ("\u2610", "[ ]"), ("\u2713", "ok"), ("\u2714", "ok"), ("\u2717", "FAIL"), ("\u2718", "FAIL"),
               ("\u26A0", "WARN"), ("\u23F1", ""), ("\u23F3", ""), ("\u21BB", "")]   # escapes: no literal emojis in code


def no_emoji(text: str) -> str:
    """Replace known emojis with words and drop any others (used on AI output and generated pages)."""
    for e, w in EMOJI_WORDS:
        text = text.replace(e + "\uFE0F", w).replace(e, w)
    return EMOJI.sub("", text).replace("( ", "(").replace(" · min", " min")


def fill(template: str, **values) -> str:
    """Replace {name} placeholders without touching LaTeX braces like G_{dB}."""
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def scope_files(u: dict) -> list[Path]:
    """Official exam documents: OneNote scope pages (export_onenote.py) plus `sources.scope` globs, e.g. an exam
    guide from the Canvas modules mirror (canvas_sync.py)."""
    pages = sorted((STATE / "onenote" / u["id"] / "scope").glob("*.pdf"))
    return pages + [p for p in find_sources(u, "scope") if p not in pages]


# The per-unit exam plan (unit_path(u, "plan")) is written by people and Claude, never by scripts.


def unit_plan(u: dict) -> tuple[Path, str]:
    """(path, Summary section) of a unit's exam plan; the summary is '' if the file or section is missing.
    plan_study.py copies the summary into 00_dashboard/study_plan.md; nothing ever writes the plan itself
    (scaffold_vault.py only creates a starter when there is none)."""
    p = unit_path(u, "plan")
    if not p.exists():
        return p, ""
    m = re.search(r"^## Summary[^\n]*\n(.*?)(?=^## |\Z)", p.read_text("utf-8"), re.S | re.M)
    return p, m.group(1).strip() if m else ""


INTEL_RULES = {
    "mock": "Use it this way:\n"
            "- The official scope, format and conditions win every conflict. Never leave a topic out because the "
            "intel doesn't mention it.\n"
            "- Claims marked strong or likely: match the described paper's question types, layout, mark split and "
            "the procedures it says carried marks, as long as they are inside the scope. Weak claims: a light "
            "influence at most. Conflicting claims: ignore.\n"
            "- Never copy a described past question. Test the same skill in a new situation: new numbers, the "
            "reversed direction (given the result, find the input), a different topology or context.\n"
            "- Never mention the intel in the paper. In the solutions, a question it shaped gets "
            "'past-paper style (intel)' in its Source line.",
    "handbook": "Use its 'What it means for study' section to decide which formulas and worked methods get full "
                "detail, worked steps and a lookup entry (the 'print in full' items above all). Never drop or "
                "shorten content because the intel doesn't mention it, and don't cite the intel in the handbook.",
}


def intel_block(u: dict, use: str) -> str:
    """The unit's exam-intel note as a text block for a model call ('' if there is none). `use`: mock, handbook."""
    p = unit_path(u, "intel")
    if not p.exists():
        return ""
    md = re.sub(r"\A---\n.*?\n---\n", "", p.read_text("utf-8"), flags=re.S)
    md = re.sub(r"(?ms)^## (Suggested config notes|Items on file)\b.*?(?=^## |\Z)", "", md).strip()
    return ("EXAM INTEL (word on the street: unverified student reports about past papers, with each claim's "
            "confidence; it ranks below the official scope and outline). " + INTEL_RULES[use] + "\n\n" + md[:30000])


def write_if_absent(path: Path, text: str) -> bool:
    """Create a file only if it does not exist (never overwrite user-edited notes)."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return True
