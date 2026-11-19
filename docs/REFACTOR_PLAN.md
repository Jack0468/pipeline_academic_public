# Refactor plan: from a folder of scripts to one program

Status (2026-11-20): **S0, the peer pilot, is being built now, together with the part of R0 it needs. R1-R5 have not
started.** Done early: file locations moved into `scripts/paths.py` and `pdf_tools.py` was added on 2026-11-18 (see
[ARCHITECTURE.md](ARCHITECTURE.md), which also holds the project goal and the redundancy audit). R1's `settings.py`
grows from `paths.py`. Checklist: BLUEPRINT.md, Track B, "R: Restructure the code" and "S: Peer share".

## Dates that set the order

| Date | What it means here |
|---|---|
| 2026-11-17, 2026-11-19 | ABCD1001 and ABCD1002 exams. Both are sat, so both units are frozen (`common.work_units`) |
| **2026-11-23 13:00** | **ABCD1003 Control exam.** Classmates should have the peer pilot (S0) by Saturday 21 Nov, so they can sit the shared mock and send back their marks before the exam |
| 2026-11-26 17:00 | ABCD1004, the last exam. Nothing else changes under exam preparation before this |
| from 2026-11-27 | The rest of R0, then R1-R5 |

Order:
1. **S0** (peer pilot), with the part of R0 it needs: pytest, ruff, tests for the new code.
2. The rest of R0 after the last exam, then R1-R5.
3. **S1**, the full pooled data, after R2.

S0 is a feature, not part of the refactor. It is built the way the refactor wants code to look: one module with only
the standard library, pure functions, tested. R1 can then move it without edits.

## Why

The Track A code works and its design is sound: deterministic Python, AI only for judgement, cached by hash,
never overwriting your files. The problem is the shape of the code. First measured on 2026-11-15, re-measured on
2026-11-20 (the earlier figure in brackets where it changed):

| Symptom | Evidence |
|---|---|
| Core formats have no single home | The mock-paper format (`## Question k`) is parsed in 5 files (`gen_practice_exam`, `grade_practice` and `mock_writer` 3 times each, `pace_recall` twice, `share_vault` once). Front matter is parsed in 11 places across 10 files (3). The `progress.md` section edit is copied 3 times. There are 5 near-identical "append this line once" functions |
| Changing a format means editing many files | Changing the mock format touches `mock_writer`, `gen_practice_exam`, `grade_practice`, `pace_recall` and `share_vault` |
| AI output is parsed with regexes that fail silently | 4 of the bugs fixed on 2026-11-15 were this kind (missing JSON summary, missing solutions separator, rewrite without its solution, incomplete check reply) |
| Scripts double as libraries | 34 flat modules (25). `gen_practice_exam` is imported by `ingest_intel`, `reconstruct_exam`, `worked_examples` and (inside a function) `mock_writer`, which it imports in turn. `pace_recall` is imported by 4 scripts. Importing `common` loads `.env` as a side effect, through `paths` |
| Dense, long functions | 139 lines over 120 characters (67); if-expressions inside f-strings; 22 lambdas in variables (11); `main()` functions that parse arguments, do the work, write files and print (about 100 lines in `gen_practice_exam`) |
| No safety net | No tests in the repo (15 test files in `scratch/tests/`, which is gitignored), no linter, no CI. pytest and ruff are not installed in the conda env. 15 `except Exception` blocks in 8 files (13) |
| Tied to one person and one machine | The conda path is hard-coded in both `.ps1` files. The share's personal-data patterns contain your name, SID pattern and Gmail. The coursework folder defaults to your OneDrive path, and the share location is derived from it. The time zone and USyd URLs are fixed in code |

Each problem is small on its own. Together they make every change slow and risky, and they block the
students-for-students idea (`docs/ideas/idea1.md`), which needs other people to run and change the code.

### Rules added since the first draft (2026-11-15) that the refactor must keep

- **Two vault layouts.** A unit picks `vault_layout: 1` or `2` (`paths.py`, [VAULT_STANDARD.md](VAULT_STANDARD.md)).
  Units that were not migrated, and sat units, stay on layout 1. Every R2 vault module locates files through
  `unit_path`, and the golden tests cover both layouts.
- **Sat units are frozen.** `common.work_units()` skips a unit whose exam is over unless `--allow-sat` is given
  (your rule, 2026-11-18). Commands keep the flag in R4. Tests inject the clock, because "now" decides which units a
  run may touch. Read-only tools (marks, plan, share, `unit_inputs`) and `reconstruct_exam.py` are exempt.
- **One resolver for inputs.** `unit_inputs.py` resolves and checks every input, and mocks stop on a CRITICAL input
  problem. It becomes the check half of `sources.py`.
- **Rules for generated files** (ARCHITECTURE, "Rules for a new script"): ask `paths.py` for locations, build in a
  temporary folder and publish finished files, never overwrite a person's file (move it to `_archive/`), and write
  `unit`, `type` and `source` in the front matter.
