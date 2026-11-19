# Exam handbook (textbook) design

The format every unit's exam handbook follows. `build_handbook.py --draft` writes it, you edit `handbook.md`,
and `--pdf` prints it. It's a living design: change it here first, add a row to the decision log, then change the
code (`scripts/build_handbook.py`, `scripts/book_index.py`, `prompts/handbook.md`).

## When it is built

**Only when you ask, for the subject you name:** `build_handbook.py` refuses to run without `--unit`, and
`refresh.ps1` never builds it. A unit whose exam is over is frozen, so its handbook and PDF are never redrafted
or rebuilt, even when the design changes. They stay exactly as they were when the exam was sat, unless you
ask for that unit with `--allow-sat`.

## Purpose

An exam-room reference for finding a method fast and applying it. It isn't for learning a topic. It replaces
printing every lecture slide (decided 2026-11-18).

## The way in: the A-Z index

You find things through the **A-Z index at the back**, not a table at the front.
- **What it holds:** section titles, "Use when" triggers, the hidden index phrases, terms, names, acronyms and
  symbols. Bold is the main entry; italic is a slide-figure page.
- **Extra terms and cross-references:** `print.terms` and `print.see` in the unit config.
- **Front of the book:** a single "Find it" line under the title gives the index, slide-figure and concepts
  pages. Edge tabs mark the groups of sections.

## Order of the printed book

1. **Title, "Find it" line, exam line** (conditions, materials, format, pace in min/mark).
2. **Constants and conversions:** a table of quantity, value and use.
3. **Numbered sections**, one per problem type or lecture, in the backbone's order. Each section, exactly:
   - `## <n>. <problem type> (<lectures>)`
   - **Use when:** the triggers, comma-separated. They feed the index, so use the exam's own wording.
   - Formulas, one per display line, each with its units and condition and an optional reference.
   - **Steps:** for multi-step problem types only, as terse imperatives.
   - **Trap (you):** only mistakes recorded in your error log, recall checks, practice checks or marker
     comments, each ending with its source in brackets, for example `[error log 2026-11-14]`. The build
     relabels any without a source as plain traps.
   - **Trap:** common traps from the recall notes.
4. **Concepts:** a table of term and one-line meaning, covering the definitions and misconceptions short-answer
   or multiple-choice questions test.
5. **Concepts in depth (optional, on request):** `## Concepts in depth`, one `### <n>. <topic> (<lecture>)` per lecture.
   Each concept has a name line, then **Idea**, **Why it works**, **What changes if**, **In the exam** and an optional
   **Don't confuse**. Claims cite slide pages; anything not on the slides ends with †. Concept names feed the index.
6. **Worked examples (combined print only):** `--pdf --with-worked-examples` writes a separate
   `handbook_with_worked_examples.pdf`, with `worked_examples.md` as a section here. Page numbers run through the
   whole book, each topic section points to its examples, and the index marks them as `WE 24`. This print uses the
   `combined` layout to save paper: tight spacing throughout, with the worked examples in two flowing columns.
7. **Slide figures:** every slide the handbook cites, 6 per page, grouped by section. Each section's heading says
   where its figures are. Title and divider slides are skipped.
8. **A-Z index.**

Every page has a running header and an edge tab, on the outer edge for double-sided printing. Tab groups come
from `print.tabs` in the unit config, or are made automatically in fives.

## Kept in handbook.md, never printed

- **Index phrases** (`## Index phrases`; older handbooks call it `## Lookup`): exam phrasings mapped to sections,
  such as "receiver accepts or discards" → §6. They only feed the index. Regenerate them with
  `--index-phrases` after renaming or adding sections.
- Your work lists: `## To add`, `## To do`.

## References

| Written | Means | Where it comes from |
|---|---|---|
| `(L7 p.12)` | lecture 7, slide page 12 | recall notes only; the build embeds that slide in the figure appendix and reports pages that don't exist |
| `(Notes p.25)` | page 25 of the unit's reference notes (the backbone's own document) | the formula sheet's page column |
| `†` | textbook formula, not on the slides | backbone marking |

## Backbone and sources

- **Backbone:** a formula sheet in `sources/*formulas*.md` with numbered sections (`## 1. Title (L5)`), usually a
  shared sheet or condensed notes converted by `convert_reference.py`.
- **Quick finders and contents pages** go in a separate, non-`formulas` file.
- **Lectures the sheet doesn't cover** (the "gap") get new sections drafted from the recall notes.

## Length

- **Open book:** complete, but every line must be usable in the exam.
- **N x A4 notes sheet:** must fit 2N sides (the cheat-sheet mode, not built yet: BLUEPRINT).

## Provenance

- **Front matter:** `drafted`, `model` and `backbone`, plus an `edits` list with one line per change by Claude, a
  script or another model (date, who, what).
- **Your own edits** need no log line; the file is yours.
- **A redraft** never overwrites: it goes to `handbook_draft_<date>.md`, and a draft that is set aside keeps its
  file (e.g. `handbook_draft_superseded_<date>.md`).

## Build checks (`--pdf`)

| Check | Effect |
|---|---|
| LaTeX outside `$...$` | the build stops and lists the lines |
| a slide reference past a lecture's last page | reported; the slide is skipped |
| lines over 40 words, near-repeats | reported, for trimming |
| lines too wide for the column | reported |

## Decision log

| Date | Decision | By | Why |
|---|---|---|---|
| 2026-11-14 | Textbook print: two columns, slide-figure appendix, A-Z index, edge tabs, running headers | owner + Claude | ABCD1001 open-book exam |
| 2026-11-18 | Make a textbook instead of printing every slide, for every open-book unit | owner | 691 pages of slides vs a 17-page book plus condensed notes |
| 2026-11-18 | "Trap (you)" must cite its source in your record, or it is relabelled | owner (accountability) + Claude | the first ABCD1002 draft invented 12 |
| 2026-11-18 | `(Notes p.N)` for the backbone's pages, `(Lx p.N)` for slides only | Claude | notes pages were cited as slides |
| 2026-11-18 | Built only on request, per subject; sat units are frozen. Design changes apply to upcoming units only (ABCD1001's sat-exam book keeps its Lookup) | owner | don't regenerate or alter a course whose exam is over |
| 2026-11-18 | The front Lookup table was dropped; the A-Z index is the way in; a "Find it" line gives the back-matter pages; the phrasings live on as hidden index phrases | owner (decision), Claude (keeping the phrases) | the index is much more useful; dropping the phrasings outright cut the index from 325 to 262 entries |
| 2026-11-19 | Optional "Concepts in depth" section, and a separate combined print with the worked examples as a section | owner | ABCD1002: understanding for analysis questions, and one binder instead of two documents. `handbook.pdf` stays available without the worked examples |
