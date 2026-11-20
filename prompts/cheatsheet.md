Write the notes sheet ("cheat sheet") for {unit_code} ({unit_name}): the only paper the student may take into the
exam. Exam: {conditions}; materials: {materials}; format: {format}; pace: {pace}.
**Hard limit: {sheets} A4 sheet(s), double-sided = {sides} sides, printed in three columns at about 6.5 pt: at most
about {words} words in total.** The build fails if it runs over, so rank by exam value and cut the rest.

What the student needs from it: in the exam the maths is not the hard part, **choosing the method is**. So the sheet
is built to answer "this question says X, what do I do?" first, and to give the formula and steps second.

Sources (text blocks), in order of authority:
1. STUDENT'S OWN NOTES SHEET DRAFT (if present): what the student already chose to put on the sheet. Keep every item.
2. HANDBOOK: the unit's exam handbook, one section per problem type, with "Use when" triggers, formulas, steps and
   traps. It is the content source: take formulas and conditions from it exactly, never from memory.
3. WORKED EXAMPLES (if present): per question type, how to recognise it and the method steps.
4. STUDENT ERRORS: the error log and check misconceptions. Each distinct one becomes a "Trap (you)" line.
5. EXAM SCOPE and EXAM INTEL: what the exam emphasises. It decides depth and order, never what is left out first.

Output exactly this structure, in Markdown:

# {unit_code} notes sheet

## Method chooser
One line per trigger, the most common exam wordings first, covering every section below:
- <what the question says or gives, in the exam's words> → <the method, a few words> (§<n>)

## <n>. <problem type, short>
**Use:** <triggers, comma-separated, short>
<formulas, inline maths, separated by " · ", each with its condition in brackets: $p_{CL} = d_p d_c + n_p n_c$ (before cancelling)>
**Steps:** 1. ... 2. ... 3. ... (multi-step types only; at most 5 terse imperatives, on one line)
**Trap (you):** <the student's own error and the fix> [<its source: error log <date> / recall check L<n> / practice check <name>>]
**Trap:** <one common trap>

Rules (strict):
- Number the sections as the HANDBOOK does (§ numbers in the chooser must match). Keep the handbook's order. A
  section of little exam value may shrink to its "Use" line and one formula line; drop a section only if the EXAM
  SCOPE says it is not examined.
- No tables, no display maths ($$), no blank-line padding. Inline maths only ($...$), standard LaTeX, no `|` inside
  maths (write \lvert x \rvert). Words stay out of the maths.
- Fragments, arrows and symbols; no sentences, introductions or filler. No emojis or pictographic symbols, no
  Obsidian callouts, no wikilinks, no HTML, no slide references (there is no room and no slides in the room).
- "Trap (you)" lines only for mistakes recorded in STUDENT ERRORS, each ending with its source in brackets. Where
  there is none for a section, write no "Trap (you)" line. Never invent one.
- Only what the student cannot quickly derive or might misremember under time pressure: conditions, signs, which
  matrix goes where, orders and degrees, standard results. Leave out definitions anyone taking the exam knows.

Output only the sheet, nothing before or after it.
