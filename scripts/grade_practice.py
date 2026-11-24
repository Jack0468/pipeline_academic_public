"""Mark your timed, self-annotated practice attempts against the reference problems and answers.

You work a lecture's exam-style problems under the time allocations, review in red after seeing the
answers (red never earns marks unless the page says otherwise), and scan to
`<unit OneDrive folder>/practice_q/` (glob `sources.practice_attempts`):
    L1_practice_1.pdf            → the "Exam-style problems" in recall/<CODE>_L1.md (+ that lecture's slides)
    mock_exam_1_attempt.pdf      → practice/mock_exam_1.md + mock_exam_1_solutions.md
For each new or changed attempt, one AI call writes units/<id>/practice_checks/<CODE>_<stem>.md:
marks per part (original ink only: red is your post-answer review and never earns marks; method marks,
carry-forward), the first error and its fix, whether your red-pen corrections are right,
answers to the questions you wrote to yourself, time used against
each allocation, recurring habits, and what to redo. Then it:
  - appends each part below full marks, and each habit, to practice/error_log.md (once each);
  - adds 1 to practice_blocks_done (or mocks_done) in 00_dashboard/progress.md, once per attempt;
  - regenerates 00_dashboard/practice_checks.md (every attempt: marks %, time used/allocated).

Usage:
    python scripts/grade_practice.py [--unit ABCD1001] [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import gemini
import pdf_tools
import progress
from common import ALLOW_SAT_HELP, work_units
from common import (PROMPTS, STATE, TZ, VAULT, Manifest, file_hash, file_uri, fill, find_sources, lecture_labels,
                    load_units, unit_path)
from grade_recall import parse_result

DEFAULT_GLOB = "practice_q/*.pdf"


PART = {"mc": "A", "a": "A", "secta": "A",                         # Section A: multiple choice
        "sa": "B", "short": "B", "written": "B", "ext": "B", "b": "B", "sb": "B", "sectb": "B"}  # B: short/extended answer


def blocks(md: str) -> dict[int, str]:
    """'## Question k' blocks of a mock paper or its solutions, by question number."""
    parts = re.split(r"(?=^## Question \d+)", md, flags=re.M)
    return {int(re.match(r"## Question (\d+)", p).group(1)): p for p in parts if re.match(r"## Question \d+", p)}


def mock_section(q_md: str, s_md: str, sec: str) -> tuple[str, str]:
    """Questions + solutions of one section ('# Section A …' in the paper; solutions are numbered only)."""
    m = re.search(rf"^# Section {sec}\b.*?(?=^# Section |\Z)", q_md, re.S | re.M)
    if not m:
        return q_md, s_md
    nums = sorted(blocks(m.group(0)))
    sols = blocks(s_md)
    return m.group(0), "\n".join(sols[k] for k in nums if k in sols)


def reference(u, att: Path, by_label: dict[str, Path]):
    """→ (title, reference text, slide PDFs, counter name) or None if the file name isn't recognised.
    Mock attempts: mock_exam_1_attempt.pdf / mock_1.pdf (whole paper), mock_1_mc.pdf / mock_1_a.pdf
    (Section A only), mock_1_sa.pdf / mock_1_ext.pdf / mock_1_b.pdf (Section B only). A name that lists both
    sections (mock_3_sa_mc.pdf, mock_3_mc_sa.pdf, mock_3_a_b.pdf) is the whole paper."""
    mm = re.match(r"mock(?:_exam)?_(\d+)((?:_[a-z]+)*)", att.stem, re.I)
    if mm:
        n = mm.group(1)
        named = {PART[t] for t in mm.group(2).lower().split("_") if t in PART}
        q = unit_path(u, "practice") / f"mock_exam_{n}.md"
        s = unit_path(u, "practice") / f"mock_exam_{n}_solutions.md"
        if not q.exists():
            return None
        q_md, s_md = q.read_text("utf-8"), (s.read_text("utf-8") if s.exists() else "")
        sec = named.pop() if len(named) == 1 else None          # none or both sections named: the whole paper
        if sec:
            q_md, s_md = mock_section(q_md, s_md, sec)
            title = f"mock exam {n} Section {sec}" + (" (multiple choice)" if sec == "A" else " (extended answer)")
        else:
            title = f"mock exam {n}"
        return title, q_md + "\n\nSOLUTIONS:\n\n" + s_md, [], ("practice" if sec else "mocks")   # a section = a practice block
    ml = re.match(r"(L\w+?)_practice", att.stem, re.I) or re.match(r"(L\w+?)(?:_|$)", att.stem, re.I)
    if not ml:
        return None
    hits = [lab for lab in by_label if lab.lower() == ml.group(1).lower()]
    if not hits:
        return None
    label = hits[0]
    rf = unit_path(u, "recall") / f"{u['code']}_{label}.md"
    if not rf.exists():
        return None
    text = rf.read_text("utf-8")
    sec = re.search(r"^## Exam-style problems.*?(?=^## (?!Exam-style)|\Z)", text, re.S | re.M)
    ref = sec.group(0) if sec else text
    slides = pdf_tools.slide_subset(by_label[label], pdf_tools.referenced_pages(ref))   # only the method pages cited
    if slides != by_label[label]:
        ref += ("\n\nNOTE: the slides attached are only the pages these problems cite (with a page either side). Cite "
                "ORIGINAL page numbers as above.")
    return f"{label} exam-style problems", ref, [slides], "practice"


def append_log(u, stem: str, title: str, d: dict, when: str) -> int:
    log = unit_path(u, "error_log")
    if not log.exists():
        return 0
    # re-marking an attempt replaces its earlier generated lines (they may hold superseded verdicts)
    text = "\n".join(l for l in log.read_text("utf-8").splitlines() if f"<!-- practice:{stem}:" not in l) + "\n"
    new = []
    for p in d.get("parts", []):
        key = f"practice:{stem}:{p.get('id')}"
        if p.get("verdict") in ("wrong", "partial") and key not in text:
            new.append(f"- {when} · **{title} {p.get('id')}** ({p.get('awarded', 0):g}/{p.get('max', 0):g}): "
                       f"{p.get('fix', '').strip()} <!-- {key} -->")
    for i, h in enumerate(d.get("habits", [])):
        key = f"practice:{stem}:habit{i}"
        if key not in text:
            new.append(f"- {when} · **habit** ({title}): {h} <!-- {key} -->")
    if new:
        head = "" if "## From practice checks" in text else "\n\n## From practice checks (appended by grade_practice.py)\n"
        log.write_text(text.rstrip() + head + "\n" + "\n".join(new) + "\n", "utf-8")
    return len(new)


def append_requests(u, stem: str, d: dict) -> int:
    """Notes you wrote for the AI ("for AI: put tables on the handbook") → a to-do list in handbook.md
    (append-only, once each; the handbook itself stays yours)."""
    g = unit_path(u, "handbook")
    reqs = [r for r in d.get("requests", []) if r and r.strip().lower() != "none"]
    if not reqs or not g.exists():
        return 0
    text = g.read_text("utf-8")
    new = [f"- [ ] {r.strip()} <!-- request:{stem}:{i} -->" for i, r in enumerate(reqs) if f"request:{stem}:{i}" not in text]
    if new:
        head = "" if "## To add (requests from your notes)" in text else "\n\n## To add (requests from your notes)\n"
        g.write_text(text.rstrip() + head + "\n" + "\n".join(new) + "\n", "utf-8")
    return len(new)


def bump(u, counter: str) -> bool:
    """+1 on practice_blocks_done / mocks_done in the unit's progress.md section."""
    if not progress.PAGE.exists():
        return False
    field = "practice_blocks_done" if counter == "practice" else "mocks_done"
    text = progress.PAGE.read_text("utf-8")
    m = re.search(rf"^## {u['code']}\b.*$", text, re.M)
    if not m:
        return False
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    sec, n = re.subn(rf"^({field}::\s*)(\d+)", lambda x: f"{x.group(1)}{int(x.group(2)) + 1}", text[m.end():end],
                     count=1, flags=re.M)
    if n:
        progress.PAGE.write_text(text[:m.end()] + sec + text[end:], "utf-8")
    return bool(n)


