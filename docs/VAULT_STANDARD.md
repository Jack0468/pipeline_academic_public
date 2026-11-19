# Vault standard

How study material is organised in the Obsidian vault. It applies to people, scripts and AI agents alike.
`scripts/check_vault.py` enforces the parts a machine can check. ABCD1002 is the reference example.

**The test for any change:** does it get a student to the next problem or the next correction faster? If it only
makes the notes tidier, it doesn't belong (README, "Design principle").

## Principles

| # | Rule | What it means here | Why |
|---|---|---|---|
| 1 | **Study comes first** | The hub leads to the plan, then to things to attempt. Reading material comes later | Practice testing and spaced practice have the strongest evidence; rereading and highlighting rate low [1] |
| 2 | **Attempt before answer** | Problems come first and answers sit in a separate file or on separate pages, with `[marks, min]` at exam pace | Attempting before instruction helped on average in a meta-analysis of 53 studies [2]. Unrestricted AI raised practice scores and lowered exam scores [3] |
| 3 | **Worked examples are for when you're stuck** | They are a reference after an attempt, and the exam-room binder. A student new to a topic may use one earlier | Heavy guidance helps novices and becomes redundant as knowledge grows [4] |
| 4 | **Folders say what a file is for; links say what it relates to; properties hold facts** | Few folders, by role. Topics are never folders | Obsidian's CEO avoids topic folders because a note often belongs to several areas [5]; community guidance keeps folders for workflow [6] |
| 5 | **One home for each thing** | A generated file exists in one place. A copy needs a reason (the slide copies exist because Obsidian can only open PDFs inside the vault) | One source of truth; a stale copy is worse than none |
| 6 | **Sources are read-only, generated files can be rebuilt, your files are never overwritten** | Three kinds of file, never mixed up (table below) | Raw data stays immutable, and every output can be traced and rebuilt [7] |
| 7 | **Nothing is deleted** | A replaced version moves to `_archive/` unchanged, and the change is recorded | The vault has no version history yet |
| 8 | **Plain files and stable names** | Markdown and PDF, usable without Obsidian or this code | Files outlast apps [8] |
| 9 | **A sat unit is frozen** | After the exam nothing is regenerated, moved or edited without `--allow-sat` and an explicit request | The record of what you studied stays true |

## The unit folder

```
units/<year>_<session>_<code>/
├── _unit.md            start here: exam facts, links to everything, lecture table
├── exam_plan.md        the plan: priorities, schedule, print list, change log
├── notes_sheet.md      your index or notes sheet
├── recall/             one file per lecture: recall prompts, exam-style problems, hidden answers
├── practice/           things to attempt, and the record of attempts
│                         mock_exam_<n>.md + mock_exam_<n>_solutions.md
│                         worked_examples_<topic>.md and .pdf (attempt-first sheets)
│                         error_log.md, stuck_log.md, past_papers.md
├── recall_checks/      AI marking of your recall attempts
├── practice_checks/    AI marking of your practice attempts
├── exam_room/          what goes into the exam: handbook, worked examples by type, their PDFs
├── reference/          facts to look up: unit_outline.md, canvas.md, exam_intel.md
├── sources/            transcriptions of your handwriting, converted third-party notes
├── slides/             copies of the lecture PDFs (rebuilt by link_slides.py)
├── EXAM_<year>/        after the exam: your recall of it and the reconstruction
└── _archive/           superseded versions and migration records; never edited
```

Only the three notes shown belong in the unit folder itself. Folders are created when first needed.

| Kind | Where | Who changes it | If it is lost |
|---|---|---|---|
| **Yours** | `_unit.md`, `notes_sheet.md`, `practice/error_log.md`, `stuck_log.md`, `past_papers.md` | You. Scripts only append marked lines | Gone: protect these |
| **Shared** | `exam_plan.md`, `exam_room/handbook.md` | You and Claude, with each change recorded. No script overwrites them | Gone: protect these |
| **Generated** | `recall/`, `recall_checks/`, `practice_checks/`, `reference/`, `sources/`, mocks, `exam_room/worked_examples.md`, every PDF | The pipeline. Edit by hand only if the front matter records it | Rebuilt, at the cost of AI calls where one was used |
| **Source** | OneDrive coursework, OneNote, Canvas | Never by the pipeline | Outside this system |

## Names

- Lowercase words joined by underscores, no spaces: `worked_examples_LAN.md` keeps a topic's usual capitals.
  Transcriptions keep their source PDF's name (`handwritten_<source>.md`), because the pipeline matches on it.
