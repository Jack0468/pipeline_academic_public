"""Shared course notes: the classmates' tool. Record your marks on a shared mock exam, report an error, and see how
the class did. Standard library only (Python 3.10 or newer), so it runs straight from the shared folder.

    python peer.py where                                  the shared folder it found, and your token
    python peer.py record ABCD1003 1                      enter your marks for mock exam 1, part by part
    python peer.py record ABCD1003 1 --from marks.csv     submit a filled-in results template instead
    python peer.py report ABCD1003 1 Q3 "Part (b): ..."   report an error in a question or its solution
    python peer.py results ABCD1003 1                     the class results next to yours
    python peer.py token [--set abcd1234]                 show your token, or reuse the one from another machine

Options for every command: --share FOLDER (if the shared folder isn't found), --inbox FOLDER (a separate
submissions folder), --session 2026-S2 (if a unit has more than one session).

What a submission holds, per question part: your marks, the option you chose (multiple choice), the minutes you
used (optional), the month you sat it, how it was marked, and a random token made on your machine. No names.
OneDrive still shows who created each file in the submissions folder, so it is pseudonymous, not anonymous.

The pipeline's share_vault.py uses this same file for the results templates, question IDs and class results, and
copies it into the share on every export. Design: pipeline_academic, docs/REFACTOR_PLAN.md, "Peer share".
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SHARE_NAME = "usyd-course-notes"
MARKER = ".peer_export"                 # written by share_vault.py; identifies the shared folder
INBOX = "_submissions"                  # the only folder classmates can write to
MIN_N = 5                               # nothing but the count is published below this many attempts
MAX_FILE_BYTES = 200_000
SEARCH_DEPTH = 3
SEARCH_LIMIT = 5000                     # folders visited per OneDrive root, at most
COLUMNS = ["unit", "session", "mock", "question_id", "question", "part", "version", "marks_available",
           "minutes_allocated", "multiple_choice", "marks_awarded", "option_chosen", "minutes_used", "sat",
           "marked_by", "token"]
ENTRY_COLUMNS = COLUMNS[COLUMNS.index("marks_awarded"):]
MARKED_BY = ("self", "ai", "tutor")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{4,32}$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
SESSION = re.compile(r"^\d{4}-S\w+$")


class PeerError(Exception):
    """A problem the person can fix; the message says how."""


# ---------------------------------------------------------------------------------------------------------------
# Finding the shared folder

def is_share(p: Path) -> bool:
    return (p / MARKER).is_file()


def _registry_roots() -> list[Path]:
    """OneDrive account folders from the Windows registry (business and personal accounts)."""
    if os.name != "nt":
        return []
    import winreg
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as accounts:
            for i in range(winreg.QueryInfoKey(accounts)[0]):
                try:
                    with winreg.OpenKey(accounts, winreg.EnumKey(accounts, i)) as key:
                        out.append(Path(winreg.QueryValueEx(key, "UserFolder")[0]))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def onedrive_roots() -> list[Path]:
    """Every OneDrive folder on this machine (Windows, macOS, Linux), without duplicates."""
    found = [Path(os.environ[v]) for v in ("OneDriveCommercial", "OneDrive", "OneDriveConsumer") if os.environ.get(v)]
    found += _registry_roots()
    home = Path.home()
    found += sorted((home / "Library" / "CloudStorage").glob("OneDrive*"))
    found += sorted(home.glob("OneDrive*"))
    out: list[Path] = []
    for p in found:
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def search(root: Path, depth: int = SEARCH_DEPTH, limit: int = SEARCH_LIMIT) -> Path | None:
    """The shared folder inside one OneDrive root: the usual places first, then breadth-first, `depth` levels."""
    for p in (root / SHARE_NAME, root / "SHARED" / SHARE_NAME):
        if is_share(p):
            return p
    level, seen = [root], 0
    for _ in range(depth):
        nxt = []
        for d in level:
            try:
                entries = [e for e in os.scandir(d) if e.is_dir() and not e.name.startswith((".", "$"))]
            except OSError:
                continue
            for e in entries:
                p = Path(e.path)
                if is_share(p):
                    return p
                nxt.append(p)
                seen += 1
                if seen >= limit:
                    return None
        level = nxt
    return None


def find_share(explicit: str | None = None, here: Path | None = None, cached: str | None = None,
               roots: Iterable[Path] | None = None) -> Path | None:
    """The shared folder: --share, else SHARE_PATH, else the folder this file is in, else the cached location, else
    a search of every OneDrive folder. An explicit location wins even if it is wrong, so the error names it."""
    for given in (explicit, os.environ.get("SHARE_PATH")):
        if given:
            p = Path(given).expanduser()
            return p if is_share(p) else None
    for p in (here, Path(cached) if cached else None):
        if p and is_share(p):
            return p
    for root in onedrive_roots() if roots is None else roots:
        hit = search(root)
        if hit:
            return hit
    return None


def inbox_root(share: Path, explicit: str | None = None) -> Path:
    given = explicit or os.environ.get("SHARE_INBOX")
    return Path(given).expanduser() if given else share / INBOX


# ---------------------------------------------------------------------------------------------------------------
# This person's settings: token and the cached share location (never inside the share)

def settings_dir() -> Path:
    if os.environ.get("PEER_HOME"):
        return Path(os.environ["PEER_HOME"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / SHARE_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / SHARE_NAME


def load_settings() -> dict:
    p = settings_dir() / "settings.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    d = settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps(s, indent=1), "utf-8")


def token(s: dict) -> str:
    """This person's token, made once on their machine (8 random hex characters)."""
    if not s.get("token"):
        s["token"] = secrets.token_hex(4)
        save_settings(s)
    return s["token"]