- **Design specs come before code.** [HANDBOOK_DESIGN.md](HANDBOOK_DESIGN.md) is the living spec for the handbook.
  `build_handbook.py` is the largest module (52 KB) and had uncommitted work on 2026-11-20. Split it in R4 only after
  that work is committed.
- **Lessons from the LOGBOOK:**
  - rename or move a section of your file, never delete it;
  - an AI re-solve finds arithmetic slips, not badly posed questions or wrong citations. This matters for R3's
    second checker.

## Goals and non-goals

Goals:
- Each concept (unit config, vault Markdown formats, mock paper, AI backend, manifest) is defined in one place.
- AI replies are validated data, not text scraped with regexes. A malformed reply is a clear error and is
  retried, never quietly accepted.
- Commands are thin; the logic is in functions that can be tested without the AI, Google, Canvas or OneDrive.
- A test suite and a linter run on every commit.
- Another student can install it and point it at their own units through config alone.
- A classmate can use the shared material, and send back marks and error reports, with one Python file and nothing
  else to install (S0).

Non-goals:
- **No change in behaviour during the refactor.** The vault files it writes stay the same, byte for byte,
  except where a phase says otherwise. Known bugs go on the follow-up list at the end, not into the refactor.
  S0 is the exception: it is a feature, built before the refactor on its own branch.
- No framework, database, web app or async code. The vault stays Markdown in Obsidian.
- No rewrite from scratch. Code moves in steps; every step leaves a working system.

## Target layout

```
pipeline_academic/
├── pyproject.toml                 package metadata, dependencies, ruff + pytest config, console entry point
├── src/pipeline_academic/
│   ├── __main__.py                python -m pipeline_academic <command>
│   ├── settings.py                paths, env, time zone, university, share location; loaded once, explicitly
│   ├── config.py                  unit / study-plan / targets schemas: load + validate (clear errors)
│   ├── fsutil.py                  atomic write, write_if_absent, copy_locked, file_uri, read-only handling
│   ├── manifest.py                hash cache (atomic writes, thread-safe)
│   ├── text.py                    no_emoji, fill, natural_key
│   ├── sources.py                 find_sources, lecture labels, the input check (unit_inputs)
│   ├── pdf.py                     Markdown to PDF steps (pdf_tools), print a note
│   ├── vault/
│   │   ├── layout.py              unit layouts 1 and 2, unit_path, note types (from paths.py)
│   │   ├── markdown.py            front matter, sections, callouts: parse + render
│   │   ├── ledger.py              append-once keyed lines (error log, notes sheet, handbook to-dos)
│   │   ├── progress.py            progress.md: read, tick, bump counters, add lectures
│   │   ├── paper.py               Question / Section / MockPaper: parse + render, question IDs; standard library only
│   │   ├── recall.py              recall file: prompts, exam-style problems, answers
│   │   ├── check.py               check_vault
│   │   └── migrate.py             migrate_vault (backup, journal, rollback)
│   ├── ai/
│   │   ├── backend.py             Backend protocol; AgyBackend, ApiBackend, HandoffBackend; retries, quota, timeouts
│   │   ├── tasks.py               each AI task = prompt file + JSON schema + renderer
│   │   └── schemas/*.json
│   ├── planning/                  marks.py, planner.py (slots, allocation, phases), ics.py
│   ├── integrations/              gcal.py, canvas.py, outline.py, onenote.py (+ .ps1), svg_notes.py
│   ├── intel/                     ingest.py (word on the street), reference.py (third-party notes)
│   ├── study/                     recall_gen, handwriting, grade_recall, grade_practice, pace, link_slides, scaffold
│   ├── mocks/                     plan.py (sections), checks.py (overlap, key, cues, coverage), writer.py,
│   │                              generate.py, log.py (generation log), reconstruct.py (sat exams)
│   ├── handbook/                  draft.py, pdf.py, index.py, worked_examples.py
│   ├── share/                     sanitize.py, export.py, peer.py (classmates' tool, standard library only),
│   │                              results.py (pooled class marks: templates, validation, statistics)
│   └── cli/                       one thin module per command: arguments → service → printed summary
├── scripts/                       refresh.ps1, nightly_ai.ps1, plus temporary shims (see R4)
├── tests/
│   ├── fixtures/                  synthetic vault, saved model replies, synthetic results.xlsx (no personal data)
│   └── test_*.py
└── scratch/                       gitignored: Claude's working files (tests in progress, probes, temp output)
```

Where each current module goes:

