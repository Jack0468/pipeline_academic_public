# pipeline_academic

A study system for university units, built around retrieval and timed practice. Python does the deterministic
work: planning, file routing, caching and exports. AI (Gemini, through the Antigravity CLI or the API) is used only
for judgement: recall questions from lecture slides, handwriting transcription, mock exams with checked solutions,
marking scanned attempts, and an exam handbook. Everything lands as Markdown in an Obsidian vault.

It was built for one student's units at the University of Sydney and is being turned into something classmates can
run on their own units, by changing configuration and not code. See [docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md).

## What it does

| Part | What | Command |
|---|---|---|
| Recall material | per lecture: free-recall prompts, exam-style problems with marks and time allocations, traps | `gen_recall.py` |
| Mock exams | full papers in the real format, solutions re-solved by a second pass, never reusing practised problems | `gen_practice_exam.py` |
| Marking | your scanned recall and practice attempts, marked per part, with the first error and the fix | `grade_recall.py`, `grade_practice.py` |
| Study plan | blocks around your Google Calendar, weighted by the exam mark each unit needs | `plan_study.py`, `gcal_sync.py` |
| Exam room | an open-book textbook with an A-Z index, and worked examples by question type | `build_handbook.py`, `worked_examples.py` |
| Peer share | a cleaned copy for classmates in a shared OneDrive folder; classmates send back practice marks with `peer.py`, and only class aggregates are published | `share_vault.py`, `peer.py` |

## Getting started

1. `conda env create -f environment.yml`, then `conda activate pipeline_academic`.
2. Copy `config.example/` to `config/` and edit it: one YAML file per unit (`config/units/`), your study hours
   (`study_plan.yaml`), your targets (`targets.yaml`) and the patterns that name you (`share.yaml`). `config/` is
   gitignored, so your details never get committed. To keep your config in its own (private) repo instead, set
   `PIPELINE_CONFIG` to that folder in `.env`.
3. Copy `.env.example` to `.env` for the AI backend and calendar settings
   ([docs/GEMINI_SETUP.md](docs/GEMINI_SETUP.md), [docs/GCAL_SETUP.md](docs/GCAL_SETUP.md)).
4. `python scripts/scaffold_vault.py`, then follow [docs/EXAM_SPRINT_SETUP.md](docs/EXAM_SPRINT_SETUP.md), which lists
   every command.

Tests: `python -m pytest` (no network, no AI). Lint: `python -m ruff check scripts/peer.py tests`.

Contributing: run `python scripts/check_personal.py --install-hook` once in your clone. Every commit is then checked
for personal data (student IDs, emails, your own patterns from `share.yaml`) and emojis.

## Classmates using a shared folder

You don't need any of the above to use a shared folder someone else publishes. Add it to your OneDrive, then from
that folder run `python peer.py where`, `python peer.py record <UNIT> <mock>` and `python peer.py results <UNIT>
<mock>` (Python 3.10+, nothing to install). Its README says what is collected and who can see it.

## Privacy by design

- Real assessment marks (`results.xlsx`, Canvas) never leave your machine: not this repo, not the share.
- Practice marks reach classmates only as class aggregates, from at least 5 attempts.
- Your own notes (OneNote exports and their transcriptions) are never exported.
- The share export refuses to write if a file still matches a personal-data pattern.

## Status

Working, but still a folder of scripts; the move to an installable package is planned in
[docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md). Some docs mention the owner's private planning files (BLUEPRINT,
LOGBOOK); those live in a private repo and are not published.

Licence: MIT ([LICENSE](LICENSE)).