# ---------------------------------------------------------------------------------------------------------------
# Mock papers: questions, parts and stable IDs

QUESTION = re.compile(r"^## Question (\d+)[^\n]*\n?.*?(?=^## Question |^# |\Z)", re.S | re.M)
HEAD_MARKS = re.compile(r"\(([\d.]+)\s*marks?(?:,\s*([\d.]+)\s*min)?", re.I)
PART_START = re.compile(r"^\s*(?:#{2,4}\s*)?\**\(([a-z]|i{1,3}|iv|vi{0,3}|ix|x)\)\**\s")   # (a) or ### (a) Title
PART_MARKS = re.compile(r"\[([\d.]+)\s*marks?\b[^\]\n]*\]", re.I)                         # [4 marks], [2 marks, 8 min]
GROUP_HEADING = re.compile(r"^\s*(#|\*\*)")
OPTION = re.compile(r"^\s*\**\(?([A-H])[.)]\**\s")
ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@dataclass(frozen=True)
class Part:
    question: int
    part: str                      # "" for a question without parts, else "a", "c.ii"
    marks: float
    minutes: float | None
    multiple_choice: bool = False


def question_blocks(md: str) -> dict[int, str]:
    """'## Question k' blocks of a paper or its solutions, by question number."""
    return {int(m.group(1)): m.group(0) for m in QUESTION.finditer(md)}


def front_matter(md: str) -> dict[str, str]:
    m = FRONT.match(md.replace("\r\n", "\n"))
    if not m:
        return {}
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in m.group(1).splitlines()) if v}


def _next_label(tok: str, letter: str) -> tuple[str, str] | None:
    """(letter, roman) after a part marker; (i) is a letter only straight after (h), likewise (v) and (x)."""
    if tok in ROMAN and not (letter and len(tok) == 1 and ord(tok) == ord(letter) + 1):
        return (letter, tok)
    return (tok, "") if len(tok) == 1 else None


def _part_marks(block: str) -> dict[str, float]:
    """Marks per part label from '[n marks]' tags, in paper order. Several tags inside one part (marks per bullet)
    add up."""
    marks: dict[str, float] = {}
    letter = roman = ""
    for line in block.splitlines()[1:]:
        start = PART_START.match(line)
        if start:
            nxt = _next_label(start.group(1), letter)
            if nxt:
                letter, roman = nxt
        elif GROUP_HEADING.match(line):           # '**Part 2: Reflector [3 marks]**' is a total, not a part's marks
            continue
        label = f"{letter}.{roman}" if letter and roman else letter or roman
        for m in PART_MARKS.finditer(line):
            if label:
                marks[label] = marks.get(label, 0.0) + float(m.group(1))
    for parent in [lab for lab in marks if "." not in lab]:       # (c) [8 marks] and also (c)(i), (c)(ii)
        kids = [lab for lab in marks if lab.startswith(parent + ".")]
        if kids and math.isclose(sum(marks[k] for k in kids), marks[parent]):
            del marks[parent]
        else:
            for k in kids:
                del marks[k]
    return marks