| Now (`scripts/`) | Goes to |
|---|---|
| `common.py` | `settings.py`, `config.py`, `fsutil.py`, `manifest.py`, `text.py`, `sources.py` |
| `paths.py` | `settings.py` (machine locations from the environment), `vault/layout.py` (layouts, `unit_path`, note types) |
| `unit_inputs.py` | `sources.py` (the resolver and its check) |
| `pdf_tools.py`, `print_note.py` | `pdf.py`, `cli/print_note.py` |
| `progress.py`; the progress edits in `grade_recall.tick`, `grade_practice.bump` | `vault/progress.py` |
| `append_*` in `grade_recall`, `grade_practice`; `seed_error_log` in `canvas_sync` | `vault/ledger.py` |
| `blocks`, `heading`, `assemble`, `split_reply` (mock_writer); `blocks`, `mock_section` (grade_practice); `_section`, `_front` (gen_practice_exam); `HEAD` (pace_recall); `QUESTION` (share_vault); the paper parser and IDs in `peer.py` (S0) | `vault/paper.py`, `vault/markdown.py` |
| `practised` (mock_writer); exam-style problem parsing in `grade_practice`, `pace_recall` | `vault/recall.py` |
| `check_vault.py`, `migrate_vault.py` | `vault/check.py`, `vault/migrate.py` |
| `gemini.py` | `ai/backend.py` |
| `mock_writer.py` checks (overlap, mc_issues, coverage) and `gen_practice_exam.py` checks (`key_mismatch`, `mc_cues`, `answer_key`) | `mocks/checks.py` |
| `mock_writer.build`, `gen_practice_exam.main` | `mocks/writer.py`, `mocks/generate.py`, `cli/mock.py` |
| `mock_log.py`, `reconstruct_exam.py` | `mocks/log.py`, `mocks/reconstruct.py` |
| `marks.py`, `plan_study.py` | `planning/marks.py`, `planning/planner.py`, `planning/ics.py` |
| `gcal_sync.py`, `canvas_sync.py`, `fetch_unit_outline.py`, `export_onenote.py` + `onenote_publish.ps1`, `svg_notes_to_pdf.py` | `integrations/` |
| `ingest_intel.py`, `convert_reference.py` | `intel/` |
| `gen_recall.py`, `extract_handwriting.py`, `grade_recall.py`, `grade_practice.py`, `pace_recall.py`, `link_slides.py`, `scaffold_vault.py` | `study/` |
| `build_handbook.py`, `book_index.py`, `worked_examples.py` | `handbook/` |
| `share_vault.py` | `share/sanitize.py` (clean, clean_mock, guards), `share/export.py` (plan + sync) |
| `peer.py` (new in S0) | `share/peer.py` (the command) and `share/results.py` (templates, validation, statistics). Both stay standard library only, because the export copies them into the share |
| `check_no_emoji.py` | `cli/emoji.py`, also run as a pre-commit hook |

## Phases

Each phase is one branch, merged only when the exit criteria hold. Rough effort is in hours of focused work.

### S0: Peer pilot on the shared OneDrive (now, before 2026-11-23; 6-8 h). A feature, not a refactor step.

The design is in "Peer share" below. What S0 builds:
- `scripts/peer.py`, the classmates' tool. It uses only the standard library and runs on Python 3.10+ with nothing
  installed. It finds the share, records marks, reports errors and shows the class results. `share_vault.py` uses the
  same module for templates, question IDs and statistics, so the format has one home.
- `share_vault.py`:
  - writes a results template per mock, `peer.py` itself, and class results (aggregates only);
  - finds the share instead of deriving it from your coursework path;
  - never touches `_submissions/`.
- Tests in `tests/` (pytest) on synthetic fixtures: discovery, question parsing and IDs, template, record,
  validation, statistics, and a sync that leaves `_submissions/` alone.

Exit:
- an export of the real vault passes the personal-data scan with `peer.py` and the templates included;
- a classmate's machine (or a second OneDrive account) finds the share, records a mock and the file appears in your
  `_submissions/`;
- the next export publishes class results from it;
- `pytest` and `ruff check` are clean on the new code.

### R0: Safety net (4-6 h). Nothing is moved yet.
- **Put the vault under git first** (BLUEPRINT P0), so any change the refactor makes to vault files can be
  reviewed as a diff and undone. On 2026-11-20 the vault still had no history. The migration backups in
  `state/vault_backups/` cover only ABCD1002.
- `pyproject.toml` with `ruff` (format + lint, line length 120) and `pytest`; a pre-commit hook running
  ruff, the tests and the emoji check. Add pytest and ruff to `environment.yml`. S0 starts this file.
- **Characterisation tests**: pin today's behaviour before changing anything.
  - Move the 15 offline test files in `scratch/tests/` into `tests/` as pytest. They cover:
    - the mock writer with a fake model (checkpoint, resume, rewrite acceptance), mock numbering, retries;
    - the planner (block under way, no overlaps) and the frozen units;
    - the share cleaning;
    - the vault layouts, unit inputs and exam intel;
    - the handbook guards and worked examples;
    - the generation log, salvage of cut-off replies and the Canvas mirror.

    Some of them read the real vault. Those switch to fixtures as they move.
  - Add golden-file tests: run `scaffold_vault`, `pace_recall`, `link_slides`, `marks`, `plan_study`
    (fixed "now") and `share_vault` on `tests/fixtures/` and compare with committed expected output.
  - Pin the question IDs of a fixture mock (S0). Classmates' submissions refer to them, so they must never change
    across the refactor.
  - Saved model replies (good, malformed, cut off) for each AI task, so parsing is tested without AI calls.
