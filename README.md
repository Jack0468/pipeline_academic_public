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
   gitignored, so your details never get committed. To keep it somewhere private and versioned instead (for example a folder in your
   Obsidian vault, under git), set `PIPELINE_CONFIG` to that folder in `.env`.
3. Copy `.env.example` to `.env` for the AI backend and calendar settings
   ([docs/GEMINI_SETUP.md](docs/GEMINI_SETUP.md), [docs/GCAL_SETUP.md](docs/GCAL_SETUP.md)).
4. `python scripts/scaffold_vault.py`, then follow [docs/EXAM_SPRINT_SETUP.md](docs/EXAM_SPRINT_SETUP.md), which lists
   every command.

Tests: `python -m pytest` (no network, no AI). Lint: `python -m ruff check scripts/peer.py tests`.

Contributing: run `python scripts/check_personal.py --install-hook` once in your clone. Every commit is then checked
for personal data (student IDs, emails, your own patterns from `share.yaml`) and emojis.

## Where your data lives

This repo is only the code. Course content and marks never come here, so you can use it, change it and share it
without putting any course material on the internet.

```
 this repo (public)                  your vault (private, your machine)         the group's shared folder (OneDrive)
 code, prompts, tests, docs  ──────► notes, recall files, mocks, checks,  ────► readable by the friends you invite:
 config.example/                     marks, handbook, your config               recall files, mocks + solutions,
                                     (+ .env, state/ beside the code)           peer.py, class results
                                                 ▲                                         │
                                                 └──── class results ◄── _submissions/ ◄───┘ friends' practice marks
```

| Place | Who can see it | What it holds |
|---|---|---|
| **This repo** | anyone | the code, prompts, tests, generic docs and `config.example/`. No course content, no marks, no names. Each commit is checked for personal data |
| **Your vault** (an Obsidian folder on your machine) | you | everything that is yours: your notes, recall files, mocks, marking, error log, your real marks and your config (for example `_pipeline/config/`, set `PIPELINE_CONFIG` to it). Put it under git for history, but keep it private. Machine secrets and caches (`.env`, `.secrets/`, `state/`) stay beside the code, gitignored |
| **The shared folder** (OneDrive, or any synced folder you can share) | the friends you invite | the group's material: a cleaned export of your recall files, mocks with solutions and outlines, `peer.py`, and the class results. Friends write only in `_submissions/`: their practice marks and error reports |

**Your own group: one person sets up a shared folder and invites their friends.** Nobody else can see it, and your
course content never touches the internet beyond the OneDrive accounts you chose to share with.
1. Set up this repo and your vault (above), then run `python scripts/share_vault.py --dry-run`. Then run it again
   without `--dry-run`. It writes the export into `SHARED/usyd-course-notes` in your OneDrive, or into `SHARE_PATH`
   from `.env`. The folder name doesn't matter: the tools find the folder by its `.peer_export` marker.
2. In OneDrive on the web, share that folder with your friends as **Can view**, and its `_submissions` subfolder as
   **Can edit**.
3. Each friend adds it to their own OneDrive ("Add shortcut to My files"). They need nothing but Python 3.10+: from
   that folder, `python peer.py record <UNIT> <mock>` sends their marks, `python peer.py results <UNIT> <mock>` shows
   the class results next to their own, and `python peer.py report ...` flags an error. `peer.py` finds the folder
   on each machine by itself.
4. Re-run `share_vault.py` after new material, and to turn new submissions into class results.

Several people can each run the full system on their own vault and export into the same shared folder, one unit or
session per folder (`<unit>/<year>-<session>/`).

**What never leaves your vault:**
- your real assessment marks (`results.xlsx`, Canvas);
- your own notes (OneNote exports and their transcriptions);
- your error log and individual attempts.

Practice marks reach the group only as class aggregates, and only from 5 attempts up. The export refuses to write if
a file still matches a personal-data pattern. Practice material only: never real exam questions or assessed work.

## Status

Working, but still a folder of scripts; the move to an installable package is planned in
[docs/REFACTOR_PLAN.md](docs/REFACTOR_PLAN.md). Some docs mention the owner's private planning files (BLUEPRINT,
LOGBOOK); those are private and not published.

Licence: MIT ([LICENSE](LICENSE)).