def parse_question(k: int, block: str) -> tuple[list[Part], str]:
    """The parts of one question and a warning ('' if none). Falls back to the whole question when the part marks
    are missing or don't add up to the heading's marks."""
    head = HEAD_MARKS.search(block.splitlines()[0] if block else "")
    if not head:
        return [], f"Q{k}: no marks in the heading, left out of the results template"
    total = float(head.group(1))
    minutes = float(head.group(2)) if head.group(2) else None
    mc = sum(1 for ln in block.splitlines() if OPTION.match(ln)) >= 2
    marks = _part_marks(block)
    if mc or not marks:
        return [Part(k, "", total, minutes, mc)], ""
    if not math.isclose(sum(marks.values()), total):
        return [Part(k, "", total, minutes)], (f"Q{k}: part marks add up to {sum(marks.values()):g}, not {total:g}; "
                                               "recorded as one question")
    return [Part(k, lab, mk, minutes * mk / total if minutes else None) for lab, mk in marks.items()], ""


def parse_paper(md: str) -> tuple[list[Part], list[str]]:
    parts, warnings = [], []
    for k, block in sorted(question_blocks(md).items()):
        ps, warn = parse_question(k, block)
        parts += ps
        if warn:
            warnings.append(warn)
    return parts, warnings


def short_hash(*items: str) -> str:
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()[:6]


def mock_uid(code: str, session: str, number: int, seed: str) -> str:
    """A mock's stable ID: unit, session (2026-S2), mock number and generation seed. Never change this function:
    classmates' submissions refer to its output (pinned in tests/test_peer.py)."""
    return short_hash(code.upper(), session, str(number), str(seed))


def text_version(block: str) -> str:
    """Short hash of a question's text, ignoring blank lines, separators and spacing: a new version on any edit."""
    lines = [" ".join(ln.split()) for ln in block.replace("\r\n", "\n").splitlines()]
    return short_hash("\n".join(ln for ln in lines if ln and ln != "---"))


def seed_of(solutions_md: str, paper_md: str) -> str:
    """The mock's generation seed (solutions front matter), else its generation date, else ''."""
    fs, fp = front_matter(solutions_md), front_matter(paper_md)
    return fs.get("seed") or fp.get("seed") or fp.get("generated") or fs.get("generated") or ""


def label(p: dict | Part) -> str:
    """'Q1(c)(ii)' for display."""
    q, part = (p.question, p.part) if isinstance(p, Part) else (p["question"], p["part"])
    return f"Q{q}" + "".join(f"({x})" for x in part.split(".") if x)


# ---------------------------------------------------------------------------------------------------------------
# Results template and submissions (CSV)

def _num(x: float | None) -> str:
    return "" if x is None else f"{round(x, 1):g}"


def template_rows(code: str, session: str, number: int, paper_md: str, solutions_md: str = "") -> tuple[list[dict],
                                                                                                       list[str]]:
    """One row per question part, with the IDs filled in and the entry columns blank."""
    uid = mock_uid(code, session, number, seed_of(solutions_md, paper_md))
    blocks = question_blocks(paper_md)
    parts, warnings = parse_paper(paper_md)
    rows = [{"unit": code, "session": session, "mock": str(number), "question_id": f"{uid}-Q{p.question}",
             "question": str(p.question), "part": p.part, "version": text_version(blocks[p.question]),
             "marks_available": _num(p.marks), "minutes_allocated": _num(p.minutes),
             "multiple_choice": "yes" if p.multiple_choice else "", **dict.fromkeys(ENTRY_COLUMNS, "")}
            for p in parts]
    return rows, warnings


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def read_csv(text: str) -> list[dict]:
    """Rows of a template or submission. Accepts a byte-order mark and the ; or tab separators Excel may use."""
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    sep = max((",", ";", "\t"), key=first.count)
    rows = csv.DictReader(io.StringIO(text), delimiter=sep)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]


def _float(s: str) -> float | None:
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


@dataclass
class Submission:
    token: str
    sat: str
    marked_by: str
    answers: dict[tuple[str, str], dict] = field(default_factory=dict)   # (question_id, part) -> awarded/option/min
    old_version: int = 0                                                  # rows for an earlier version of a question


