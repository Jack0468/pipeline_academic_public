# Using pipeline_academic (exam sprint, as of 2026-11-09)

> The per-unit protocol (folders, inputs, intel, plan, textbook or notes sheet, mocks) is the project skill
> [.claude/skills/exam_prep/SKILL.md](../.claude/skills/exam_prep/SKILL.md). It is the same for every subject.

How the system works now and how to drive it day to day. History and reasons are in
[LOGBOOK.md](../LOGBOOK.md). Status is in [BLUEPRINT.md](../BLUEPRINT.md). Subsystem detail:
[GCAL_SETUP.md](GCAL_SETUP.md) (calendar) and [GEMINI_SETUP.md](GEMINI_SETUP.md) (AI backends).

## The system in one picture

```
 OneDrive .UNI-COURSEWORK            OneNote notebooks          Google Calendar (your accounts)
 slides, tutorials, results.xlsx     ink notes, exam-info pages  work, sport, uni …
        │                                  │ export_onenote.py         │ gcal_sync.py busy
        │                                  ▼                           ▼
        │   gen_recall.py ◄── Gemini (agy CLI) ──► extract_handwriting.py      state/gcal_busy.json
        │        │                                       │                          │
        ▼        ▼                                       ▼                          ▼
 marks.py ──► plan_study.py ◄── 00_dashboard/progress.md (your ticks) ◄── sleep, buffers, blocks
                 │
                 ├──► academic_vault/00_dashboard/study_plan.md   (Obsidian)
                 └──► out/calendar/study_plan.ics ──► gcal_sync.py push ──► "Exam study (pipeline)" calendar
```

Everything you read and write lives in the **Obsidian vault** `..\academic_vault` (start at
`00_dashboard/Home`; each unit opens at its `_unit.md`, and [VAULT_STANDARD.md](VAULT_STANDARD.md) explains the
folders). Everything the scripts produce is regenerated from inputs, so nothing breaks
if you skip a day.

## Daily loop
1. **Check today's blocks** in Google Calendar ("Exam study (pipeline)"; one colour per unit,
   exams red) or in `00_dashboard/study_plan.md`. Each block's description says what to do; the
   method is in `00_dashboard/how_to_study.md`.
2. **Do the block.**
   - **Coverage:**
     1. With the book closed, answer the lecture's recall prompts on paper, numbered like the
        prompts in `units/<unit>/recall/<CODE>_L<n>.md`.
     2. Scan the pages to `<unit OneDrive folder>\summary_recall\recall_L<n>.pdf`.
     3. Open the slides, fix the gaps, and do 1–2 tutorial problems.

     The nightly batch checks the scan (see "Recall checks" below).
   - **Practice:** mixed timed problems.
   - **Simulate:** a mock exam under exam conditions.
3. **Log every lost mark** in `units/<unit>/practice/error_log.md`. These feed the evening review
   and your notes sheet.
4. **Check in:** tick the lectures you covered in `00_dashboard/progress.md`, and bump
   `practice_blocks_done::` / `mocks_done::`.
5. **Re-plan:** `powershell -File scripts\refresh.ps1`. It:
   - syncs Canvas (new marks, marker feedback, announcements, newly posted slides);
   - pulls busy times from all your calendars;
   - re-plans from *now*;
   - updates the vault and pushes the calendar.

   Missed or sick days drop off and the remaining work is re-spread. If it no longer fits,
   `study_plan.md` shows a **"Not enough time"** list of lectures to skim.

Run `refresh.ps1` again whenever something changes: a new shift or event in Google Calendar, a new
mark in `results.xlsx`, a changed `confidence`, or new ticks.

## Recall checks (your handwritten attempts)
`grade_recall.py` runs in `nightly_ai.ps1`, or on its own with `--unit X`. For each new or re-scanned
`summary_recall/recall_L<n>.pdf` it makes one AI call with your attempt, that lecture's slides and
its recall file, and writes `units/<unit>/recall_checks/<CODE>_recall_L<n>.md`:
- **A verdict per prompt:** correct, partial, wrong, not attempted, or deferred.
  "Deferred" means you deliberately wrote "see notes sheet / open book" and wrote nothing wrong,
  which is a valid strategy for notes-allowed exams. Each verdict shows what you wrote, the check
  against the slides with page numbers, and the fix.
