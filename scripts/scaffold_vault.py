"""Create (never overwrite) the Obsidian vault structure for every configured unit.

Usage:
    python scripts/scaffold_vault.py
Safe to re-run: existing files are left untouched; new units/sources are added.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

import progress

from common import (ONEDRIVE_COURSEWORK, STATE, TZ, VAULT, exam_over, file_uri, find_sources, lecture_labels, load_units,
                    unit_link, unit_path, write_if_absent)
from paths import layout

CALLOUT_CSS = """/* pipeline_academic callouts: enable in Settings > Appearance > CSS snippets */
.callout[data-callout="mine"]     { --callout-color: 46, 160, 67;  --callout-icon: lucide-pen-line; }
.callout[data-callout="confused"] { --callout-color: 220, 80, 60;  --callout-icon: lucide-circle-help; }
.callout[data-callout="check"]    { --callout-color: 230, 160, 30; --callout-icon: lucide-alert-triangle; }
.callout[data-callout="answer"]   { --callout-color: 120, 120, 120; --callout-icon: lucide-eye-off; }
.callout[data-callout="trap"]     { --callout-color: 200, 60, 160; --callout-icon: lucide-zap; }
"""

HOW_TO = """---
source: pipeline_academic
---
# How to study (exam sprint)

## The block (90 min)
1. **Blurt (5 min).** Notes closed. Write everything you remember about the block's topic.
2. **Check (15 min).** Open the slides and your OneNote. Fix gaps in a different colour. The gaps are the point.
3. **Recall questions (20 min).** `recall/<UNIT>_L<n>.md`. Answer out loud or on paper *before* expanding anything.
4. **Problems (45 min).** Tutorial / quiz / mock-exam questions using the hint ladder below.
5. **Log (5 min).** Every miss goes into `practice/error_log.md`. Every stuck point goes into `stuck_log.md`.

## Hint ladder (no AI until step 4)
1. Attempt cold for 20–25 min. Closed book first, then open notes.
2. Stuck: write one line in `stuck_log.md` saying exactly what you can't do. Check your notes and slides.
3. +10 min: find a *similar* worked example (lecture/tutorial), not this problem's solution.
4. Then AI in **hint-only** mode (`pipeline_academic/prompts/hint_tutor.md` as a Gemini Gem): one hint at a time, never the working.
5. Only after a full attempt: compare with the solution and log every difference.

Why: unrestricted AI during practice raised practice scores but cut exam scores by 17%; hint-only AI did not (Bastani et al., PNAS 2025).

