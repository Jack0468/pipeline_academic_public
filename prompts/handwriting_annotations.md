The attached PDF is part of an annotated export (OneNote, or a note app; pages {first}-{last} of
the original file) for {unit_code} {unit_name}. Most pages are printed lecture slides, or the
student's own typed assignment, with the student's HANDWRITTEN ink annotations on top and often in
the blank margin beside the page. Some pages are fully handwritten (e.g. a formula page).

Transcribe ONLY the student's handwriting. Do NOT transcribe the printed slide text, except for
a short context line that says what the slide is about.

Output Obsidian Markdown, nothing else. For every page that has handwriting, in page order:

### p. <original page number> · <5-10 word context from the printed slide>
Then each handwritten item as one of:
> [!mine]
> the student's note, verbatim as far as legible. Maths in LaTeX `$…$` / `$$…$$`.

> [!confused]
> any question, "??", "why?", circled or starred uncertainty, verbatim.

> [!check]
> a handwritten correction or a note that contradicts the printed slide (say what differs).

Rules:
- Original page number = position in this chunk + {offset}. Page 1 of this chunk is p. {first}.
- Skip pages with no handwriting entirely (don't list them).
- Keep the student's own words, abbreviations and arrows (→). Do not polish or expand them.
- Illegible parts: write `[illegible]`. Never guess formulas. If unsure of a symbol, add `(?)`.
- Diagrams/sketches: one line `[sketch: what it shows]`.
