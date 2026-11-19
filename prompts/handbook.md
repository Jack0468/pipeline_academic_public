Write the exam handbook for {unit_code} ({unit_name}): the one document the student opens in the exam to
find the right formula, constant or method fast. Exam: {conditions}; materials: {materials}; format: {format};
pace: {pace}. Length budget: {budget}.

Sources (text blocks), in order of authority:
1. FORMULA SHEET: a vetted formula sheet grouped by problem type. It is the backbone: keep its coverage,
   formulas, conditions and "dagger" (textbook-only) markers.
2. RECALL NOTES: per lecture, "notes-sheet candidates" and "common traps". Use them to add anything
   the formula sheet misses, especially lectures it doesn't cover ({gap}).
3. STUDENT ERRORS: the student's own error log, recall-check misconceptions and practice-check
   habits. Every distinct error becomes a **Trap** line next to the formula it concerns, marked
   "(you)". These matter most: they are the mistakes this student actually makes.
4. UNIT OUTLINE: learning outcomes and weekly topics, to check coverage.
5. EXAM INTEL (if present): students' unverified reports of past papers. It only decides depth (what
   gets full worked detail), never what is left out; its own header says how.

Style (strict):
- No filler words, no introductions, no full sentences unless a condition needs one. Fragments,
  arrows, symbols. Actions are imperatives: "convert to linear first", "check quadrant".
- Every formula carries its units and the condition it holds under, inline:
  `$\lambda = c/f$ (m)`, `$R > 2D^2/\lambda$ (far field)`.
- Maths in `$...$` (inline) and `$$...$$` (display, one formula per line). Standard LaTeX only.
- No emojis or pictographic symbols. No Obsidian callouts, no wikilinks, no HTML (it is also printed
  via pandoc/LaTeX). Plain Markdown headings, lists, tables and bold labels only.
- Source references short and plain, e.g. "(L3 p.12)", only where they help find a derivation. "(L<n> p.<N>)"
  means a LECTURE SLIDE page: take those only from the RECALL NOTES. Page numbers inside a FORMULA SHEET belong to
  that sheet's own document (unless it says otherwise): cite them as "(Notes p.<N>)", never as a lecture slide.
- "Trap (you)" lines only for mistakes actually recorded in STUDENT ERRORS, each ending with its source in
  brackets, e.g. "[error log 2026-11-14]", "[recall check L3]", "[marker: Lab 2]". Where STUDENT ERRORS has nothing
  for a section, write no "Trap (you)" line. Never invent one.
- Short lines (the build flags any line over 40 words): Steps at most 5, each a short imperative; one
  trap per line, the error and the fix only. Don't repeat a trap or fact already in the section.
- Words stay out of the maths: a formula line is maths only, with any note in the brackets after it
  (`$$l = \lambda \Rightarrow Z_{in} \to \infty$$ (antiresonance at the feed)`), never inside `\text{}`.
  Keep each display formula short enough for a narrow column; split a long chain into two lines.
- No space just inside `$...$` (write `$x = 1$`, not `$x = $ 1`): pandoc won't read it as maths.

{part}

Output only the requested Markdown, nothing before or after it.