def _check_row(r: dict, t: dict) -> tuple[dict, list[str]]:
    """One validated answer, and the problems with it."""
    errs, where = [], label(t)
    awarded = None
    if r.get("marks_awarded"):
        awarded = _float(r["marks_awarded"])
        if awarded is None or not 0 <= awarded <= float(t["marks_available"]):
            errs.append(f"{where}: marks {r['marks_awarded']!r} must be between 0 and {t['marks_available']}")
    option = r.get("option_chosen", "").upper()
    if option and not re.fullmatch(r"[A-H]", option):
        errs.append(f"{where}: option {option!r} must be one letter A-H")
    minutes = None
    if r.get("minutes_used"):
        minutes = _float(r["minutes_used"])
        if minutes is None or not 0 < minutes <= 600:
            errs.append(f"{where}: minutes {r['minutes_used']!r} must be a number from 0 to 600")
    return {"awarded": awarded, "option": option, "minutes": minutes}, errs


def validate(rows: list[dict], template: list[dict], fallback_token: str = "") -> tuple[Submission | None, list[str]]:
    """A submission checked against the mock's template. Any problem rejects the whole file, with the reasons."""
    known = {(t["question_id"], t["part"]): t for t in template}
    first = {c: next((r.get(c, "") for r in rows if r.get(c)), "") for c in ("token", "sat", "marked_by")}
    tok = first["token"] or fallback_token
    errs = [] if TOKEN.match(tok) else [f"token {tok!r} must be 4-32 letters, digits, - or _"]
    if first["sat"] and not MONTH.match(first["sat"]):
        errs.append(f"sat {first['sat']!r} must be a year and month, like 2026-11")
    if first["marked_by"] and first["marked_by"].lower() not in MARKED_BY:
        errs.append(f"marked_by {first['marked_by']!r} must be one of {', '.join(MARKED_BY)}")
    sub = Submission(tok, first["sat"], first["marked_by"].lower())
    for r in rows:
        t = known.get((r.get("question_id", ""), r.get("part", "")))
        if t is None:
            errs.append(f"unknown question {r.get('question_id')!r} part {r.get('part')!r} (not in this mock)")
        elif r.get("version") and r["version"] != t["version"]:
            sub.old_version += 1
        else:
            answer, problems = _check_row(r, t)
            errs += problems
            sub.answers[(t["question_id"], t["part"])] = answer
    return (None if errs else sub), errs


def collect(folder: Path, template: list[dict]) -> tuple[list[Submission], list[str]]:
    """Valid submissions in one mock's submissions folder (newest file per token), and why others were rejected."""
    best: dict[str, tuple[float, Submission]] = {}
    rejected = []
    for f in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                rejected.append(f"{f.name}: larger than {MAX_FILE_BYTES // 1000} KB")
                continue
            sub, errs = validate(read_csv(f.read_bytes().decode("utf-8", errors="replace")), template, f.stem)
            mtime = f.stat().st_mtime
        except (OSError, csv.Error) as e:
            rejected.append(f"{f.name}: unreadable ({e})")
            continue
        if sub is None:
            more = f" (+{len(errs) - 3} more)" if len(errs) > 3 else ""
            rejected.append(f"{f.name}: " + "; ".join(errs[:3]) + more)
        elif sub.token not in best or mtime > best[sub.token][0]:
            best[sub.token] = (mtime, sub)
    return [s for _, s in best.values()], rejected


# ---------------------------------------------------------------------------------------------------------------
# Class results (aggregates only)

def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mx, my = _mean(xs), _mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def _scores(sub: Submission, template: list[dict]) -> dict[str, tuple[float, float]]:
    """(awarded, available) per question, over the parts this person attempted."""
    out: dict[str, tuple[float, float]] = {}
    for t in template:
        a = sub.answers.get((t["question_id"], t["part"]))
        if a and a["awarded"] is not None:
            aw, av = out.get(t["question_id"], (0.0, 0.0))
            out[t["question_id"]] = (aw + a["awarded"], av + float(t["marks_available"]))
    return out


def _part_stats(t: dict, subs: list[Submission], min_n: int) -> dict:
    answers = [s.answers.get((t["question_id"], t["part"])) for s in subs]
    done = [a for a in answers if a and a["awarded"] is not None]
    n, marks = len(done), float(t["marks_available"])
    options: dict[str, int] = {}
    for a in done:
        if a["option"]:
            options[a["option"]] = options.get(a["option"], 0) + 1
    shown = n >= min_n
    return {"part": t["part"], "label": label(t), "marks": marks, "n": n,
            "facility": _mean([a["awarded"] / marks for a in done]) if shown and marks else None,
            "options": dict(sorted(options.items())) if shown and options else None}


