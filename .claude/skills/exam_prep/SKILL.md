---
name: exam_prep
description: The exam-preparation protocol for any university unit in pipeline_academic, from "the exam is coming" to a printed textbook or notes sheet, a dated plan and checked mock exams. Covers the folder conventions, the single source of truth for inputs (unit config `sources:` resolved by unit_inputs.py), the Canvas mirror, word on the street (ingest_intel.py), third-party notes (convert_reference.py), the per-unit exam plan with provenance, the open-book textbook or the restricted notes sheet, and mocks. Use whenever the user asks to prepare for, plan for, study for or make a mock for an exam in any subject, sets up a new unit or semester, says inputs or slides look wrong or out of date, drops new material into a unit folder, or asks "what should my plan be" for a unit.
---

# Exam prep protocol (any unit)

The same steps for every subject. Code never forks per unit: everything unit-specific lives in
`config/units/<unit id>.yaml` and the unit's folders. Run commands from the repo root with
the `pipeline_academic` conda env's Python. AI steps use agy (Gemini on the user's AI Pro quota).
Their costs are listed in docs/EXAM_SPRINT_SETUP.md.

## Two standing rules (since 2026-11-18)

- **A unit whose exam is over is frozen.** Nothing about it is regenerated or altered: recall files,
  transcriptions, the handbook and its PDF, mocks, Canvas data, slides, the vault files. Every content-writing
  script skips it (`common.work_units`) unless the user asks for that unit and the command gets `--allow-sat`. Never
  use a sat unit as a "regression check" for a build. The only exception is `reconstruct_exam.py`, which exists
  for after the exam and only adds new files.
- **The handbook is built only when the user says so, for the subject they name** (`--unit` is required). No batch
  job builds it.

## Where files go

Vault folders follow `docs/VAULT_STANDARD.md`; generated-file routing is in `docs/ARCHITECTURE.md`. Scripts get
locations from `scripts/paths.py` (`unit_path(u, key)`), and units on the standard have `vault_layout: 2`. Paths below
are for that layout: units not yet migrated keep `handbook.md`, `exam_intel.md`, `canvas.md` and `_unit_outline.md` at the
top of the unit folder. Run `check_vault.py --unit X` after changing a unit's files. Move superseded files to
`_archive/`; never leave them beside the live version.

## 0. Folder conventions (in the unit's OneDrive folder)

| Folder | What goes in | Read by | Config |
|---|---|---|---|
| `canvas/<module>/` | Canvas modules mirror (canvas_sync.py, never edit): **the ground truth for lectures and tutorials**, every module file | `sources.lectures`, `sources.practice`, `sources.tutorial_solutions` point here | the globs |
| `_superseded/` | old copies a unit's sources no longer use (adopt_sources.py --supersede, logged in MOVED.md) | nothing | none |
| `exam_info_research/` | word on the street: screenshots, saved posts, `.md` notes of what someone said (role, not name; when; which year) | ingest_intel.py | default, no config needed |
| `xtra_resources/` | third-party notes: shared formula sheets, condensed notes | mocks; handbook backbone after convert_reference.py | default, no config needed |
| `notes/` | the student's paper scans | extract_handwriting.py, once added to `sources.handwritten` | add the glob |

Other students' files (e.g. a lab partner's folder) are never sources.

## 1. Make the inputs right (single source of truth)

1. `scripts\canvas_sync.py --unit X`: marks, announcements, the modules mirror. Read `canvas.md`'s to-do of
   saved files no glob covers.
2. `scripts\unit_inputs.py --unit X`: every input, where it lives and who reads it, plus checks. Fix the config until
   there is **no CRITICAL** line, and look at every WARN and every "not read" PDF. The usual causes:
   - lecture files moved (fewer lecture PDFs than recall files);
   - two copies of one lecture (L2a/L2b): the Canvas mirror is the one source;
   - a unit still reading its own copies: point `sources.lectures`, `sources.practice` (tutorials) and
     `sources.tutorial_solutions` at `canvas/<module>/`, then `adopt_sources.py --unit X` (dry run). Ask the user
     whether to keep or regenerate recall files whose Canvas version differs (regenerating changes practice
     problems they may be using). Keeping: `--apply` (with `--map` for merged lectures), then `--supersede` the old
     copies, then `link_slides.py` and `link_tutorials.py`. A CONFLICT means the old copies were numbered
     differently: stop and show the user, never force it;
   - an exam guide or sample paper sitting outside every glob: official ones go in `sources.scope`; practice
     papers that aren't official (check the paper's own words) go in `sources.practice`.
3. Official exam facts (format, total marks, scope) go in `exam.format`, `exam.total_marks` and `exam.scope`,
   with the source in a comment. `total_marks` sets the pace in every time allocation.
