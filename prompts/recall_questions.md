You are preparing active-recall study material for a third-year electrical engineering student
at the University of Sydney, from ONE lecture's slides (attached PDF). Unit: {unit_code} {unit_name}.
Lecture label: {label}. The exam is a 2-hour paper exam ({conditions}).

Goal: material the student uses to TEST themselves, not to re-read. Questions must make them
retrieve and apply ideas, and every item must be traceable to a slide page so they can check it.

Output GitHub-flavoured Markdown for Obsidian, exactly these sections and nothing else
(no preamble, no closing remarks):

# {unit_code} {label}: <short topic title from the slides>

## Topics
- 4-8 bullets, one line each, in slide order, each ending with the page range like `(pp. 3-7)`.

## Free-recall prompts
- 8-12 numbered prompts that ask the student to explain, derive, compare or predict
  ("Explain why…", "Derive…", "What happens to X if Y doubles, and why?", "Sketch…").
- No answers. End each with the page to check, like `(p. 12)`.
- Prefer understanding and reasoning over definitions. At most 2 pure-definition prompts.

## Exam-style problems
- 3-5 numbered quantitative problems modelled on the worked examples / methods in these slides,
  with new numbers. State all given values with units. Mark allocation in brackets, e.g. `[6 marks]`.
- After each problem, a collapsed answer callout with the FINAL numeric answer(s) and the slide
  page of the method, never the full working:
  > [!answer]- Answer
  > $G_{dB} \approx 8.2$ dB. Method: p. 14.
- If the lecture is purely conceptual (no calculations), give 3 short-answer exam questions instead.

## Notes-sheet candidates
- The 4-10 relations worth putting on a notes sheet, as `$$ … $$` display LaTeX, each followed by
  one line: symbols with units, when to use it, and `(p. N)`.

## Common traps
- 3-5 bullets: sign conventions, units/dB vs linear, factors of 2 or 2π, assumptions that fail.

Rules:
- LaTeX: inline `$…$`, display `$$…$$`. No `\[ \]` or `\( \)`.
- Use only what is in the slides; if you add a standard fact not in the slides, mark it `(not in slides)`.
- If a slide is unreadable, skip it. Never invent page numbers.
