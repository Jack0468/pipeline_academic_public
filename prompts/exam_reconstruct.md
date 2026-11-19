You are reconstructing the {year} final exam for {unit_code} ({unit_name}) at the University of Sydney from a
student's notes written straight after sitting it. The goal is a faithful practice copy of the real paper, with
worked solutions, for the student's own revision.

## The student's recall notes (verbatim)
{recall}

## The real exam's conditions
- Duration: {reading_min} min reading + {writing_min} min writing. Marks and pace: {pace}.
- Conditions: {conditions}. Allowed: {materials}.
- Format: {format}

## How to reconstruct
- **Scope of this pass:** {sections_rule}
- Reconstruct **only the questions the notes mention**, in the paper's real format and style. Do not invent
  extra questions to fill the paper. Keep the question numbers the notes give (e.g. 21-26). Number recalled
  multiple-choice questions 1, 2, ... in the order noted, and say in the solutions that their position in
  the real paper is unknown.
- **Every value in the notes stays exactly as written** (frequencies, dimensions, impedances, gains, powers,
  angles, spacings). Write each quantity once, in the form the notes give it.
- Where a question can't be solved from the notes alone, add the missing given in the form a real exam
  would state it, and tag it inline as `[inferred]`. Prefer standard textbook values and the lecture's
  conventions (the attached slides and the handbook text block are the authority).
- Where the notes allow two readings (e.g. a broadside or an end-fire array, which load a power refers
  to), write the question with the more likely reading. Solve both readings in the solutions and say
  which you chose and why.
- Give each part its marks. Section B's marks must add up to the section total in the format. Where the
  notes don't say, split them the way the lecture's exam style suggests (method plus answer marks).
- Multiple choice: four options A-D, one correct, distractors that are real errors (wrong formula, dB vs
  linear, a missing factor). Write `**Correct Answer: X**` in the solution.

## Output format (Markdown, nothing else)
First the paper: {paper_layout} Each question is headed `## Question <k> (<m> marks)`, with LaTeX maths in
`$...$` / `$$...$$`.

Then a line containing exactly `=====SOLUTIONS=====` on its own (required: the paper and the solutions are
split at that line, and a reply without it is discarded), then `## Question <k>` for each question with:
- a full worked solution, the marking scheme in brackets per step ("[1] correct lambda"), and the final
  answers in bold with units and the precision a boxed exam answer needs;
- `*Source: L<n> p<page>*` for the slide the method comes from;
- `*Assumptions:*` listing every `[inferred]` value and any reading you chose, with the alternative answer
  where it differs;
- `> [!check]` wherever you are not certain the reconstruction matches the real question.