def _flags(q: dict) -> list[str]:
    out = []
    f, d = q["facility"], q["discrimination"]
    if f is not None and f < 0.25:
        out.append("few get this right: check the question and its solution")
    if f is not None and f > 0.95:
        out.append("nearly everyone gets this right")
    if d is not None and d < 0.1:
        out.append("stronger students do no better here: check for an ambiguity or an error")
    if q["minutes_used"] and q["minutes_allocated"] and q["minutes_used"] > 1.5 * q["minutes_allocated"]:
        out.append("takes much longer than its time allocation")
    if q["errata"]:
        out.append(f"{q['errata']} error report(s), being checked")
    return out


def _question_stats(qid: str, rows: list[dict], subs: list[Submission], scores: list[dict], min_n: int,
                    errata: int) -> dict:
    marks = sum(float(t["marks_available"]) for t in rows)
    mine = [(s[qid], s) for s in scores if qid in s]
    n = len(mine)
    rest_x, rest_y = [], []
    for (aw, av), s in mine:
        other_aw = sum(v[0] for k, v in s.items() if k != qid)
        other_av = sum(v[1] for k, v in s.items() if k != qid)
        if av and other_av:
            rest_x.append(aw / av)
            rest_y.append(other_aw / other_av)
    minutes = [a["minutes"] for sub in subs for t in rows
               if (a := sub.answers.get((t["question_id"], t["part"]))) and a["minutes"] is not None]
    alloc = sum(_float(t["minutes_allocated"]) or 0 for t in rows) or None
    q = {"question": int(rows[0]["question"]), "question_id": qid, "marks": marks, "n": n,
         "facility": _mean([aw / av for (aw, av), _ in mine if av]) if n >= min_n else None,
         "discrimination": _pearson(rest_x, rest_y) if len(rest_x) >= min_n else None,
         "minutes_used": _mean(minutes) if len(minutes) >= min_n else None, "minutes_allocated": alloc,
         "errata": errata, "parts": [_part_stats(t, subs, min_n) for t in rows if t["part"]]}
    q["flags"] = _flags(q)
    return q


def aggregate(template: list[dict], subs: list[Submission], errata: dict[int, int] | None = None,
              min_n: int = MIN_N) -> dict:
    """Class results for one mock. Below min_n attempts a question shows only its count."""
    errata = errata or {}
    t0 = template[0] if template else {}
    scores = [_scores(s, template) for s in subs]
    by_q: dict[str, list[dict]] = {}
    for t in template:
        by_q.setdefault(t["question_id"], []).append(t)
    totals = [sum(a for a, _ in sc.values()) / sum(v for _, v in sc.values()) for sc in scores
              if sum(v for _, v in sc.values())]
    marked: dict[str, int] = {}
    for s in subs:
        marked[s.marked_by or "not given"] = marked.get(s.marked_by or "not given", 0) + 1
    return {"unit": t0.get("unit", ""), "session": t0.get("session", ""), "mock": t0.get("mock", ""),
            "mock_uid": t0.get("question_id", "").split("-Q")[0], "min_n": min_n, "participants": len(subs),
            "marked_by": dict(sorted(marked.items())),
            "mean": _mean(totals) if len(totals) >= min_n else None,
            "older_version_rows": sum(s.old_version for s in subs),
            "questions": [_question_stats(qid, rows, subs, scores, min_n, errata.get(int(rows[0]["question"]), 0))
                          for qid, rows in by_q.items()]}


def _pct(x: float | None) -> str:
    return "" if x is None else f"{100 * x:.0f}%"


def _options(counts: dict[str, int] | None) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items()) if counts else ""