4. Recall files current: `gen_recall.py --unit X`, `extract_handwriting.py --unit X`, `pace_recall.py`, `link_slides.py`
   (or `nightly_ai.ps1`).

## 2. Word on the street

Items in `exam_info_research/` → `scripts\ingest_intel.py --unit X` → `reference/exam_intel.md`. Hearsay always ranks below
official information, and an analysis of a post is never a second source. Mocks and the handbook draft read it
automatically. Check that the generator's first line says `exam intel: yes`.

## 3. Ask what the student has actually done

Never infer readiness from marks on AI-allowed work . Use their handwritten notes'
page index, recall and practice checks, the error log, and ask: which tutorials, which lectures, which notes.

## 4. The unit's exam plan: `units/<id>/exam_plan.md`

- No script writes it. `scaffold_vault.py` makes a starter when there is none; `plan_study.py` copies its
  **Summary** into `00_dashboard/study_plan.md`.
- Provenance on everything:
  - front matter `authors` and `inputs`;
  - an attribution line under each section;
  - fact tags: [official] [config] [marks.py] [intel: confidence] [notes p.N] [me] [Claude] [agy];
  - the student's own words in `[!mine]` callouts;
  - a change-log row for every edit, naming the model id or the person.
- Content:
  - facts with sources;
  - the starting point per lecture (notes, tutorials, recall problems);
  - priorities, with the calculation types first for calculation exams;
  - the method (problems with sources open, an index as you go, self-checks);
  - a block-by-block schedule with "done when";
  - what to print or what goes on the sheet;
  - exam-room technique, a cut list, open questions.

## 5. What goes into the exam room

Read `exam.conditions` and `exam.notes_sheet`.
- **Open book: a textbook, not all the slides.**
  1. If there is a third-party formula sheet or condensed notes, convert its formula pages:
     `convert_reference.py --unit X --pdf "xtra_resources/<file>.pdf" --pages <pages> --name <short> --author "<who>"`.
     Check page 1 of the output by hand.
  2. Give the backbone numbered sections (`## 1. Title (L5)`, one per lecture or problem type), the format the
     builder expects. Move navigation aids (quick finders, contents) to a non-`formulas` file. Then run
     `build_handbook.py --unit X --draft` (formulas by problem type, slide page refs). Checks before the user sees it:
     - "Trap (you)" lines must cite a source in their record, or they are relabelled automatically;
     - the sheet's own page numbers are cited as (Notes p.N), not as slides;
     - spot-check a handful of (Lx p.N) refs against the slides.
  The format, and every change to it, is in `docs/HANDBOOK_DESIGN.md`: change the design there (with a
  decision-log row) before changing the code.
  3. The user edits `exam_room/handbook.md`; then `build_handbook.py --unit X --pdf` gives a "Find it" line, a slide-figure appendix,
     the A-Z index and edge tabs (`print:` block in the config; tabs once the section numbers are known).
  4. Print the textbook plus the third-party notes and the tutorial solutions.
- **Restricted (N x A4):** only the sheet goes in, so the handbook is the study reference and the sheet is condensed
  from it (cheat-sheet mode, docs/HANDBOOK_DESIGN.md):
  1. Backbone and `build_handbook.py --unit X --draft` as above (for a unit with no third-party sheet, Claude writes
     `sources/<CODE>_formulas_methods.md`, numbered sections by question type, with provenance).
  2. Worked examples (below) help most here: their "Recognise it" lines feed the sheet's method chooser.
  3. `build_cheatsheet.py --unit X --draft` (1 agy call), check every formula against the handbook, then
     `--pdf`: the largest font from 7 to 5.5 pt that fits 2N sides; it fails rather than run over.

**Worked examples by question type (when the user asks).** Better for the exam room than printing mock solutions: they
are organised by type, show the method, and cover more types per page.
1. Rank the likely calculation types from the exam guide, the intel, the tutorials and any reference notes, and write
   them to `units/<id>/exam_room/worked_examples_types.md`, with the evidence for each.
2. `worked_examples.py --unit X` (6 agy calls).
3. **Read all of it and recompute the answers with independent code** (see
   `scratch/probes/verify_worked_examples.py`), and test the page references against the page text. The second AI
   pass is not enough: on ABCD1002 it left a self-contradictory question, wrong slide pages and a figure that
   didn't exist.
4. Keep the AI's version, list the edits in the front matter, and rebuild with `--pdf-only`.

## 6. Mocks (only when the user asks)

`gen_practice_exam.py --unit X`: one per request, never scheduled. It refuses to run on a CRITICAL input problem.
The student sits it timed with only the allowed materials, marks it with the solutions file, and logs every lost mark.
The generation log (`state/mock_logs/`) contains answers: read it only after sitting the mock.

## 7. After the exam

Recall notes → `reconstruct_exam.py`. Update the plan's change log; start the next unit's plan.