- **Lists:** "Misconceptions to fix", "For your notes sheet", and "Re-test tonight" (do these in
  the evening review).

It also feeds the rest of the system:
- wrong and partial items are appended to `error_log.md`.
- deferred items become checkboxes under "To add (from recall checks)" in `notes_sheet.md`.
- A score of (correct + ½ partial) / non-deferred prompts ≥ `recall_pass` (0.6) ticks the lecture in
  `progress.md`. Below that, the lecture stays in the coverage queue.
- `00_dashboard/recall_checks.md` lists every check with its score.

Extra attempts are fine: `recall_L1_2.pdf` and `recall_L1_2026-11-20.pdf` are checked separately.

## Practice checks (your timed, self-marked problem attempts)
Work a lecture's exam-style problems against their time allocations, self-mark in red, and scan to
`<unit OneDrive folder>\practice_q\L<n>_practice_<k>.pdf`. A mock-exam attempt goes to
`practice_q\mock_exam_<n>_attempt.pdf` and is marked against that mock's solutions.

`grade_practice.py` runs in `nightly_ai.ps1`, or on its own with `--unit X`. It makes one AI call
per new attempt and writes `units/<unit>/practice_checks/<CODE>_<stem>.md`:
- **Marks per part**, as a real marker would give them (method marks, carry-forward), with the
  correct answer recomputed;
- **The first error** in each part below full marks, and the fix, with a slide page;
- **Where your own self-marking** was right or wrong;
- **Answers to the questions** you wrote to yourself;
- **Time used** against each allocation, read from your clock notes (e.g. "1:38 left of 12:00");
- **Habits to fix**, and what to redo.

It also feeds the rest of the system:
- parts below full marks and each habit are appended to `error_log.md`;
- each attempt adds 1 to `practice_blocks_done` (or `mocks_done`) in `progress.md`, once;
- `00_dashboard/practice_checks.md` lists every attempt with its marks and timing.

## How the planner decides
- **Time available:**
  - Four 90-min blocks a day (09:00 / 11:00 / 13:30 / 15:30; Sundays 3) plus a 45-min review at
    19:30.
  - Only inside your waking hours: wake 07:00 + 1 h, until bed 23:00 − 1 h.
  - If an event ends after bedtime, the next day starts at least 8 h after it ends, plus that hour.
- **Busy time:**
  - Every event in your Google calendars counts, with 30 min clear on each side.
  - Blocks an event knocks out move to another gap the same day (" moved (busy)"). On busy days
    it also tries packing from the start of the day.
  - Events over 12 h, all-day events and events shown as "Free" are treated as markers, not
    commitments.
- **Who gets the time:**
  - Each unit's share ∝ EIHWAM leverage × exam mark needed × (6 − confidence) × backlog, where
    backlog is the share of lectures not yet ticked.
  - The 30 h before each exam is locked to that unit ((locked)).
  - Blocks inside a unit run coverage (untouched lectures, about 2 per block), then practice, then
    simulation, then a final light review.