- Fixtures are synthetic: no real marks, names, IDs or handwriting.

Exit: `pytest` passes with no network and no AI; `ruff check` is clean or only lists known issues; the golden
tests fail on purpose when one output byte is changed.

### R1: Package, settings, config schema (3-4 h)
- Move the code under `src/pipeline_academic/`; it can be installed (`pip install -e .`) in the conda env.
- `settings.py` replaces the module-level constants in `common.py` and `paths.py`. `.env` is loaded once, by the
  command entry point, not on import. These come from settings or config, not code:
  - paths, the time zone and the university URL pattern;
  - the share's personal-data patterns;
  - the share location: `SHARE_PATH`, else S0's discovery.
- The coursework folder has no personal default. When it is missing, the error says which setting to add.
- `config.py` validates `config/units/*.yaml`, `study_plan.yaml` and `targets.yaml` into dataclasses (or
  pydantic models), with clear errors for missing or mistyped fields instead of `KeyError` mid-run.
- The `.ps1` runners take the Python path from an environment variable or `conda run`, not a hard-coded path.

Exit: all tests green; `python -m pipeline_academic --help` lists every command; no import reads `.env`.

### R2: One home for each vault format (6-8 h). Most of the duplication goes here.
- `vault/markdown.py`: front matter read/write, find/replace a `## section`, callouts.
- `vault/ledger.py`: one "append a keyed line once, replace on re-mark, keep ticked ones" helper, used by
  the error log, the notes-sheet and handbook to-dos, and the Canvas feedback.
- `vault/progress.py`: read, tick, bump counters, add lectures, all through one section parser.
- `vault/paper.py`: `Question` (number, marks, parts, body, options, solution, source, tests, targets, values),
  `Section` and `MockPaper`, with one parser and one renderer. Every reader of mock files uses it: writer, checks,
  grading, pacing, sharing and the peer tool. It stays standard library only, and the export copies it into the
  share next to `peer.py`.
- `vault/recall.py`: the same for recall files and their exam-style problems.
- **Stable question IDs.** S0 already computes them at export time with the rules below. R2 moves the code to
  `vault/paper.py` unchanged; the pinned IDs from R0 prove it.
  - Each mock gets a `mock_uid`: a short hash of unit, session, mock number and seed.
  - Each question gets `<mock_uid>-Q<k>`, plus a short hash of its text as its version.
  - R2 also writes `mock_uid` into the front matter of both files for new mocks. This is the one vault-format change
    R2 makes on purpose: a new field, nothing existing changes.
  - With the question bank (QUESTION_GENERATION_RESEARCH.md), the ID becomes the item model plus the variant seed.

Exit: golden tests unchanged; `rg "## Question"` finds the pattern only in `vault/paper.py` and tests;
the 5 append helpers, 3 progress editors and the 11 front-matter parsers are gone.

### R3: AI layer with validated replies (6-10 h)
- `ai/backend.py`: a `Backend` protocol (`generate(task, files, texts) -> Reply`) with `AgyBackend` and
  `ApiBackend`. One place for retries, quota exhaustion, timeouts and the agy workspace rules (including the
  recovery of answers agy writes to linked files, added 2026-11-17). No module-level state; the backend is passed in,
  so tests use a fake.
- **Room for ChatGPT Edu** (USyd, all students from S1 2027; BLUEPRINT Track B "E"). It has no API or Codex
  announced, so it can't be a scripted backend yet. The protocol should still make two things possible:
  - a `HandoffBackend`: for a task you want answered there, the pipeline writes a ready-to-paste bundle
    (prompt plus the list of files to upload) to `scratch/handoff/`, and an import command reads the pasted
    reply and validates it against the task's schema;
  - an `OpenAIBackend` or Codex backend, added later if the licence includes API or `codex exec` access.

  The main use is an independent, non-Gemini second checker for mock solutions, so errors don't repeat across the
  writer and the checker the way they did in mock 2. Per the worked-examples lesson, a re-solve still doesn't replace
  recomputing with code. Classmates' error reports (S0) are a third check.
- `ai/tasks.py`: each AI task bundles its prompt file, a JSON schema for the reply, and a renderer from the
  validated data to the vault's Markdown. A reply that fails the schema is retried once, then reported.
- **Spike first (1 h):** confirm that agy's JSON-schema output (see the `antigravity_cli` skill) works for
  long replies with LaTeX. If it doesn't, keep Markdown replies but parse them through `vault/paper.py`,
  with strict checks.
- Convert one task at a time, simplest first: `grade_recall` (already ends in JSON), `grade_practice`, the
  solution check, then the mock writers (questions as a JSON list, rendered with `vault/paper.py`).
  `gen_recall`, handwriting transcription, `ingest_intel` and `reconstruct_exam` can stay Markdown, since nothing
  parses them strictly.

