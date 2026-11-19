"""Add exam-pace time allocations to every exam-style question: "[6 marks]" → "[6 marks, 12 min]".

Pace per unit = exam.writing_min / exam.total_marks (override with exam.minutes_per_mark). ABCD1001's
final is 60 marks in 120 min = 2 min/mark. Applied to:
  - units/<id>/recall/*.md          "## Exam-style problems": each [N marks] gets its time, plus a
                                    pace line with the section total ("4 problems: 22 marks ≈ 44 min")
  - units/<id>/practice/mock_exam_<n>.md   "## Question k (N marks)" headings get their time
Idempotent: re-running (e.g. after correcting total_marks) recomputes. Zero AI calls; refresh.ps1 and
nightly_ai.ps1 run it.

Usage:
    python scripts/pace_recall.py [--unit ABCD1001]
"""
from __future__ import annotations

import argparse
import re
import sys

from common import ALLOW_SAT_HELP, work_units
from common import load_units, unit_path

SW = chr(0x23F1)                    # the stopwatch used by an earlier format (built at runtime: no emoji in source)
OLD = r"(?:\s*(?:·\s*" + SW + r"|,)\s*[\d.]+\s*min)?"   # earlier formats, so re-runs convert them
MARKS = re.compile(r"\[(\d+(?:\.\d+)?)\s*marks?" + OLD + r"\]", re.I)
HEAD = re.compile(r"^(## Question \d+\s*)\((\d+(?:\.\d+)?)\s*marks?" + OLD + r"\)", re.I | re.M)
PACE_LINE = re.compile(r"^> \[!info\] (?:" + SW + r" )?Exam pace.*(?:\r?\n)?", re.M)


def pace(u: dict) -> tuple[float, str]:
    ex = u["exam"]
    if ex.get("minutes_per_mark"):
        mpm = float(ex["minutes_per_mark"])
        return mpm, f"{mpm:g} min/mark"
    total = float(ex.get("total_marks") or 100)
    mpm = float(ex.get("writing_min", 120)) / total
    return mpm, f"{mpm:g} min/mark ({total:g} marks in {ex.get('writing_min', 120)} min" + \
        (")" if ex.get("total_marks") else ", total marks assumed)")


def fmt_min(x: float) -> str:
    return f"{round(x):d}" if x >= 3 else f"{x:.1f}".rstrip("0").rstrip(".")


TOTAL_LINE = re.compile(r"^[ \t]*(?:" + SW + r" )?\*\*Problem total:.*(?:\r?\n)?", re.M)
PROBLEM = re.compile(r"^(?=\d+\.\s)", re.M)


def tag(n: float, mpm: float) -> str:
    return f"[{n:g} mark{'' if n == 1 else 's'}, {fmt_min(n * mpm)} min]"


def pace_recall(md: str, mpm: float, desc: str) -> str:
    m = re.search(r"^## Exam-style problems[^\n]*\n", md, re.M)
    if not m:
        return md
    end = re.search(r"^## ", md[m.end():], re.M)
    end = m.end() + (end.start() if end else len(md) - m.end())
    sec = TOTAL_LINE.sub("", PACE_LINE.sub("", md[m.end():end]))
    chunks = PROBLEM.split(sec)
    out, totals = [], []
    for ch in chunks:
        if not re.match(r"\d+\.\s", ch):                 # text before the first problem
            out.append(ch)
            continue
        q, sep, rest = ch.partition("> [!answer]")        # only the question carries mark tags
        marks = [float(x) for x in MARKS.findall(q)]
        q = MARKS.sub(lambda mm: tag(float(mm.group(1)), mpm), q)
        if marks:
            totals.append(sum(marks))
            if len(marks) > 1:                            # multi-part: one allocation for the whole problem
                q = q.rstrip("\n") + f"\n**Problem total: {sum(marks):g} marks, {fmt_min(sum(marks) * mpm)} min**\n"
        out.append(q + sep + rest)
    total = sum(totals)
    line = (f"> [!info] Exam pace: {desc}. These {len(totals)} problems: {total:g} marks ≈ "
            f"{fmt_min(total * mpm)} min. Time yourself and stop at each allocation; that's the exam skill.\n\n")
    return md[:m.end()] + "\n" + line + "".join(out).lstrip("\n") + md[end:]


def pace_mock(md: str, mpm: float) -> str:
    return HEAD.sub(lambda mm: f"{mm.group(1)}({mm.group(2)} marks, {fmt_min(float(mm.group(2)) * mpm)} min)", md)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    args = ap.parse_args()
    for u in work_units(args.unit, args.allow_sat):
        mpm, desc = pace(u)
        changed = 0
        for p in sorted(unit_path(u, "recall").glob("*.md")):
            md = p.read_text("utf-8")
            new = pace_recall(md, mpm, desc)
            if new != md:
                p.write_text(new, "utf-8")
                changed += 1
        for p in sorted(unit_path(u, "practice").glob("mock_exam_[0-9]*.md")):
            if p.stem.endswith("_solutions"):
                continue
            md = p.read_text("utf-8")
            new = pace_mock(md, mpm)
            if new != md:
                p.write_text(new, "utf-8")
                changed += 1
        print(f"  {u['code']}: {desc}; {changed} file(s) updated")


if __name__ == "__main__":
    main()