## Occasionally
| When | Do |
|---|---|
| You want a mock exam | `python scripts\gen_practice_exam.py --unit ABCD1001` (`--n 2` for two, `--research` to draw on textbooks/other universities). Output: `practice/mock_exam_<n>.md` (sit it) + `_solutions.md` (mark with it). On request only; never scheduled |
| You hear something about the exam (a Reddit or Discord post, a group chat, a friend who sat it) | Screenshot or save it into the unit's OneDrive `exam_info_research\` folder. For something said in person, write a short `.md` there: who (their role, not a name), when, which year they sat it, what they said. Then `python scripts\ingest_intel.py --unit X` (or wait for `nightly_ai.ps1`). Read `units/<unit>/exam_intel.md`: bottom line, claims with confidence, what to drill and print. The next mock and handbook draft use it automatically. Paste its suggested `exam.format` into the config only if you believe it |
| New ink in OneNote you want transcribed | `python scripts\export_onenote.py` (re-exports changed sections only), then `powershell -File scripts\nightly_ai.ps1`. A re-exported section is re-transcribed in full |
| Annotated slides came out as an SVG, and every conversion (pdf24, Inkscape, the browser) shows the ink but blank slides | The note app embedded the whole PDF as a fake PNG once per page. List the SVG under `sources.annotated_svg`, and add `${STATE}/annotated/<unit>/*.pdf` to `sources.handwritten`. `refresh.ps1` then rebuilds it (`svg_notes_to_pdf.py`, 0 AI calls: real slide underneath, ink on top, margin notes kept) and `nightly_ai.ps1` transcribes it |
| New lecture PDF in OneDrive | `nightly_ai.ps1` (makes its recall file); `refresh.ps1` adds it to `progress.md` |
| New mark | Enter it in `results.xlsx`, then `refresh.ps1` (marks → required exam % → allocation) |
| Unwell / lighter day | Set `max_blocks_per_day: 2` in `config/study_plan.yaml`, run `refresh.ps1`; remove it after |
| Calendar says "Not logged in" | Testing-mode Google tokens expire after 7 days: rerun the `auth` commands in GCAL_SETUP.md |
| Stuck on a tutorial question | Use the hint ladder (README §5.4). Step 4 is the hint-only Gem from `prompts/hint_tutor.md`; it gives hints, never answers |
| Setting up a tutor (Gem, NotebookLM, an agent) for a unit | Give it `units/<unit>/_unit_outline.md` first. It's written as tutor context: learning outcomes, the weekly schedule, assessments (exam weight, format, hurdles) and the readings |
| New unit or new semester | Add `config/units/<unit>.yaml`, then `refresh.ps1`. The outline is fetched from the URL convention (set `outline_url:` if the page lives elsewhere) |

## Configuration (what to edit, where)
| File | Key | Meaning |
|---|---|---|
| `config/study_plan.yaml` | `blocks`, `review_block`, `day_overrides` | preferred block times; Sunday keeps blocks 0–2 |
| | `sleep: wake/bed/min_sleep_hours/morning_buffer_min/wind_down_min` | waking window and late-night recovery |
| | `busy_buffer_min` (30), `max_busy_hours` (12) | gap around events; longer blocks are ignored as markers |
| | `reflow`, `reflow_block_min`, `break_min`, `max_blocks_per_day` | moving knocked-out blocks; daily cap |
| | `lock_hours_before_exam` (30), `exam_buffer_before/after_min` (90/60) | exam run-up and rest |
| | `phases`, `coverage_max`, `lectures_per_block` (2) | how a unit's blocks split; when triage warns |
| | `busy:` | one-off commitments *not* in Google Calendar |
| `config/units/<unit>.yaml` | `confidence` (1–5) | your self-rating; lower = more time |
| | `exam:` start/venue/seat/conditions/materials, `scope`, `format` | timetable data; `scope`/`format` steer mock exams |
| | `exam.total_marks` (or `exam.minutes_per_mark`) | exam pace for  allocations. ABCD1001 = 60 (2 min/mark); the others are **assumed 100**, so correct them from the exam cover page |
| | `onenote: notebook/sections/scope_pages/attempt_sections` | what export_onenote.py exports. `attempt_sections`: a section of your from-memory attempts, exported ONE PDF PER PAGE to `state/onenote/<id>/attempts/` and named from the page title: "L 3" is a free-recall attempt (`recall_L3.pdf`, marked by grade_recall.py), "L 3 qs" an attempt at the recall file's exam-style problems (`L3_practice_1.pdf`, grade_practice.py; "L 3 qs 2" for a second). Add `${STATE}/onenote/<id>/attempts/recall_*.pdf` to `recall_attempts` and `.../L*_practice_*.pdf` to `practice_attempts`. A page is exported only once it has been left for `attempt_settle_minutes` (default 60), so a page you are still writing is not marked |
| | `sources: lectures/handwritten/practice/tutorial_solutions/tutorial_work/own_work/annotated_svg/intel` | globs under the unit's OneDrive folder (`${STATE}` allowed). `tutorial_work` = scans of your own tutorial working with no transcription yet (e.g. `tutorial/tut_*.pdf`): the attempts checklist marks their tutorial "scan on file" until `extract_handwriting.py` reads them (add them to `handwritten` for that). Lectures and tutorials point at the Canvas mirror (`canvas/<module>/`). `tutorial_solutions` = the course's own tutorial solutions; the glob may be broad (e.g. every tutorial file in the mirror): only files with a solutions heading or "solution" in the name count, and `link_tutorials.py` links each from the page where its answers start. `intel` = word on the street for `ingest_intel.py` (default `exam_info_research/**/*`). `reference` = third-party study notes, such as shared formula sheets and condensed notes (default `xtra_resources/**/*.pdf`): mocks read them below the slides, and `convert_reference.py` makes a handbook backbone from them. Both defaults work with no config (`common.DEFAULT_SOURCES`). `scope` = official exam documents (e.g. `canvas/Final Exam/*.pdf` from the Canvas mirror): mocks, reconstructions and the intel check read them with the OneNote scope pages. `annotated_svg` = note-app SVG exports to rebuild as PDFs (see "Occasionally"). `lectures` = typed slides (recall files, coverage blocks, mocks); `practice` = question sheets (briefs, tutorials, quizzes, problem sets); `own_work` = your typed submissions and reports (mocks re-test what you got wrong, alongside the Canvas marker comments) |
| | `references:` | textbooks/sites mock exams may draw on |
| `config/targets.yaml` | `target_eihwam` | drives "exam % needed" |
| `.env` (not committed) | `GEMINI_BACKEND=agy`, `AGY_MODEL` | AI backend (GEMINI_SETUP.md) |
| | `GCAL_BUSY_CALENDARS`, `GCAL_IGNORE_CALENDARS`, `GCAL_LONG_EVENT_HOURS` | calendar inputs (GCAL_SETUP.md) |