## Open-book and notes-sheet exams are not easier
- Looking things up costs time you won't have (~1.2 min per mark). Know the methods and look up only constants and long formulas.
- **Open-book units:** bring a *tabbed, indexed* binder. Build the index (`notes_sheet.md`) as you study, not the night before.
- **Restricted units (N × A4 sheets, see each unit's `exam.notes_sheet`):** writing the sheet *is* revision. Draft in `notes_sheet.md`, handwrite the final 2 days before.
- Put your **error-log traps** on the sheet. They're the mistakes you actually make.

## Evening spaced review (45 min)
Re-attempt, without looking, the recall questions you missed and the error-log items from the last 3 days, across all units.

## Exam simulation
No past papers exist, so use the generated mock exams (`practice/mock_exam_<n>.md`, made on request by `gen_practice_exam.py`). Time it strictly and allow only the permitted materials, then mark it honestly.
"""

ERROR_LOG = """---
unit: {code}
type: error log
source: handwritten
rank: 1
---
# {code} error log

The most valuable file in the system. One row per mistake. Re-attempt rows in the evening review; move the traps to your notes sheet.

| Date | Source (paper/tut Q) | Topic | What I did | Why it was wrong | Fix / rule | Re-tested ok |
|---|---|---|---|---|---|---|
"""

STUCK_LOG = """---
unit: {code}
type: stuck log
source: handwritten
rank: 1
---
# {code} stuck log

Write the exact thing you couldn't do *before* looking anything up, then what got you unstuck and which hint-ladder step it took.

| Date | Problem | Stuck on (exactly) | What unstuck me | Ladder step (1-5) |
|---|---|---|---|---|
"""


def hub_entries(u) -> list[tuple[str, str]]:
    """(note key, line) for the hub's Working files list, in study order: plan, attempt and log, exam room, reference."""
    x = u["exam"]
    return [
        ("plan", f"- {unit_link(u, 'plan', 'Exam plan')} (yours and Claude's; never overwritten by scripts)"),
        ("notes_sheet", f"- {unit_link(u, 'notes_sheet', 'Notes sheet / open-book index')} ({x.get('notes_sheet', '')})"),
        ("error_log", f"- {unit_link(u, 'error_log', 'Error log')} · {unit_link(u, 'stuck_log', 'Stuck log')} · "
                      f"{unit_link(u, 'past_papers', 'Practice & mock exams')}"),
        ("handbook", f"- {unit_link(u, 'handbook', 'Exam handbook')} (for the exam room; "
                     f"{unit_link(u, 'handbook_pdf', 'print PDF')})"),
        ("worked_examples", f"- {unit_link(u, 'worked_examples', 'Worked examples by type')} (attempt the problems first; "
                            f"{unit_link(u, 'worked_examples_pdf', 'print PDF')})"),
        ("outline", f"- {unit_link(u, 'outline', 'Official unit outline')} (learning outcomes, assessment, weekly schedule)"),
        ("canvas", f"- {unit_link(u, 'canvas', 'Canvas record')} (marks, marker comments, announcements)"),
        ("intel", f"- {unit_link(u, 'intel', 'Exam intel (word on the street)')} (unverified student reports, sorted by "
                  "trust; drop screenshots in the unit's `exam_info_research/` and run `ingest_intel.py`)"),
    ]


def unit_hub(u, lectures, practice, handwritten, marks) -> str:
    x = u["exam"]
    m = marks.get("units", {}).get(u["code"], {})
    labels = lecture_labels(lectures)
    L = ["---", f"unit: {u['code']}", "type: unit hub", f"exam: {x['start_dt']:%Y-%m-%dT%H:%M}", "source: pipeline_academic", "---",
         f"# {u['code']} {u['name']}", "",
         f"> [!important] Exam {x['start_dt']:%A %d %b %Y, %H:%M} · {x['venue']} · seat {x.get('seat')}",
         f"> {x['conditions']}. Materials: {'; '.join(x['materials'])}.",
         f"> Reading {x['reading_min']} min + writing {x['writing_min']} min. [Map]({x.get('map', '')})", ""]
    if m:
        L += [f"**Coursework banked:** {m['banked']:.1f} / {100 - m['exam_weight'] * 100:.0f} · "
              f"**Exam weight:** {m['exam_weight'] * 100:.0f}% · exam % needed for unit 80: "
              f"**{m['exam_needed']['80']:.0f}**, for 85: **{m['exam_needed']['85']:.0f}** "
              f"(see [[00_dashboard/marks|marks]])", ""]
    optional = {"handbook", "worked_examples", "canvas"}            # listed once they exist
    L += ["## Working files"] + [line for key, line in hub_entries(u)
                                 if key not in optional or unit_path(u, key).exists()] + [""]
    if handwritten:
        L += ["## Your handwritten notes (rank 1)"] + \
             [f"- [{p.name}]({file_uri(p)}) → transcription: [[units/{u['id']}/sources/handwritten_{p.stem}|handwritten_{p.stem}]]"
              for p in handwritten] + [""]
    L += ["## Lecture tracker",
          "Confidence 1 (lost) to 5 (could teach it). Update after each block; update the unit's `confidence` in config when the average shifts.", "",
          "| Lecture | Slides | Recall Qs | Blurted | Recall done | Confidence |", "|---|---|---|---|---|---|"]
    for p in lectures:
        lab = labels[p]
        slides = f"[[units/{u['id']}/slides/{p.name}\\|pdf]]" if layout(u) >= 2 else f"[pdf]({file_uri(p)})"
        L.append(f"| {lab} | {slides} | [[units/{u['id']}/recall/{u['code']}_{lab}\\|{u['code']}_{lab}]] | [ ] | [ ] | |")
    return "\n".join(L) + "\n"


def past_papers(u, practice) -> str:
    L = ["---", f"unit: {u['code']}", "type: practice log", "source: pipeline_academic", "---",
         f"# {u['code']} practice material", "",
         "> [!todo] No past papers exist for this unit. Generate a mock exam on request: "
         f"`python scripts\\gen_practice_exam.py --unit {u['code']}` (add `--research` to draw on textbooks and "
         "other universities' exams). Mocks land here as `mock_exam_<n>.md` + `_solutions.md`.", "",
         "## Log", "| Date | Paper / set | Time taken | Score | Weakest topic |", "|---|---|---|---|---|", "",
         "## Found on disk"]
    base = len(str(ONEDRIVE_COURSEWORK / u["onedrive_folder"])) + 1
    L += [f"- [ ] [{str(p)[base:]}]({file_uri(p)})" for p in practice] or ["- (none)"]
    return "\n".join(L) + "\n"


def notes_sheet(u) -> str:
    kind = u["exam"].get("notes_sheet", "")
    return (f"---\nunit: {u['code']}\ntype: notes sheet\nsource: handwritten\nrank: 1\n---\n# {u['code']} notes sheet: {kind}\n\n"
            "Draft here while you study; write the final version by hand 2 days before the exam.\n\n"
            "## Formulas (with when-to-use + units)\n\n## Methods / procedures (step lists)\n\n"
            "## My traps (from error_log)\n\n## Constants & conversions\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    marks = json.loads((STATE / "marks.json").read_text("utf-8")) if (STATE / "marks.json").exists() else {}
    created = []
    w = lambda path, text: created.append(path) if write_if_absent(path, text) else None

    w(VAULT / ".obsidian" / "snippets" / "pipeline_callouts.css", CALLOUT_CSS)
    w(VAULT / ".gitignore", ".obsidian/workspace*.json\n.obsidian/cache\n.trash/\n")
    w(VAULT / "00_dashboard" / "how_to_study.md", HOW_TO)
    units = load_units()
    home = ["# Home", "", f"_Scaffolded {datetime.now(TZ):%Y-%m-%d} by pipeline_academic._", "",
            "- [[00_dashboard/progress|Progress check-in]] · [[00_dashboard/recall_checks|Recall checks]] · "
            "[[00_dashboard/study_plan|Study plan]] · [[00_dashboard/marks|Marks & targets]] · "
            "[[00_dashboard/how_to_study|How to study]]", "", "## Units (by exam date)"]
    for u in units:
        home.append(f"- **{u['exam']['start_dt']:%a %d %b %H:%M}**: [[units/{u['id']}/_unit|{u['code']} {u['name']}]]")
    w(VAULT / "00_dashboard" / "Home.md", "\n".join(home) + "\n")

    active = [u for u in units if not exam_over(u)]      # sat units are frozen: no new files, no new lectures
    for u in active:
        lectures, practice = find_sources(u, "lectures"), find_sources(u, "practice")
        hand = find_sources(u, "handwritten")
        w(unit_path(u, "hub"), unit_hub(u, lectures, practice, hand, marks))
        w(unit_path(u, "notes_sheet"), notes_sheet(u))
        w(unit_path(u, "error_log"), ERROR_LOG.format(code=u["code"]))
        w(unit_path(u, "stuck_log"), STUCK_LOG.format(code=u["code"]))
        w(unit_path(u, "past_papers"), past_papers(u, practice))
        w(unit_path(u, "plan"), plan_starter(u))        # a starter only: the plan is then yours and Claude's
        for key in ("recall", "sources"):
            unit_path(u, key).mkdir(parents=True, exist_ok=True)
        if layout(u) >= 2:
            for key in ("outline", "handbook"):         # reference/ and exam_room/
                unit_path(u, key).parent.mkdir(parents=True, exist_ok=True)
    for c in ("concepts", "thesis", "templates"):
        (VAULT / c).mkdir(parents=True, exist_ok=True)
    note = progress.ensure_page(active)          # yours to tick; only ever appended to
    if note:
        created.append(progress.PAGE)
        print(f"  progress.md: {note}")

    print(f"Vault: {VAULT}")
    print("\n".join(f"  + {p.relative_to(VAULT)}" for p in created) or "  (nothing new)")


PLAN_STARTER = """---
unit: {code}
type: exam plan
share: never
created: {today}
authors:
  - "scaffold_vault.py (starter skeleton only)"
---

# {code} exam plan (exam {exam})

> [!info] How this file works
> - No script overwrites it: `scaffold_vault.py` writes this starter once, and `plan_study.py` only copies the
>   **Summary** below into `00_dashboard/study_plan.md`.
> - Every section starts with a line saying who wrote it and from what. Your own words go in `> [!mine]`
>   callouts; whoever changes the plan (you, Claude, another model) adds a row to the change log.
> - Fact tags: [official] lecturer, Canvas or outline · [config] unit config · [intel: confidence] word on the
>   street · [notes p.N] your handwritten notes · [Claude] / [model id] a model's judgement · [me] you.

## Summary
_Starter, no plan yet. Ask Claude to draft one from the exam info, your notes and the recall files._

## Facts this plan rests on

## Where you start

## Priorities

## Schedule

## Binder / notes sheet

## In the exam

## Open questions

## Progress log
> [!mine]
> (your notes as you go: what you did, what's still shaky)

## Change log
| Date | Who | What |
|---|---|---|
| {today} | scaffold_vault.py | Starter skeleton |
"""


def plan_starter(u: dict) -> str:
    return PLAN_STARTER.format(code=u["code"], today=f"{datetime.now(TZ):%Y-%m-%d}",
                               exam=f"{u['exam']['start_dt']:%a %d %b %Y %H:%M}")


if __name__ == "__main__":
    main()
