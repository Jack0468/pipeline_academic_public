# Architecture

## Core goal

**A modular study system that you and your peers can each run on your own units, by changing configuration and not
code.** Each part does one job, can be replaced alone, and writes its output to one known place.

What that requires:

| Quality | Test |
|---|---|
| **Modular** | A change to one format or output location touches one module |
| **Easy to use** | A student needs one setup guide and a few commands, and an error says what to do next |
| **Safe** | Your notes are never overwritten, sat units are frozen, and a migration can be undone |
| **Honest** | Every generated file says what made it, and AI output is checked before it is trusted |
| **Cheap to re-run** | Unchanged inputs cost no AI calls |
| **Serves study** | Features are judged by time on retrieval and practice (README, "Design principle") |

## How it fits together

```
INPUTS (read-only)            PIPELINE (scripts/)                     OUTPUTS
OneDrive coursework  ─┐       config/units/*.yaml  what differs       Vault: notes and PDFs you use
Canvas               ─┤  ──►  paths.py             where files go ──► out/calendar: .ics exports
OneNote              ─┤       common.py            shared helpers     state/: caches, logs, backups
Google Calendar      ─┤       gemini.py            AI backend         OneDrive SHARED: peer export
results.xlsx         ─┘       one script per job
```

Python does the deterministic work. AI is used only for judgement, through `gemini.py` and the versioned `prompts/`.

## Repository layout

| Path | Holds | In git |
|---|---|---|
| `scripts/` | The pipeline | yes |
| `prompts/` | AI task instructions | yes |
| `config/` | Units, targets, study plan settings | yes |
| `docs/` | Guides, standards, designs ([index](README.md)) | yes |
| `.claude/skills/` | Project protocol for agents | yes |
| `state/` | Machine state: `manifests/`, `logs/`, `vault_backups/`, `vault_migrations/`, per-tool caches | no |
| `out/` | Exports for other programs: `calendar/` | no |
| `scratch/` | Claude's tests, probes and temporary output: `tests/`, `probes/`, `logs/`, `sandbox/` | no |
| `.secrets/`, `.env` | Credentials | no |

## Where generated files go

A script gets every location from `scripts/paths.py`. `unit_path(u, key)` returns a unit's note or folder in that
unit's layout, so old and new layouts both work.

| Script | Writes | To |
|---|---|---|
| `scaffold_vault.py` | hub, notes sheet, logs, plan starter, dashboard pages | unit folder, `practice/`, `00_dashboard/` |
| `gen_recall.py` | recall notes | `recall/` |
| `extract_handwriting.py` | transcriptions | `sources/`; chunks in `state/` |
| `convert_reference.py` | converted third-party notes | `sources/` |
| `gen_practice_exam.py`, `mock_writer.py` | mock papers and solutions | `practice/`; drafts and logs in `state/` |
| `grade_recall.py`, `grade_practice.py` | marking notes, error-log lines | `recall_checks/`, `practice_checks/`, `practice/error_log.md` |
| `ingest_intel.py` | exam intel | `reference/exam_intel.md` |
| `canvas_sync.py` | Canvas record, error-log lines | `reference/canvas.md`; raw data in `state/canvas/`; mirror in OneDrive |
| `fetch_unit_outline.py` | unit outline | `reference/unit_outline.md`; raw data in `state/outlines/` |
| `build_handbook.py` | handbook draft and PDF | `exam_room/` |
| `build_cheatsheet.py` | notes sheet (cheat sheet) draft and PDF, restricted exams | `exam_room/` |
| `worked_examples.py` | worked examples and PDF | `exam_room/`; the previous set to `_archive/`; raw replies in `state/` |
| `print_note.py` | a PDF of one note | beside the note |
| `link_slides.py` | slide copies (a cache of the lecture sources; stale copies removed), page links | `slides/`, links inside `recall/` and the check folders |
| `link_tutorials.py` | tutorial sheet, solution and notes copies (a cache), the tutorial index | `tutorials/`, `practice/tutorials.md` |
| `adopt_sources.py` | recall manifest entries, relinked and relabelled notes, moved old copies | `state/manifests/`, unit notes, `_superseded/` in the unit's OneDrive folder; log in `state/logs/` |
| `reconstruct_exam.py` | reconstructed exam | `EXAM_<year>/` |
| `marks.py`, `plan_study.py`, `progress.py` | marks, study plan, progress page | `00_dashboard/`; `state/marks.json` |
| `plan_study.py` | calendar files | `out/calendar/` |
| `gcal_sync.py` | sync state, busy times | `state/` |
| `export_onenote.py`, `svg_notes_to_pdf.py` | exported and annotated PDFs | `state/onenote/`, `state/annotated/` |
| `share_vault.py` | peer-safe export | `SHARE_PATH` in OneDrive |
| `migrate_vault.py` | backup, journal, moved notes, record | `state/vault_backups/`, `state/vault_migrations/`, `_archive/` |
| `nightly_ai.ps1` | run log | `state/logs/` |