## Commands
Python = the `pipeline_academic` conda env (create it with `conda env create -f environment.yml`; the
`.ps1` runners look in `%USERPROFILE%\.conda\envs\`, or set `PIPELINE_PYTHON`). Run from the repo root.

| Command | AI calls | What |
|---|---|---|
| `powershell -File scripts\refresh.ps1` | 0 | gcal busy → marks → plan → vault → gcal push. **The one you run daily.** It never builds handbooks: those are on request, per subject |
| `powershell -File scripts\nightly_ai.ps1` | ≈1 per new lecture / 20 PDF pages | recall + handwriting for anything new, unit by unit in exam order; cached, so re-runs are cheap |
| `python scripts\gen_practice_exam.py --unit X [--n 2] [--research]` | 3-7 per mock | mock exam + checked solutions, written by one agent per section (written answers first, then multiple choice), then checked and rewritten until clean (up to 3 rounds): no repeats of this paper, earlier mocks or your practised problems; the multiple-choice answer key is drawn balanced over A-D beforehand and the correct option must not be the longest; no lecture over 20% of the marks. About 40% of the marks target your **weak points**; numbers come from **random draws**; each quantity is given once, in the form industry quotes (you do the conversions). The paper never carries answers: the key, the seed and the list of open issues are in the solutions file (the paper only says how many issues). Each run writes a **generation log** to `state/mock_logs/<unit id>/mock_exam_<n>.md` (+ `.json`, and `state/mock_logs/index.md`): every question sent back, why, before and after, and where each final question came from. It contains answers, so read it after sitting the mock. Research and design: [EXAM_WRITING.md](EXAM_WRITING.md) |
| `python scripts\share_vault.py [--unit X] [--include-handbook] [--allow-assignment-derived] [--dry-run]` | 0 | peer-safe copy for classmates → `OneDrive\SHARED\usyd-course-notes\<CODE>\<YEAR>-S<n>\` (recall files, mock exams with solutions, outlines, study method; handbook only with the flag). Personal data never included: mocks lose their weak-point targets and references to your own work, and mocks that mirror an assignment are held back until you review them. Re-run after every new mock; share the folder read-only. Guide: [SHARING.md](SHARING.md) |
| `python scripts\reconstruct_exam.py --unit X [--sections A\|B\|AB] [--recall FILE] [--force] [--dry-run]` | 2 | after an exam: write what you remember in `units/<unit>/EXAM_<year>/<...recall...>.md`, and this rebuilds the paper in the real format plus checked worked solutions (`exam_<year>_reconstructed[_section_b].md` and `_solutions.md`, next to the notes). Recalled values are kept exactly; anything added is tagged `[inferred]`; ambiguous questions are solved both ways. Real exam content, so the files are `share: never` and never exported. Any unit; skips recall files it has already done (`--force` redoes) |
| `python scripts\unit_inputs.py [--unit X] [--brief]` | 0 | **The single source of truth for inputs.** Lists every input of a unit (scope, lectures, practice, own work, reference notes, handwritten PDFs and transcriptions, recall files, intel, outline, Canvas, handbook), where it lives and which script reads it. Checks for the known breakages: lecture files moved (fewer lecture PDFs than recall files), two PDFs for one lecture, dead globs, untranscribed scans, a stale intel note. Also lists the PDFs no glob covers. `gen_practice_exam.py` uses it and stops on a CRITICAL problem (`--ignore-input-check` overrides). Run it after moving files, adding material or starting a unit |
| `python scripts\convert_reference.py --unit X --pdf FILE --pages 2,73-76 --name N --author "WHO"` | 1 | faithful Markdown of a third-party formula sheet or condensed notes → `units/<unit>/sources/<CODE>_formulas_<N>.md` (provenance front matter, `share: never`): the handbook's backbone. Check page 1 by hand |
| `python scripts\ingest_intel.py [--unit X] [--force] [--dry-run]` | 1 per unit with new items | word on the street → the unit's `reference/exam_intel.md`. Reads every screenshot, PDF and `.md`/`.txt` note in the unit's `exam_info_research\` (config `sources.intel`) with the outline, Canvas and the formulas per lecture. Each item is classed first-hand, second-hand, analysis or rumour; an analysis of a post is not counted as a second source. Each claim gets a confidence: strong, likely, weak or conflicts (official wins). It also maps the described past paper to lectures and slides, says what to drill and what to print in full, suggests `exam.format` text (not applied), lists open questions, and transcribes the screenshots without usernames. `gen_practice_exam.py` and `build_handbook.py --draft` pick it up. `share: never`; never exported. Skips units whose items haven't changed; raw replies in `state\exam_intel\` |
| `python scripts\lecture_examples.py --unit X [--force] [--dry-run]` | 1 per new or changed lecture | the slides' OWN examples and exercises (the recall files only have new problems modelled on them): per item the slide page, the question with its given values and the page where the slides answer it, never the answer, into `practice/lecture_examples.md`. Reads image-only slides too (vision). Cached by PDF hash; `nightly_ai.ps1` runs it |
| `python scripts\worked_examples.py --unit X [--no-verify] [--pdf-only]` | 6 | on request only. One worked example per calculation type listed in the unit's `exam_room/worked_examples_types.md` (a table you or Claude write: # / Type / Lectures / Why): how to recognise it, the method with slide references, an exam-style worked solution, a reversed variant and traps. Uses the slides, tutorial solutions, formula sheet, recall notes and intel; never reuses practised problems or mock papers. A second AI pass re-solves everything; **recompute the key answers with code before printing**. Output: `worked_examples.md` + `exam_room/worked_examples.pdf` (contents page by type, each type on a new page). An earlier set is kept as `worked_examples_<date>_previous.md` |
| `python scripts\build_handbook.py --unit X --draft` | 4 (parts) | exam handbook → the unit's `exam_room/handbook.md`, in the format of [HANDBOOK_DESIGN.md](HANDBOOK_DESIGN.md): constants, per problem type (use when, formula with units and condition, steps, **Trap (you)** from your error log and checks), multiple-choice concepts, and hidden **index phrases** (exam wordings, not printed, which feed the A-Z index; regenerate only those with `--index-phrases`, 1 call). Backbone = a formula sheet in `sources/*formulas*.md`. Drafted once, then **yours**: never overwritten (a re-draft goes to `handbook_draft_<date>.md`) |
| `python scripts\build_handbook.py --unit X --pdf [--layout roomy/compact] [--no-figures] [--no-index]` | 0 | prints your current `handbook.md` as a small **exam textbook** → `exam_room/handbook.pdf`, beside the note. Order: handbook → **slide figures** (every slide page the handbook cites "(L7 p.12)", 6 per A4 page, by section) → **A-Z index** (terms, names, acronyms, symbols; bold = main entry, italic = figure page; no AI). The A-Z index is the way in: a "Find it" line under the title gives the index, figure and concepts pages (there is no front Lookup table since 2026-11-18). Each section says where its figures are; every page has a running header and a coloured **edge tab** (groups from `print.tabs` in the unit config, on the outer edge for double-sided printing; extra index cross-references under `print.see`). **roomy** (default): room to annotate by hand; **compact**: fewest pages. Re-run after you edit the handbook, then print double-sided. Print rules, the same for every unit: your "To add" / "To do" lists stay in the .md but don't print; slides are embedded as images, so equations look the same in every viewer; title and divider slides are skipped; formulas too wide for a column wrap as text or shrink to fit. Each build reports a **conciseness check** (lines over 40 words, repeated lines) and any line still too wide, for you to trim in the .md |
| `python scripts\build_cheatsheet.py --unit X --draft` / `--pdf` | 1 / 0 | restricted exams (`exam.notes_sheet: "N x A4"`) only, on request: the notes sheet you take in → the unit's `exam_room/cheatsheet.md` and `cheatsheet.pdf`. `--draft` condenses `handbook.md` (draft that first), your `notes_sheet.md`, the worked examples' "Recognise it" lines and your error log into a **method chooser** ("the question gives X → method, §n") plus one compact block per problem type; never overwrites (a redraft goes to `cheatsheet_draft_<date>.md`). `--pdf` prints three columns at the largest font (7 to 5.5 pt) that fits 2N sides, and fails without writing if it can't: cut lines and re-run. Format: [HANDBOOK_DESIGN.md](HANDBOOK_DESIGN.md) |
| `python scripts\grade_recall.py [--unit X] [--force] [--dry-run]` | 1 per new attempt | check `summary_recall/recall_L<n>.pdf` or a OneNote attempt page (also in nightly_ai.ps1). Sends only the slide pages the recall file's prompts cite (grade_practice.py likewise for its problems), cached in `state/slide_subsets/` |
| `python scripts\pace_recall.py [--unit X]` | 0 | add time allocations to every exam-style question (`[2 marks, 4 min]`, a per-problem total, a section total) at the unit's exam pace = `writing_min / total_marks` (ABCD1001 2 min/mark; the others 1.2 min/mark until `total_marks` is known). Also does mock-exam question headings. Run by `refresh.ps1` and `nightly_ai.ps1`; re-run after changing `total_marks` |
| `python scripts\link_slides.py [--unit X]` | 0 | copy lecture PDFs into `units/<unit>/slides/` (gitignored) and turn every "p. N" in recall files and checks into a link that opens the slides at that page in Obsidian. Run by `refresh.ps1` and `nightly_ai.ps1` |
| `python scripts\export_onenote.py [--unit X] [--force] [--list] [--attempts-only]` | 0 | OneNote sections and scope pages → PDF in `state\onenote\` (OneNote desktop must be installed). Attempt sections go page by page, and only pages whose OneNote modified time is newer than their export are published (re-exported PDFs differ byte for byte every time, so OneNote's own page metadata decides). `--attempts-only` (run by nightly_ai.ps1) exports just those pages |
| `python scripts\canvas_sync.py [--unit X] [--no-files] [--videos]` | 0 | Canvas (read-only, `CANVAS_TOKEN` in `.env`) → `units/<unit>/canvas.md`: every mark + marker comment, "where marks were lost", exam-related announcements in full; appends marker comments to `error_log.md` (once each); downloads slides posted in announcements (pptx → PDF) into the unit's lecture sources; flags config `assumptions` that Canvas now has real marks for. **Mirrors the course content** into the unit's OneDrive folder, `canvas\<module>\`: every module file, plus files linked from module pages. **The mirror is the ground truth for lectures and tutorials** (since 2026-11-20): every file is saved, even if you have a copy elsewhere, and the unit config's lecture and tutorial globs point at it. Images and videos are skipped (`--videos` adds videos). Only files changed on Canvas are fetched again, and nothing is deleted. `canvas.md` lists every file, with a to-do of saved files no `sources:` glob covers yet; add globs for the ones that matter. To move a unit's lecture source to the mirror, use `adopt_sources.py` (below). `refresh.ps1` runs it |
| `python scripts\canvas_sync.py --past [--dry-run] [--unit CODE ...] [--videos]` | 0 | **Past courses**, before Canvas access closes: the same module-files mirror for every course in `state\course_inventory.json` (run `course_inventory.py` first) whose Canvas access is still open and that has no unit config (configured units use the normal sync, so sat units stay frozen). Files go to the unit's OneDrive folder (its `.old` folder first; a new `.old\<YEAR>_<SESSION>_<CODE>` if it has none), under `canvas\<module>\`. Nothing else is fetched or written for these courses. `--dry-run` lists the file count and size per course first. Re-runs fetch only changed files. The mirror is in OneDrive, not the vault: use OneDrive's "Free up space" to keep it online-only |
| `python scripts\adopt_sources.py --unit X [--apply] [--map OLD=NEW] [--supersede GLOB...]` | 0 | after pointing a unit's `sources.lectures` at new files (usually the Canvas mirror): **keeps the recall files**. Per lecture: ADOPT (record the new file as the recall file's source, so `gen_recall.py` doesn't regenerate it, and rewrite every link to the old file), RELINK (same bytes, new place), RENAME with `--map` (e.g. `L6a=L6` when two uploads of one lecture became one; `(L6a p.N)` references are relabelled; a second copy goes to `_archive/`), NEW (no recall file yet). **CONFLICT**: a new file has the bytes another lecture's recall file was built from, so the new names number the lectures differently; `--apply` then changes nothing, unless `--by-content`: each recall file then takes the label its slides have under the new names (RENUMBER, all at once, with every link, `(L5 p.N)` reference, hub row and progress row relabelled; other units' progress rows are untouched), and a recall file whose label is taken and whose source has no counterpart goes to `_archive/` (DISPLACED). `--supersede` moves the old copies into the unit folder's `_superseded/` (logged in `_superseded/MOVED.md`), only where the sources hold the same bytes, or a file of the same number and the same kind (question sheet for question sheet, solutions for solutions: Canvas often gives a sheet and its solutions one name) with at least 90% the same text; scans with no text (your own notes) always stay; links follow the files. Dry run by default; page numbers are not renumbered, so check pairs whose page counts differ. Log: `state/logs/adopt_sources_*.md` |

| `python scripts\fetch_unit_outline.py [--unit X] [--if-missing] [--offline]` | 0 | official unit outline from `https://www.sydney.edu.au/units/<CODE>/<YEAR>-S<n>C-ND-CC` → `units/<unit>/_unit_outline.md` (tutor context) + `state\outlines\<CODE>.json`; checks the exam weight against results.xlsx and flags hurdles. `refresh.ps1` runs it for units that don't have one yet |
| `python scripts\course_inventory.py [--no-canvas]` | 0 | every unit you have taken, from the results sheet (year, session, unit, cp, level; never marks), Canvas (all enrolments, active and completed) and the OneDrive unit folders (`.UNI-COURSEWORK` and `.old`), with what the pipeline has for each (config, vault folder, concept index). Flags units missing from a source, a Canvas course with no mirror yet, closed Canvas access and unknown sessions. Writes a private page, `PIPELINE_PRIVATE/course_inventory.md` (default the vault's `_pipeline/`, never exported), and `state\course_inventory.json`; prints counts only |
| `python scripts\gcal_sync.py busy\|push\|status` | 0 | calendar steps on their own; `busy` lists every event it counts and every one it skipped |
| `python scripts\gcal_sync.py auth [--account L] --discover --events` | 0 | Google logins (GCAL_SETUP.md) |
| `python scripts\marks.py [--target 80]` | 0 | required exam marks → `00_dashboard/marks.md` |
| `python scripts\plan_study.py` | 0 | plan only (no calendar push) → `study_plan.md`, `out\calendar\*.ics` |
| `python scripts\check_vault.py [--unit X] [--brief]` | 0 | checks units on the folder standard: file places, properties, links ([VAULT_STANDARD.md](VAULT_STANDARD.md)) |
| `python scripts\migrate_vault.py --unit X [--apply] [--rollback]` | 0 | moves a unit to the folder standard, with links rewritten, a backup and an undo. Dry run by default |
| `python scripts\print_note.py --unit X --note <path in unit> [--title T]` | 0 | prints one note to a PDF beside it |
| `python scripts\scaffold_vault.py` | 0 | create missing vault pages; appends new lectures to `progress.md`; never overwrites |
| `python scripts\svg_notes_to_pdf.py [--unit X] [--force]` | 0 | rebuilds `sources.annotated_svg` into `state\annotated\<unit>\<name>_annotated.pdf` (cached by SVG hash; run by `refresh.ps1`) |
| `python scripts\gen_recall.py [--unit X] [--force]`, `extract_handwriting.py [--unit X]` | as above | the two halves of `nightly_ai.ps1` |
| `python scripts\gemini.py` | 0 | list models the API key can use |

## Where data lives
- **Inputs (read-only to scripts):**
  - OneDrive `.UNI-COURSEWORK\<unit>\`.
  - `results.xlsx`, copied to temp before reading because it's usually open in Excel.
  - OneNote, Google Calendar.
- **Vault `..\academic_vault`:**
  - Unit folders follow [VAULT_STANDARD.md](VAULT_STANDARD.md). Units not yet migrated keep the outline, Canvas, intel
    and handbook files directly in the unit folder.
  - *Generated, don't edit:* `00_dashboard/marks.md`, `study_plan.md`, the unit's `reference/` notes,
    `recall/*.md`, `practice/mock_exam_*.md`.
  - *Generated, your corrections kept:* `sources/handwritten_*.md`. Fix a misread word or tick an open question
    in place, or rename the file: `extract_handwriting.py` finds a transcription by its `original:` line and
    never overwrites your changes while the scan is unchanged. If the scan changes (a new OneNote export), your
    edited copy goes to `_archive/` before the new transcription is written.
  - *Yours:* `00_dashboard/progress.md`, `error_log.md`, `stuck_log.md`, `notes_sheet.md`,
    `_unit.md`, `past_papers.md` (the scaffold never overwrites these).
  - *Yours and Claude's:* `units/*/exam_plan.md`, the per-unit exam plan. No script writes it: `scaffold_vault.py`
    creates a starter for an upcoming unit only if there is none, and `plan_study.py` copies its **Summary** into
    `study_plan.md` (so the generated page shows every plan, and regenerating it loses nothing).
    Provenance rules:
    - each section starts with who wrote it and from what;
    - facts carry tags ([official], [config], [intel], [notes], [me], [Claude], [agy]);
    - your own words go in `[!mine]` callouts;
    - every change, by anyone, adds a change-log row (the vault isn't under git, so this is the record).
- **`out\calendar\`:** `study_plan.ics` / `exams.ics`. They're what gets pushed, and a manual-import fallback.
  PDFs live in the vault, beside their notes.
- **`state\`:** caches (`manifests\`, `logs\`, vault backups, PDF chunks, transcription parts, OneNote exports, `gcal_busy.json`,
  `gcal.json`). Gitignored. Deleting it causes re-processing, not data loss.
- **`.secrets\`:** Google OAuth client and tokens. Gitignored; never commit.

## Sat units are frozen (since 2026-11-18)

Once a unit's exam is over, no script regenerates or alters anything about it: recall files, transcriptions, the
handbook and its PDF, mocks, Canvas data and mirror, slide copies, vault files. Content-writing scripts print
"exam sat ...; frozen, nothing changed" and skip it. `nightly_ai.ps1` and `scaffold_vault.py` only loop over
upcoming units. To change a sat unit on purpose, name it and add `--allow-sat`. Read-only tools (marks, plan,
share, `unit_inputs.py`) still see every unit. `reconstruct_exam.py` is for sat exams and only adds new files.

## Gotchas
- **"Method: p. 23" and "(p. 15)"** in recall files and checks are links: click one to open that
  lecture's slides at that page inside Obsidian. Page numbers are the PDF's own page numbers, not
  the numbers printed on the slides.
- **AI output is a draft.** Recall answers and mock solutions are AI-written. Spot-checks were
  correct, but the slide page each item cites is the authority.
- **Handwriting open questions** are a checklist at the top of each `sources/handwritten_*.md`.
  That file is regenerated if the section is re-transcribed, so copy any ticks you care about to
  `progress.md` or `_unit.md`.
- **Don't edit events in "Exam study (pipeline)".** The next push overwrites them. Change an input
  instead: an event in your own calendar, the config, or `progress.md`.
- **Lecture numbering** comes from file names. Two files with the same number become `L7a`/`L7b`,
  and files with no number keep their name (e.g. the ABCD1003 Bode notes).
- **No past papers exist** for these units. Mock exams replace them; ABCD1001's follow the real
  format from your OneNote INFO page (20 multiple-choice at 1 mark + 6 extended, 60 marks).