- No dates or versions in a live file's name. The folder gives the current version; `_archive/` holds the rest as
  `<name>_superseded_<date>.md` or `<name>_<date>_previous.md`.
- ISO dates everywhere. Unit folders are `<year>_sc<n>_<code>`, matching OneDrive.
- A PDF has the same name as its source note and sits beside it.

## Properties

Every live note starts with YAML front matter.

| Property | Required | Example |
|---|---|---|
| `unit` | yes | `ABCD1002` |
| `type` | yes | `recall`, `mock`, `handbook`, `error log`, `exam plan` |
| `source` or `authors` | yes | `ai`, `handwritten`, `canvas`, or a list of who wrote it |
| `model`, `generated` | AI output | `agy:gemini-3.8-flash-medium`, `2026-11-18` |
| `share` | when it must stay private | `never` |
| `edits` | when generated text is changed by hand | one line per change, naming who |

Your own words go in `> [!mine]` callouts, things you didn't understand in `> [!confused]`, and disagreements between
sources in `> [!check]`. Facts in a plan carry their origin: [official], [config], [intel: confidence], [notes p.N],
[me], [Claude], or a model id.

## Links

- Link with the full vault path, `[[units/<id>/recall/ABCD1002_L5|L5 recall]]`, so a link survives another note taking
  the same name. Slide references link to the page: `[[units/<id>/slides/Lecture5.pdf#page=15|p. 15]]`.
- Moves inside Obsidian update links. **Moves made outside it do not**, so scripts and agents use `migrate_vault.py`
  or rewrite the links themselves [9].
- Files outside the vault use `file:///` links and can go stale. `check_vault.py` lists dead ones.

## Everyday tasks

| Task | Do this |
|---|---|
| Check a unit | `python scripts\check_vault.py --unit X` (also runs in `refresh.ps1`) |
| Replace a note | Move the old version to `_archive/` with a dated name, write the new one, add a change-log row to the plan |
| Print a one-off sheet | `python scripts\print_note.py --unit X --note practice/<file>.md` |
| Start a new unit | Put `vault_layout: 2` in its config, then run `scaffold_vault.py` |
| Move an older unit to this layout | `python scripts\migrate_vault.py --unit X` (dry run), then `--apply`; undo with `--rollback` |
| Add a new kind of file | Give it a key in `scripts/paths.py`, a row in this document and in `docs/ARCHITECTURE.md` |

## Current state

| Unit | Layout | Why |
|---|---|---|
| ABCD1002 | standard (2) | Migrated 2026-11-18; record in its `_archive/migration_2026-11-18.md` |
| ABCD1003, ABCD1004 | old (1) | Exams on 23 and 26 Nov. Scripts handle both layouts, so they keep working untouched |
| ABCD1001 | old (1) | Sat, so frozen |

**Known limits.** Excluded files are ranked down in Obsidian, not hidden: `_archive/` can still appear in the file
list and in search [10]. The vault still has no git history (BLUEPRINT P0), so
`migrate_vault.py` keeps its own backup in `state/vault_backups/`. Obsidian Bases views over `type` are a natural next
step and have not been built.

## Evidence

Items 2 and 4 were read at abstract or summary level, not in full text. Item 6 is community practice, not research.

1. Dunlosky et al. 2013, *Improving students' learning with effective learning techniques*. [Summary](https://www.psychologicalscience.org/news/releases/which-study-strategies-make-the-grade.html)
2. Sinha & Kapur 2021, *When problem solving followed by instruction works*, Review of Educational Research (53 studies, g = 0.36). [Abstract](https://www.x-mol.com/paper/1406031499744456704)
3. Bastani et al. 2025, PNAS. [Abstract](https://ideas.repec.org/a/nas/journl/v122y2025pe2422633122.html)
4. Kalyuga, the expertise reversal effect. [Overview](https://my.chartered.college/impact_article/expertise-reversal-effect-and-its-instructional-implications/)
5. Steph Ango, [How I use Obsidian](https://stephango.com/vault).
6. Obsidian forum, [Folders vs. linking vs. tags](https://forum.obsidian.md/t/folders-vs-linking-vs-tags-the-definitive-guide-extremely-short-read-this/78468).
7. [Cookiecutter Data Science: opinions](https://cookiecutter-data-science.drivendata.org/opinions/).
8. Steph Ango, [File over app](https://stephango.com/file-over-app).
9. Obsidian Help: [Internal links](https://obsidian.md/help/links) (updates on rename) and [How Obsidian stores data](https://obsidian.md/help/data-storage).
10. Obsidian forum, [Excluded files not hidden in search](https://forum.obsidian.md/t/files-and-links-excluded-files-not-hidden-in-search/106205).
