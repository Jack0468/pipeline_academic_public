You are checking a mock exam's worked solutions for {unit_code} ({unit_name}) before a student relies on them.

For every question part:
1. Re-solve it independently from the question text. Recompute every number carefully (units, powers of
   ten, dB vs linear, radians vs degrees, peak vs RMS).
2. Compare with the given solution.

Then output the full corrected solutions section in the same Markdown format (headings, marking
scheme, bold final answers, `*Source:*` lines). Rules:
- Where the given answer was wrong, fix it and add `> [!trap] Corrected: <what was wrong>`. Students make
  the same mistakes, so these notes are useful.
- Where the question itself is ambiguous or unanswerable as written, add `> [!check] <the issue and the
  assumption used>`.
- Keep correct solutions as they are. Output only the solutions section, starting with `## Question 1`.

The exam is in two text blocks: QUESTIONS (the paper) and SOLUTIONS TO CHECK. Output every question's
solution, including the ones you did not change.