Rules for a new script:
1. Ask `paths.py` for locations. Don't build output paths from string literals.
2. Build in a temporary folder and publish finished files (`pdf_tools.publish`).
3. Never overwrite a person's file. Move what you replace to `_archive/`.
4. Write front matter with at least `unit`, `type` and `source`.
5. Use `work_units()` so sat units stay frozen.
6. Add a row to the table above.

## Redundancy audit (2026-11-18)

Measured on the code and the vault before this change.

**Fixed now**

| Issue | Evidence | Fix |
|---|---|---|
| Output paths written out in each script | 18 scripts built unit paths by hand | `paths.py` and `unit_path` |
| Every PDF stored twice | 3 ABCD1002 PDFs were byte-identical in `out/` and the vault | One home, beside the note. The `out/` copies are in the migration backup |
| Live and superseded versions side by side | 17 loose files in the ABCD1002 folder, 4 of them old versions | Three notes at the top level; old versions in `_archive/` |
| Two copies of the Markdown-to-PDF steps | `worked_examples.py` and `scratch/lan/build_pdf.py` | `pdf_tools.py`, `print_note.py` |
| Identical section parser in two modules | `build_handbook.section` and `gen_practice_exam._section` | `common.md_section` |
| Dead links | All 12 hub slide links and 18 links in 9 recall notes pointed at moved files | Repaired by the migration |
| Flat `state/` and `out/` | Manifests and logs mixed with caches | `state/manifests/`, `state/logs/`, `out/calendar/` |

**Still open**

| Issue | Evidence | Plan |
|---|---|---|
| The mock format is parsed in several places | `## Question` appears 18 times in 5 scripts | REFACTOR_PLAN R2, `vault/paper.py` |
| Front matter and section handling are repeated | Separate front-matter parsing in 5 scripts | R2, `vault/markdown.py` |
| Commands double as libraries | `gen_practice_exam` is imported by 4 scripts, `pace_recall` by 4, `unit_inputs` by 2, `grade_recall` by 1 | R1, R4 |
| Command boilerplate | `--unit` in 18 scripts, `--allow-sat` in 15, stdout setup in 23 | R4, a single command entry point |
| Broad error handling | `except Exception` 15 times in 8 scripts | R5 |
| Two lecture trackers | The hub's table and `00_dashboard/progress.md`; the planner reads only `progress.md` | Decide after exams: keep `progress.md`, make the hub table links only |
| `notes_sheet.md` overlaps the handbook in open-book units | ABCD1002's sheet is still the empty starter | Create it only for restricted-notes exams |
| `past_papers.md` repeats the input list | Its "Found on disk" list duplicates `unit_inputs.py` and goes stale (1 dead link remains) | Keep the log table, drop the list for new units |
| Overlapping status documents | README section 0, BLUEPRINT and EXAM_SPRINT_SETUP each describe the current system | README becomes a short front page; the research moves to its own document |
| Scratch tests are the only tests | `scratch/tests/` is gitignored | REFACTOR_PLAN R0 |

## What stands between this and a peer using it

1. **Install.** The Python path is hard-coded in `refresh.ps1` and `nightly_ai.ps1`, and there is no packaged command.
2. **University specifics in code.** The time zone, outline URL and Canvas address (R5).
3. **Personal patterns.** Moved out of the code into the private `config/share.yaml` (2026-11-20); the code keeps
   only generic ones (SIDs, Gmail addresses, user folders).
4. **Setup.** A new student needs one guide from an empty folder to a first recall file. Today it is spread across
   `EXAM_SPRINT_SETUP.md`, `GCAL_SETUP.md` and `GEMINI_SETUP.md`.
5. **AI access.** The default backend assumes an AI Pro subscription. A peer needs a documented alternative (R3).
6. **Tests.** Other people's changes need a test suite and CI (R0, R5).

The sequence is in [REFACTOR_PLAN.md](REFACTOR_PLAN.md). It starts after the last exam on 2026-11-26, so nothing
changes under the remaining exam preparation. The one exception is the peer pilot (S0): classmates use only
`peer.py`, which needs nothing but Python, so it can come first.

## Sources for these choices

- [Cookiecutter Data Science: opinions](https://cookiecutter-data-science.drivendata.org/opinions/): immutable raw data, rebuildable outputs, secrets in `.env`, shared code in one place.
- [The Twelve-Factor App: config](https://12factor.net/config): settings that vary per machine live in the environment.
- [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir/latest/): caches and state are kept apart from user data.
- [Diátaxis](https://diataxis.fr/): tutorials, how-to guides, reference and explanation are separate kinds of documentation.
