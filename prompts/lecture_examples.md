You are reading ONE lecture's slides (attached PDF) for {unit_code} {unit_name}, lecture {label}. List every example
and exercise the slides contain, so the student can attempt each one before looking at the lecturer's answer.

What counts:
- worked examples (a slide or run of slides that sets up a problem with values and solves it);
- exercises, "try this", quiz or check questions, questions posed to the class;
- example calculations inside a derivation that use specific numbers.
Include slides that are images: read them visually. Leave out plain derivations with no specific problem, and
definitions.

Output, in slide order, exactly this block per item (nothing before the first block, nothing after the last):

### p.<N> · <short title, at most 8 words>
**Type:** <worked example | exercise | question>
**Question:** <the problem as the slides set it: every given value with its units, what is asked, in your own
words only where the slide is a figure; equations in LaTeX $...$; a figure the question needs as [figure: ...]>
**Answer on the slides:** <pp. <N>-<M> where the slides solve or answer it, or "none" if they don't>

Rules:
- <N> is the 1-based page number of the attached PDF where the item starts (the PDF viewer's page, not a number
  printed on the slide). Never invent a page.
- NEVER write the solution, the working or the final answer: only where it is on the slides.
- If the lecture has no examples or exercises at all, output exactly: NONE
- No emojis, no preamble, no closing remarks.