def class_results_md(agg: dict, generated: str) -> str:
    n, k = agg["participants"], agg["min_n"]
    marked = ", ".join(f"{v} {key}-marked" for key, v in agg["marked_by"].items()) or "none yet"
    L = ["---", f"unit: {agg['unit']}", "type: class results", f"mock: {agg['mock']}", f"mock_uid: {agg['mock_uid']}",
         "source: peer submissions", f"generated: {generated}", "---", "",
         f"# {agg['unit']} mock exam {agg['mock']}: class results", "",
         f"> [!info] From {n} attempt(s) sent back by classmates ({marked}). A question's results show once at least "
         f"{k} people have attempted it; until then only the count shows. No individual results are published.", ""]
    if agg["mean"] is not None:
        L += [f"Class mean over the parts attempted: {_pct(agg['mean'])}.", ""]
    L += ["| Question | Marks | Attempts | Class mean | Minutes used / allocated | Discrimination | Notes |",
          "|---|---|---|---|---|---|---|"]
    for q in agg["questions"]:
        mins = f"{q['minutes_used']:.0f} / {q['minutes_allocated']:g}" if q["minutes_used"] is not None else ""
        disc = "" if q["discrimination"] is None else f"{q['discrimination']:.2f}"
        L.append(f"| Q{q['question']} | {q['marks']:g} | {q['n']} | {_pct(q['facility'])} | {mins} | {disc} | "
                 f"{'; '.join(q['flags'])} |")
    parts = [p for q in agg["questions"] for p in q["parts"]]
    if parts:
        L += ["", "## By part", "", "| Part | Marks | Attempts | Class mean | Options chosen |",
              "|---|---|---|---|---|"]
        L += [f"| {p['label']} | {p['marks']:g} | {p['n']} | {_pct(p['facility'])} | {_options(p['options'])} |"
              for p in parts]
    L += ["", "Discrimination: how well a question separates stronger from weaker attempts (the correlation with "
          "the rest of the paper). Near zero or negative usually means the question or its solution needs a look.",
          "", f"Add yours: `python peer.py record {agg['unit']} {agg['mock']}` from the top of the shared folder."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# Error reports

def errata_counts(folder: Path, mock: int) -> dict[int, int]:
    """Error reports per question number for one mock, from an errata folder."""
    out: dict[int, int] = {}
    for f in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            fm = front_matter(f.read_text("utf-8", errors="replace"))
        except OSError:
            continue
        m = re.match(r"Q?(\d+)", fm.get("question", ""), re.I)
        if fm.get("mock") == str(mock) and m:
            out[int(m.group(1))] = out.get(int(m.group(1)), 0) + 1
    return out


def write_report(inbox: Path, code: str, session: str, mock: int, question: str, text: str, tok: str,
                 now: datetime) -> Path:
    q = question.strip().upper()
    if not re.fullmatch(r"Q?\d+[A-Z().]*", q):
        raise PeerError(f"question {question!r}: give it like Q3 or Q3b")
    if not text.strip():
        raise PeerError("say what is wrong, e.g. \"Part (b): the answer should be 0.30 rad\"")
    q = q if q.startswith("Q") else "Q" + q
    path = inbox / code / session / "errata" / f"{tok}_{now:%Y%m%d-%H%M%S}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(["---", f"unit: {code}", f"session: {session}", f"mock: {mock}", f"question: {q}",
                               f"token: {tok}", f"date: {now:%Y-%m-%d}", "---", "", text.strip(), ""]), "utf-8")
    return path


# ---------------------------------------------------------------------------------------------------------------
# Commands

def mock_dir(share: Path, code: str, session: str) -> Path:
    return share / code / session / "mock_exams"