def dashboard() -> None:
    rows = []
    for u in load_units():
        for p in sorted(unit_path(u, "practice_checks").glob("*.md")):
            t = p.read_text("utf-8")
            fm = dict(re.findall(r"^(\w+): (.*)$", t.split("---", 2)[1], re.M)) if t.startswith("---") else {}
            rows.append(f"| {u['code']} | {fm.get('reference', '')} | {fm.get('attempt_date', '')} | "
                        f"{fm.get('marks', '')} | {fm.get('time', '')} | [[units/{u['id']}/practice_checks/{p.stem}|{p.stem}]] |")
    L = ["---", "source: generated", "generator: pipeline_academic/scripts/grade_practice.py", "---", "",
         "# Practice checks", "", "> [!tip] Work a lecture's exam-style problems against the time allocations, "
         "self-mark in red, scan to `practice_q/L<n>_practice_<k>.pdf` (or `mock_exam_<n>_attempt.pdf`), "
         "then run `scripts\\nightly_ai.ps1` or `grade_practice.py`.", "",
         "| Unit | Reference | Attempt | Marks | Time used / allocated | Check |", "|---|---|---|---|---|---|"]
    L += rows or ["| - | - | - | - | - | - |"]
    (VAULT / "00_dashboard" / "practice_checks.md").write_text("\n".join(L) + "\n", "utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    template = (PROMPTS / "grade_practice.md").read_text("utf-8")
    manifest = Manifest("practice_checks")
    for u in work_units(args.unit, args.allow_sat):
        if not (u.get("sources") or {}).get("practice_attempts"):
            u.setdefault("sources", {})["practice_attempts"] = [DEFAULT_GLOB]
        lectures = find_sources(u, "lectures")
        by_label = {lab: p for p, lab in lecture_labels(lectures).items()}
        for att in find_sources(u, "practice_attempts"):
            ref = reference(u, att, by_label)
            if not ref:
                print(f"  ? {u['code']}: {att.name}: no matching reference (expected L<n>_practice_<k>.pdf or "
                      "mock_exam_<n>_attempt.pdf, with that recall file or mock existing)")
                continue
            title, ref_text, slides, counter = ref
            # your note on the attempt, if any: same name, .log/.txt/.md (conditions, timing, what you skipped)
            note = next((att.with_suffix(x) for x in (".log", ".txt", ".md") if att.with_suffix(x).exists()), None)
            h, key = file_hash(att) + (f"+{file_hash(note)[:8]}" if note else ""), f"{u['code']}:{att.name}"
            out = unit_path(u, "practice_checks") / f"{u['code']}_{att.stem}.md"
            if not args.force and out.exists() and manifest.done(key, h):
                continue
            print(f"  {u['code']}: marking {att.name} against {title}" + (f" (with your note {note.name})" if note else ""))
            if args.dry_run:
                continue
            prompt = fill(template, unit_code=u["code"], unit_name=u["name"], what=title, title=title,
                          slides_note=("**The lecture slides**: the authority for methods and page references."
                                       if slides else "(No slides attached: use the reference solutions.)"))
            try:
                note_text = ([f"STUDENT'S NOTE ON THIS ATTEMPT ({note.name}): how it was sat. Use it for the timing "
                              "and treat questions it says were not attempted as not attempted.\n\n"
                              + note.read_text("utf-8", errors="replace").strip()] if note else [])
                body = gemini.generate([att] + slides, prompt, texts=[f"REFERENCE ({title}):\n\n{ref_text}"] + note_text,
                                       temperature=0.1)
            except gemini.DailyQuotaExhausted as e:
                print(f"STOPPED: {e}")
                return
            md, d = parse_result(body)
            when = datetime.fromtimestamp(att.stat().st_mtime, TZ).strftime("%Y-%m-%d")
            aw, av = d.get("awarded"), d.get("available")
            marks = f"{aw:g}/{av:g} ({100 * aw / av:.0f}%)" if aw is not None and av else ""
            used, alloc = d.get("used_min"), d.get("allocated_min")
            tm = (f"{used:g}/{alloc:g} min" if used is not None and alloc else (f"not recorded/{alloc:g} min" if alloc else ""))
            # your own reported time beats the AI's reading of the clock notes, and survives a re-mark
            prev = out.read_text("utf-8") if out.exists() else ""
            rep = re.search(r"^time_reported: *(.+)$", prev, re.M)
            if rep:
                tm = rep.group(1).strip()
            front = ["---", f"unit: {u['code']}", "type: practice check", f"reference: {title}", "source: practice-check",
                     f"attempt_date: {when}", f"marks: {marks}", f"time: {tm}"] +                     ([f"time_reported: {tm}"] if rep else []) + [f"model: {gemini.used_model()}",
                     f"attempt: \"{file_uri(att)}\""] + ([f"slides: \"{file_uri(slides[0])}\""] if slides else []) + \
                    ["---", "", f"> [!check] AI marking of your handwritten attempt. If a mark looks wrong, the slide "
                     f"page is the authority. [Your attempt]({file_uri(att)})", "", ""]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(front) + md + "\n", "utf-8")
            if not d:                          # no JSON summary: keep the text, but don't file it as done
                print(f"    WARN {out.name}: the reply has no readable JSON summary, so no marks, error-log entries "
                      "or counter bump. Saved as is; it is marked again on the next run.")
                continue
            first = not manifest.data.get(key)                      # count each attempt file once
            manifest.mark(key, h, out=str(out), marks=marks, counted=True)
            # per-part marks, kept as data so mock_log's generation logs can be joined with how you did on each
            # question (are replaced questions harder?)
            data_dir = STATE / "practice_check_data" / u["id"]
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / f"{att.stem}.json").write_text(json.dumps(     # d already holds used_min/allocated_min
                {**d, "unit": u["code"], "attempt": att.name, "reference": title, "attempt_date": when},
                indent=1, ensure_ascii=False), "utf-8")
            n_log = append_log(u, att.stem, title, d, when)
            n_req = append_requests(u, att.stem, d)
            bumped = first and bump(u, counter)
            print(f"    ok {out.name}: {marks or '?'}; time {tm or '?'}; {n_log} item(s) → error_log; {n_req} request(s) → handbook to-do"
                  + (f"; +1 {'practice_blocks_done' if counter == 'practice' else 'mocks_done'}" if bumped else ""))
    if not args.dry_run:
        dashboard()


if __name__ == "__main__":
    main()