Exit: no regex parses a model reply outside `ai/tasks.py` and `vault/`; each converted task has tests for a
good reply, a malformed one and a cut-off one.

### R4: Thin commands, smaller functions (6-8 h)
- Each command in `cli/` only parses arguments, calls a service function and prints a summary. Services
  return data; rendering and file writing are separate steps. The repeated boilerplate (`--unit` in 18 scripts,
  `--allow-sat` in 15, stdout setup in 23) moves into the single entry point.
- Split the long functions: `gen_practice_exam.main`, `mock_writer.build`, `plan_study.build_slots`,
  `share_vault.main`, `build_handbook.pdf` (after its pending work is committed), `gcal_sync.cmd_busy`.
- Replace lambdas in variables and if-expressions inside f-strings with named helpers; wrap long lines.
- Keep `scripts/<name>.py` as 3-line shims that call the new command, so the documented commands,
  `refresh.ps1` and `nightly_ai.ps1` keep working; remove the shims once the docs point at
  `python -m pipeline_academic ...`. `peer.py` in the share is not a shim: it stays a standalone file.

Exit: no function over about 60 lines outside rendering templates; the docs use the new commands; the shims
are removed or marked deprecated.

### R5: Hardening and generality (3-4 h)
- `logging` instead of bare `print` for diagnostics (the user-facing summary lines stay as they are).
- Replace each `except Exception` with the errors actually expected, or log and re-raise.
- Atomic writes everywhere through `fsutil` (the manifest, gcal state, the vault pages the scripts own).
- University-specific parts (outline URL, Canvas base, time zone, exam-calendar notes, the share's folder name) move
  into config, so another student at another university changes config, not code.
- CI (GitHub Actions on Windows): ruff and pytest on every push.

Exit: CI green; a fresh clone plus `.env` plus a unit YAML runs `scaffold`, `marks` and `plan` for a new
student without code edits.

## Effort and order

| Phase | Hours | When | Value |
|---|---|---|---|
| S0 peer pilot | 6-8 | now, before 2026-11-23 | High for the ABCD1003 trial; first real use by other people |
| R0 safety net | 4-6 | the S0 part now, the rest from 2026-11-27 | Highest: makes everything after it safe |
| R1 package + config | 3-4 | from 2026-11-27 | Medium; needed before anyone else runs the full pipeline |
| R2 vault formats | 6-8 | | Highest: removes most of the duplication |
| R3 validated AI replies | 6-10 | | High: removes the silent-failure class of bugs |
| R4 thin commands | 6-8 | | Medium: readability and reviewability |
| R5 hardening | 3-4 | | Medium; required for the students-for-students idea |
| **Total** | **34-48** | | |

The refactor alone (R0-R5, 28-40 h) is about the size of the original Track B budget (25-35 h, README §1). If time
is short, do **R0, R2 and R3 first** (about 16-24 h). They remove the risk and most of the duplication. R1, R4 and
R5 can follow when another person is about to run the full pipeline. S0 doesn't need them, because classmates run
only `peer.py`.

## Rules while refactoring

- One phase per branch; tests green before merging; no AI calls in tests.
- Moves and behaviour changes never in the same commit. A move commit's golden-test diff is empty.
- Vault output stays byte-identical unless a phase says otherwise, and any change is reviewed as a diff in
  the vault repo before it is committed.
- Question IDs never change (the pinned test from R0).
- No new personal data in tracked files: no real marks, seats, account names or IDs in code, docs or tests. Use
  synthetic examples (see "Publishing the app without your data").
- No emojis anywhere (the pre-commit hook enforces it).
- Update README, `docs/EXAM_SPRINT_SETUP.md` and the LOGBOOK at the end of each phase, not at the end.

## Risks

| Risk | Mitigation |
|---|---|
| The refactor runs into 2027 S1 Week 1 and breaks the daily loop | Finish R0-R3 by end of January; the shims in R4 keep the old commands working throughout |
| Vault format drift breaks your notes or Obsidian links | Golden tests plus the vault under git (R0) |
| agy's structured output is unreliable for long LaTeX replies | The R3 spike, with a fallback to strict Markdown parsing |
| Scope creep into new features | Features wait for P2-P6, except S0; the follow-up list below is fixed after the refactor |
| Golden tests are brittle (dates, "now", hashes) | Inject a clock and fixed seeds; normalise dates in comparisons |
| S0 is built in the week of two exams | Keep it to the pilot list in "Peer share"; everything else waits for S1 |

## Peer share: shared OneDrive, submissions and pooled difficulty

**Idea:** you and your classmates share one OneDrive folder. You publish the study material into it (recall files,
mocks with solutions, outlines). Classmates sit the shared mocks and push their results back into the same folder:
their marks per question, and reports of errors they find. Your next export turns the pooled marks into class
results: how hard each question really is for the class, not just for one person. Checklist: BLUEPRINT Track B "S".
S0 is the pilot for ABCD1003; S1 completes it after R2.

**Why:**
- **One person's attempts are too few to judge a question.** The question-bank research found that
  AI-predicted difficulty is a screen, not a replacement for real response data. A class gives real data.
- **Flags bad questions.** A question nobody gets right, or one the strongest students get wrong more often
  than weaker ones, is usually wrong or ambiguous. A multiple-choice distractor picked more often than the
  key usually means a miskeyed or unclear item. Classmates' error reports catch what the AI checker misses.
- **Better papers.** The assembler (QUESTION_GENERATION_RESEARCH.md) can target a difficulty, and the
  generation log's hypothesis (are replacement questions harder?) gets tested on many students, not one.
- **Better feedback to students.** "68% of the class lost marks here" points at a common misconception,
  which is what `docs/ideas/idea1.md` asks for ("anonymously aggregate feedback ... common misconceptions").

### Where it lives

One OneDrive folder that you own: `SHARED\usyd-course-notes\` in your university OneDrive. Classmates add it to their
own OneDrive (in the browser: Shared, then "Add shortcut to My files"). It then syncs to their machines like their
own files, at a path that differs from yours.

```
usyd-course-notes/                       classmates: Can view. Written only by share_vault.py
├── .peer_export                         marker: the tools find the folder by it
├── README.md, how_to_study.md           what this is, how to use it, the consent note
├── peer.py                              the classmates' tool (standard library only, Python 3.10+)
├── <CODE>/<YEAR>-S<n>/
│   ├── README.md, outline.md, recall/
│   └── mock_exams/
│       ├── mock_exam_<n>.md, mock_exam_<n>_solutions.md
│       ├── mock_exam_<n>_results_template.csv    one row per question part, with the IDs filled in
│       └── mock_exam_<n>_class_results.md/.json  aggregates only
└── _submissions/                        classmates: Can edit. share_vault.py never writes, rewrites or deletes here
    └── <CODE>/<YEAR>-S<n>/
        ├── mock_exam_<n>/<token>.csv              one file per person per mock; a resubmission replaces it
        └── errata/<token>_<date-time>.md          "this question or solution is wrong"
```

Each person writes only their own files. OneDrive syncs whole files, so two people never edit the same file and
there are no sync conflicts.

### What the share holds

The rule: share what the pipeline makes that classmates can't get from Canvas themselves. Never share:
- your own notes (the OneNote exports and their transcriptions);
- your real assessment marks;
- your practice marks at the level of single attempts.

Practice marks reach the group only as class aggregates, through `peer.py` (below).

| Content | Vault location (layout 2) | Shared on 2026-11-20 | Target |
|---|---|---|---|
| Recall files | `recall/` | yes | yes |
| Mock exams and solutions | `practice/mock_exam_<n>*.md` | yes, cleaned; assignment-derived ones held back for review | same |
| Results templates, class results, `peer.py` | made by the export | yes (S0) | yes |
| Unit outline | `reference/unit_outline.md` | yes, seat and venue removed | yes |
| Study method | `00_dashboard/how_to_study.md` | yes | yes |
| Handbook (Markdown and PDFs) | `exam_room/handbook*` | only with `--include-handbook` | **yes**, once the author of any classmate's formula sheet it builds on agrees (open: ABCD1001 cites "Formula Sheet Group"). The PDFs carry slide figures; every classmate has the slides already |
| Worked examples (Markdown and PDF) | `exam_room/worked_examples*`, `practice/worked_examples_*` | no | **yes**: AI-written and checked, nothing personal in them |
| Exam intel | `reference/exam_intel.md` | no | **yes, cleaned:** keep the claims, their confidence and "What it means for study"; drop "Items on file" and anything that names a source person or account. The personal-data scan runs on it |
| Notes sheet | `notes_sheet.md` | no | only after a review: its traps come from your error log |
| Reconstructed sat exam | `EXAM_<year>/` | no | **no by default (open):** it reproduces real exam questions, which this design never shares |
| Error and stuck logs, recall and practice checks, `past_papers.md` log, progress, marks, study plan, exam plan, unit hub | various | no | no: these hold your marks and record at attempt level |
| Canvas record, slides, handwriting transcriptions | `reference/canvas.md`, `slides/`, `sources/` | no | no: from Canvas or your own notes |

**Real marks can't enter the share by design.** A submission is accepted only against a results template, and
templates exist only for shared mocks. A real assignment or exam has no question IDs, so there is nothing to submit
it against.

### Finding the folder on any machine

`peer.find_share()` tries these in order and takes the first folder that holds `.peer_export`:
1. `--share` on the command line, or `SHARE_PATH` in the environment (or `.env` for the pipeline).
2. The folder `peer.py` itself is in. This is the usual case: classmates run the copy inside the share.
3. The folder found last time, cached in the person's own settings file. If it has gone, search again.
4. Every OneDrive folder on the machine:
   - on Windows, the `OneDriveCommercial`, `OneDrive` and `OneDriveConsumer` variables and the account folders in
     the registry;
   - on macOS, `~/Library/CloudStorage/OneDrive-*`;
   - elsewhere, `~/OneDrive*`.

   In each, it checks the usual places first (`usyd-course-notes`, `SHARED/usyd-course-notes`), then searches up to
   3 folders deep.

If nothing is found, the error says how to add the shortcut and how to pass `--share`.

### Commands

For classmates (run from the share, e.g. `python peer.py record ABCD1003 1`):

| Command | What it does |
|---|---|
| `where` | Shows the share and submissions folders it found, and your token |
| `record ABCD1003 1` | Asks for your marks per part (and the option you chose, for multiple choice), the minutes you used per question (optional), the month you sat it and how it was marked. Saves a copy on your machine and writes your file into `_submissions/` |
| `record ABCD1003 1 --from marks.csv` | Submits a filled-in copy of the results template instead |
| `report ABCD1003 1 Q3 "..."` | Reports an error in a question or its solution |
| `results ABCD1003 1` | The class results for that mock next to your own |
| `token` | Shows your token; `token --set <token>` reuses the same one on a second machine |

For you:
- `share_vault.py` does what it did before. It also:
  - writes the templates and `peer.py`;
  - reads `_submissions/`, validates each file and prints what it rejected and why;
  - publishes the class results;
  - lists new error reports in its output. Reports are for you to review and are never published automatically.
- To add your own attempt, use `peer.py record` like everyone else, with "marked by: ai" if `grade_practice` marked
  it. In S1, your own attempts join the pool automatically from `state/practice_check_data/`.

### What is collected, per attempt (nothing else)

| Field | Example | Note |
|---|---|---|
| question ID and part | `a3f9c2-Q1`, `c.ii` | from the export, never the question text |
| version | `5be01d` | a short hash of the question text; a regenerated or edited question gets a new one |
| marks awarded / available | 4.5 / 7 | per part where the paper has parts, else per question. Blank = not attempted |
| option chosen | C | multiple choice only, to see which distractors catch people |
| minutes used | 16 | optional, per question; compared with the exam-pace allocation |
| when it was sat | 2026-11 | year and month only, never the day (with a small class, an exact date could identify someone). The session comes from the paper (2026 S2) |
| how it was marked | self / ai | self-marked and AI-marked scores are counted separately |
| participant token | 8 random characters | made on the student's own machine; never a name, SID or email |

### Statistics

S0 (the pilot):
- **Facility** (mean fraction of the marks) per part and per question, with n shown. Nothing but n is published
  until a question has at least 5 attempts.
- **Discrimination:** the correlation between the score on a question and the score on the rest of the paper.
  Near zero or negative flags the question.
- **Multiple choice:** how often each option is chosen.
- **Time used** against the allocation: which questions blow the pace.
- **Flags** in plain words: nearly nobody or nearly everybody gets it, stronger students do no better, takes much
  longer than its allocation, has error reports.

S1 (after R2):
- the distractor flag, which needs the answer key read through `vault/paper.py`;
- groups by session and year, so a question's difficulty can be compared across cohorts. When a course changes
  lecturer or content, a question that suddenly gets much harder or easier shows it;
- early attempts against attempts in the exam month, which separates "hard question" from "sat before the topic was
  revised";
- self-marked against AI-marked.

Each group keeps the rule of at least 5 attempts. Later, once a question bank exists and there are about 30 or more
attempts per item model: a Rasch or two-parameter IRT model. That puts all items on one difficulty scale, so papers
of equal difficulty can be assembled.

### Privacy and integrity (idea1 says security matters)

- **Pseudonymous, not anonymous. Say so plainly.** OneDrive shows who created each file, to you and to everyone
  who can open `_submissions/`. The token keeps names out of the data and out of the published results. It does not
  hide who uploaded a file. The consent note in the share's README says this.

  If the group later wants real anonymity, switch the channel and keep the same CSV:
  - a Microsoft Form set to not record names;
  - a OneDrive "Request files" link, where uploaders can't see each other's files.
- **Voluntary and informed.** The consent note says:
  - what is collected;
  - that only aggregates are published;
  - who can see the raw files;
  - how to withdraw: delete your own files in `_submissions/`, or send your token and those rows are deleted.
- **Small-group protection.** Publish nothing but n for fewer than 5 attempts, and never per-person results, so
  nobody can be singled out. Dates are only ever year and month.
- **Practice data only.** Collect marks on the shared mocks and practice problems only. Never collect real exam
  questions, answers or marks, and never assessed work. That keeps it clear of exam confidentiality and of using it
  for anything assessed (the share README's integrity note).
- **Submissions are data, never code.**
  - Classmates can write only under `_submissions/`, so nobody can change `peer.py` or the material others read.
  - `share_vault.py` reads only `.csv` and `.md` files there, with a size limit, validates every row (known IDs,
    marks in range) and ignores everything else.
  - Its personal-data scan also runs on error reports before they are listed.
- **Retention:** raw submissions stay in `_submissions/` until the session's aggregates are final, then you delete
  that session's folder. The aggregates stay with the paper.

### Setting up the pilot (you, once)

1. Run `share_vault.py --dry-run`, then `share_vault.py`. The export creates `_submissions/`.
2. In OneDrive on the web: share `usyd-course-notes` with the classmates as **Can view**, then share
   `usyd-course-notes/_submissions` with the same people as **Can edit**.
3. Check with one classmate before telling the group:
   - they can add the shortcut;
   - `python peer.py where` finds the share;
   - `record` writes a file you can see;
   - they cannot change files outside `_submissions/`.

   If the subfolder permission doesn't hold through the shortcut, fall back to a separate folder shared Can edit and
   pass it with `--inbox`.

### Trial

One unit, one shared mock (ABCD1003 mock 1), a small group of classmates, before 2026-11-23.
- Check that `record` takes under 5 minutes.
- Look at which questions get flagged or reported.
- Ask whether the class results changed what people practised.

### Risks

- **Self-marking is generous and inconsistent.** Record how each attempt was marked, and compare a few
  self-marked attempts with AI marking.
- **Few submissions.** Make it optional and low effort, and show the result back ("you scored above the
  class mean on 4 of 5 questions"), which is the reason people will fill it in. With fewer than 5 attempts only n
  shows, so a very small trial group sees little. That is the price of the privacy rule.
- **Mixed versions.** A regenerated or edited mock changes the question text. The version hash catches this, and
  only rows that match the current version are counted.
- **No Python on a classmate's machine.** They fill in the CSV template by hand and drop it into `_submissions/`.
  The file name then serves as the token.
- **Excel changes the CSV** (encoding, separators). The reader accepts UTF-8 with or without a byte-order mark, and
  `record` avoids Excel altogether.
- **A classmate deletes or overwrites someone else's file** in `_submissions/`. OneDrive's version history and
  recycle bin restore it. A small group of people you know is the real mitigation; a Form is the alternative.

## Publishing the app without your data

**Done on 2026-11-20, before R1.** The code moved to this public repo with a fresh history. The original private
repo, whose history holds personal data (real marks, accounts, exam seats), is archived and stays private.

| Where | Holds | Visibility |
|---|---|---|
| This repo | code, prompts, tests, generic docs, `config.example/` | public |
| Your personal repo (private) | your `config/` (units, targets, study plan, `share.yaml`), logbook, planning, research | private |
| Your machine only | `.env`, `.secrets/`, `state/`, `out/`, the vault, `results.xlsx` | never in git |

- **Config comes from outside the repo.** Point `PIPELINE_CONFIG` at your config folder in `.env`, or copy
  `config.example/` to `config/`, which is gitignored.
- **Nothing personal in the code.**
  - Names became "you" or "the owner", and real unit codes became example ones (ABCD1001 and so on).
  - Paths are defaults (`OneDriveCommercial`, `%USERPROFILE%`, `PIPELINE_PYTHON`).
  - The patterns that name you live in your `share.yaml`.
- **Guard on every commit:** `scripts/check_personal.py`, installed as a pre-commit hook with
  `python scripts/check_personal.py --install-hook`. It checks the staged files against the generic patterns (student
  IDs, Gmail addresses, user folders) and your `share.yaml` lists. It prints only file, line and pattern number, never
  the matching text. It also runs the emoji check.
- **Commits use the GitHub no-reply address,** not a personal email (`git config user.email`).

Still to do: CI running the tests and the generic patterns (R5).

How marks move after the split:

| Kind | Where it lives | Leaves your machine? |
|---|---|---|
| Real assessment marks (`results.xlsx`, Canvas, `marks.py`) | OneDrive, `state/`, the vault | never; not the repo, not the share |
| Your practice attempts (`grade_practice`) | `state/practice_check_data/`, the vault | only as your own `peer.py` submission, shown to others as class aggregates |
| Classmates' practice marks | `_submissions/` in the share | only aggregates are published |

Checklist: BLUEPRINT Track B, "R" (after R1).

## Behaviour fixes queued for after the refactor

Found in the 2026-11-15 audit and left out of the refactor on purpose. Each needs a test first:
- `marks.py` will count S2 units twice once their marks appear in `TOTAL-MARKS`; any task with "exam" in its
  name counts as the final exam.
- Lecture labels shift when a new file reuses a number (L12 becomes L12a and L12b), which breaks ticks, recall
  file names and attempt names. Store the labels once and keep them.
- The nightly log is written partly in UTF-16 (PowerShell 5.1 `Tee-Object`).
- `no_emoji` also changes `( ` to `(` across whole files.
- Temporary copies of `results.xlsx` are never deleted.
- The share's personal-data scan is pattern-based; consider an allow-list of exported sections instead.

Fixed in S0, so no longer queued:
- the share sync deleted anything a classmate put in the folder;
- the share location was derived from your coursework path.