def resolve_session(share: Path, code: str, session: str | None) -> str:
    if session:
        return session
    unit = share / code
    found = sorted(p.name for p in unit.iterdir() if p.is_dir() and SESSION.match(p.name)) if unit.is_dir() else []
    if not found:
        units = sorted(p.name for p in share.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
        raise PeerError(f"no unit {code} in the shared folder; it has: {', '.join(units) or 'none'}")
    return found[-1]


def load_template(share: Path, code: str, session: str, mock: int) -> list[dict]:
    p = mock_dir(share, code, session) / f"mock_exam_{mock}_results_template.csv"
    if not p.exists():
        templates = mock_dir(share, code, session).glob("*_results_template.csv")
        have = sorted(f.name.split("_results")[0] for f in templates)
        raise PeerError(f"no results template for {code} mock {mock}; templates exist for: {', '.join(have) or 'none'}")
    return read_csv(p.read_text("utf-8"))


def _ask_until(ask: Callable[[str], str], prompt: str, ok: Callable[[str], bool], default: str = "") -> str:
    while True:
        answer = ask(prompt).strip() or default
        if ok(answer):
            return answer
        print("    not valid, try again")


def _is_mark(limit: float) -> Callable[[str], bool]:
    def ok(s: str) -> bool:
        x = _float(s) if s else 0.0
        return x is not None and 0 <= x <= limit
    return ok


def ask_marks(template: list[dict], ask: Callable[[str], str], today: datetime) -> list[dict]:
    """Interactive entry: when sat, how marked, then marks (and option, minutes) per part."""
    sat = _ask_until(ask, f"Month you sat it, YYYY-MM [{today:%Y-%m}]: ", lambda s: bool(MONTH.match(s)),
                     f"{today:%Y-%m}")
    by = _ask_until(ask, "Marked by self or ai [self]: ", lambda s: s.lower() in MARKED_BY, "self").lower()
    print("Marks per part. Leave blank for a part you did not attempt.")
    groups: dict[str, list[dict]] = {}
    for t in template:
        groups.setdefault(t["question"], []).append(t)
    rows = []
    for q, parts in groups.items():
        mine = []
        for t in parts:
            r = {**t, "sat": sat, "marked_by": by}
            if t["multiple_choice"]:
                r["option_chosen"] = _ask_until(ask, f"  {label(t)} option chosen (blank = skipped): ",
                                                lambda s: s == "" or bool(re.fullmatch(r"[A-Ha-h]", s))).upper()
            r["marks_awarded"] = _ask_until(ask, f"  {label(t)} marks out of {t['marks_available']}: ",
                                            _is_mark(float(t["marks_available"])))
            mine.append(r)
        if not parts[0]["multiple_choice"] and any(r["marks_awarded"] for r in mine):
            mine[0]["minutes_used"] = _ask_until(ask, f"  Minutes spent on Q{q} (blank to skip): ",
                                                 lambda s: s == "" or (_float(s) or 0) > 0)
        rows += mine
    return rows


def save_submission(rows: list[dict], template: list[dict], tok: str, inbox: Path, local: Path) -> tuple[Path, str]:
    """Validate, then write the person's file to the submissions folder and a local copy. -> (local copy, note)."""
    for r in rows:
        r["token"] = tok
    sub, errs = validate(rows, template, tok)
    if sub is None:
        raise PeerError("not saved:\n  " + "\n  ".join(errs))
    text = to_csv(rows)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(text, "utf-8")
    t0 = template[0]
    out = inbox / t0["unit"] / t0["session"] / f"mock_exam_{t0['mock']}" / f"{tok}.csv"
    replaced = out.exists()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, "utf-8")
    except OSError as e:
        return local, (f"could not write to the submissions folder ({e}). Ask the sharer for edit access to "
                       f"{INBOX}, or send them {local}")
    return local, f"{'replaced your earlier file' if replaced else 'sent'}: {out}"


def summary(rows: list[dict]) -> str:
    done = [r for r in rows if r.get("marks_awarded")]
    aw = sum(float(r["marks_awarded"]) for r in done)
    av = sum(float(r["marks_available"]) for r in done)
    return f"{aw:g}/{av:g} on the {len(done)} part(s) attempted" + (f" ({100 * aw / av:.0f}%)" if av else "")


def compare(agg: dict, mine: list[dict]) -> list[str]:
    """Your score per question next to the class mean."""
    lines, above, shown = [], 0, 0
    for q in agg["questions"]:
        rows = [r for r in mine if r["question_id"] == q["question_id"] and r.get("marks_awarded")]
        av = sum(float(r["marks_available"]) for r in rows)
        you = sum(float(r["marks_awarded"]) for r in rows) / av if av else None
        cls = q["facility"]
        if you is not None and cls is not None:
            shown += 1
            above += you > cls
        lines.append(f"  Q{q['question']:<3} you {_pct(you) or '-':>5}   class {_pct(cls) or '-':>5}   (n={q['n']})"
                     + (f"   {'; '.join(q['flags'])}" if q["flags"] else ""))
    if shown:
        lines.append(f"You are above the class mean on {above} of {shown} question(s) with results.")
    else:
        lines.append(f"No question has {agg['min_n']} attempts yet, so only the counts show.")
    return lines


def local_copy(code: str, session: str, mock: int) -> Path:
    return settings_dir() / "submissions" / code / session / f"mock_exam_{mock}.csv"


def _context(a: argparse.Namespace) -> tuple[dict, Path, Path]:
    s = load_settings()
    share = find_share(a.share, Path(__file__).resolve().parent, s.get("share"))
    if share is None:
        where = a.share or os.environ.get("SHARE_PATH")
        raise PeerError(f"{where} is not the shared folder (no {MARKER} in it)" if where else
                        "could not find the shared folder. In OneDrive on the web, open Shared, find "
                        f"'{SHARE_NAME}' and choose 'Add shortcut to My files', wait for it to sync, then try "
                        "again; or pass --share <folder>")
    if s.get("share") != str(share):
        s["share"] = str(share)
        save_settings(s)
    return s, share, inbox_root(share, a.inbox)


