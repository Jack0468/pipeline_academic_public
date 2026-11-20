# Sharing study material with classmates

`scripts/share_vault.py` makes a peer-safe copy of the vault: the material classmates can use, none of
your personal data. It writes to `OneDrive\SHARED\usyd-course-notes\` (or `SHARE_PATH` in `.env`), which you
share read-only from OneDrive. No AI calls.

## What goes out, and what never does

| Shared | Never shared |
|---|---|
| Recall files per lecture (prompts, exam-style problems with answers, time allocations, common traps) | Marks, `marks.md`, `results.xlsx`, Canvas data (`canvas.md`) |
| Mock exams with their solutions (cleaned, see below) | Error log, stuck log, recall checks, practice checks, progress, the study plan |
| Unit outline summary (public anyway; your venue and seat removed) | Handwriting transcriptions, your submitted work, slides |
| `how_to_study.md` (the study method) | Other people's sheets (the handbook only with `--include-handbook`, once its formula-sheet author agrees) |

Mocks need extra cleaning, because they are written from your own record (error log, marker feedback,
submitted work, handwritten notes, weak points). The export removes:
- `*Targets:*` lines (the weak point a question targets) and `*Values:*` lines (the random draws);
- from each `*Source:*` note, everything that points at your own material (marker notes, student or
  handwritten notes, answer sheets, submissions, the agy context files) and every assignment reference.
  Lecture pages, the unit's textbooks and practical briefs stay;
- the coordinator's "Not fully clean" warning and corrections of pipeline bugs;
- the "log every lost mark in your error log" tip.

A mock goes out only together with its solutions. A final scan refuses to write anything if a file
still contains your SID, name, email, a local path, a `SHARE_BLOCK_PATTERNS` match, or (in a mock) a
trace of your record.

## Usage

```powershell
$py = "$env:USERPROFILE\.conda\envs\pipeline_academic\python.exe"
& $py scripts\share_vault.py --dry-run                 # 1. what would go out, and what is held back
& $py scripts\share_vault.py                           # 2. write the export
& $py scripts\share_vault.py --allow-assignment-derived  # ...after reviewing held-back mocks (below)
& $py scripts\share_vault.py --unit ABCD1001           # update one unit's folder only; the others stay as they are
```

Then, once, in OneDrive on the web:
1. Share `SHARED\usyd-course-notes` with the classmates as **Can view** (never "Can edit" on this folder).
2. Share its `_submissions` subfolder with the same people as **Can edit**. This is where classmates' `peer.py`
   writes their marks and error reports (design: [REFACTOR_PLAN.md](REFACTOR_PLAN.md), "Peer share").
3. Check with one classmate that they can write in `_submissions` and nowhere else.

Classmates add the folder to their own OneDrive ("Add shortcut to My files") and run `python peer.py ...` from it.
The export finds the folder on your machine by its `.peer_export` marker, so `SHARE_PATH` is only needed if it
lives somewhere unusual. A guide for classmates follows once the pilot has settled.

**Re-run it after every new mock**, and after a mock or recall file changes (`pace_recall.py` and
`link_slides.py` rewrite them). The export is a snapshot; nothing updates it automatically, on purpose:
publishing is a decision, not a side effect of `refresh.ps1`.

**Each run syncs the folder.** New and changed files are written first, then files that are no longer part of
the export are removed: a mock that is held back also disappears from an earlier export. `_submissions/` is never
written, rewritten or deleted by the export; it only reads it. Each run turns valid submissions into
`mock_exam_<n>_class_results.md/.json` (aggregates only, from 5 attempts), lists rejected files and why, and copies
new error reports to `state/share/errata.md` for you to check. `--unit X` limits the sync to that unit's folder and
the top-level files. The dry run lists what would be removed as `(remove) ...`.

If OneDrive or an open window holds a file, the run finishes and prints `WARN remove ...` for it instead of
stopping. Close Explorer/Obsidian windows on the folder, let OneDrive finish syncing, and run it again.

## Sharing one of your own notes on purpose

Your own notes are never exported by default. To share a particular one with the group (crash-course notes, a
study guide you wrote), list it in the unit's config:

```yaml
share:
  notes:
    - file: "sources/handwritten_crash course.md"   # path inside the unit's vault folder
      as: crash_course_notes.md                     # name in the share: <unit>/<session>/notes/
      title: Crash course notes, with the tutor's advice
```

It goes out cleaned like a recall file: local links and transcription details removed, front matter `type: shared
notes`. A line at the top says it is a classmate's own notes, transcribed by AI and unofficial. The personal-data scan
applies to it like every other file. Everything in the note goes out, including your `[!mine]` and `[!confused]`
callouts and any names it mentions, so read it first.

## Held-back mocks: questions that mirror an assignment

Mock writers are told to re-test what went wrong in your assignments "in a new context", so some
questions cite an assignment as their source. If a course reuses assignments, a shared worked solution
could give away an answer. Those mocks are held back by default:

```
HELD BACK ABCD1001 mock_exam_2.md: questions Q21, Q22, Q23 mirror an assignment question: ...
```

To review, open the mock's solutions in the vault (`units/<unit>/practice/mock_exam_<n>_solutions.md`),
find each listed question, and compare it with the assignment brief:
- **Fine to share:** the same idea or method in a different situation (different givens, a different
  unknown, a different context).
- **Not fine:** the same setup with the same or merely rescaled numbers, or a solution that would answer
  an assignment part directly. Rewrite that question by hand in the vault, or don't share that mock.

When every listed question is fine, run with `--allow-assignment-derived`. The assignment references
are removed from the exported source notes either way.

## Keeping other people's names out

Some coursework folders hold other students' files (e.g. a classmate's lab folder, or notes a
friend sent you). Add their names and anything else that must never appear to `.env`:

```
SHARE_BLOCK_PATTERNS=(?i)\bfirstname\b,(?i)surname
```

Each entry is a regular expression (no commas inside one); a match anywhere blocks the whole export
until it is fixed.

## Limits

- The checks are pattern-based. They catch the known traces of your record, not every possible one.
  Skim a new mock's exported solutions once before sharing the folder for the first time.
- Mocks are AI-written. Solutions are re-solved by a second pass but not verified by a person; the
  shared README says so.
- Source notes may still name "Formula Sheet Group N". If that sheet is a classmate's, those notes point
  peers at something they don't have (nothing from the sheet itself is copied into the mock).
- About 40% of a mock targets the sharer's weak points. With the target notes removed, nothing says which.