def cmd_where(a: argparse.Namespace) -> None:
    s, share, inbox = _context(a)
    print(f"shared folder: {share}\nsubmissions:   {inbox}\nyour token:    {token(s)}\nsettings:      {settings_dir()}")


def cmd_token(a: argparse.Namespace) -> None:
    s = load_settings()
    if a.set:
        if not TOKEN.match(a.set):
            raise PeerError("a token is 4-32 letters, digits, - or _")
        s["token"] = a.set
        save_settings(s)
    print(token(s))


def cmd_record(a: argparse.Namespace) -> None:
    s, share, inbox = _context(a)
    code = a.unit.upper()
    session = resolve_session(share, code, a.session)
    template = load_template(share, code, session, a.mock)
    if a.from_file:
        given = {(r.get("question_id"), r.get("part")): r for r in read_csv(Path(a.from_file).read_text("utf-8-sig"))}
        rows = [{**t, **{c: given.get((t["question_id"], t["part"]), {}).get(c, "") for c in ENTRY_COLUMNS}}
                for t in template]
        for r in rows:
            r["marked_by"] = r["marked_by"] or "self"
    else:
        print(f"{code} {session} mock exam {a.mock}: {len(template)} part(s)")
        rows = ask_marks(template, input, datetime.now())
    copy, note = save_submission(rows, template, token(s), inbox, local_copy(code, session, a.mock))
    print(f"{summary(rows)}\n{note}\nyour copy: {copy}")


def cmd_report(a: argparse.Namespace) -> None:
    s, share, inbox = _context(a)
    code = a.unit.upper()
    session = resolve_session(share, code, a.session)
    text = a.text or sys.stdin.read()
    path = write_report(inbox, code, session, a.mock, a.question, text, token(s), datetime.now())
    print(f"reported: {path}")


def cmd_results(a: argparse.Namespace) -> None:
    _, share, _ = _context(a)
    code = a.unit.upper()
    session = resolve_session(share, code, a.session)
    p = mock_dir(share, code, session) / f"mock_exam_{a.mock}_class_results.json"
    if not p.exists():
        raise PeerError(f"no class results for {code} mock {a.mock} yet; they appear after the next export")
    agg = json.loads(p.read_text("utf-8"))
    mine_path = local_copy(code, session, a.mock)
    mine = read_csv(mine_path.read_text("utf-8")) if mine_path.exists() else []
    print(f"{code} mock exam {a.mock}: {agg['participants']} attempt(s)")
    print("\n".join(compare(agg, mine)))
    if not mine:
        print(f"(no submission of yours on this machine: python peer.py record {code} {a.mock})")


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--share", help="the shared folder, if it isn't found automatically")
    common.add_argument("--inbox", help="a separate submissions folder (default: _submissions in the share)")
    common.add_argument("--session", help="e.g. 2026-S2 (default: the latest the unit has)")
    ap = argparse.ArgumentParser(prog="peer.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where", parents=[common], help="show the shared folder, submissions folder and your token")
    t = sub.add_parser("token", help="show your token, or --set one from another machine")
    t.add_argument("--set")
    for name, helptext in (("record", "record your marks on a mock"), ("results", "class results next to yours"),
                           ("report", "report an error in a question or solution")):
        p = sub.add_parser(name, parents=[common], help=helptext)
        p.add_argument("unit", help="unit code, e.g. ABCD1003")
        p.add_argument("mock", type=int, help="mock exam number")
    rec = sub.choices["record"]
    rec.add_argument("--from", dest="from_file", help="a filled-in results template (CSV)")
    rep = sub.choices["report"]
    rep.add_argument("question", help="e.g. Q3 or Q3b")
    rep.add_argument("text", nargs="?", help="what is wrong (or type it after the command, then Ctrl+Z / Ctrl+D)")
    return ap


COMMANDS = {"where": cmd_where, "token": cmd_token, "record": cmd_record, "report": cmd_report,
            "results": cmd_results}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = parser().parse_args(argv)
    try:
        COMMANDS[a.cmd](a)
    except PeerError as e:
        print(f"peer.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
