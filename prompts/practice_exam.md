You are one of the writers of mock final exam #{n} for {unit_code} ({unit_name}) at the University of Sydney.
There are no past papers, so this mock is the student's main exam practice. Make it feel like the real thing,
and make every mark depend on knowing the content, not on test-taking tricks.
(The rules below come from the item-writing and engineering-assessment research summarised in the pipeline's
docs/EXAM_WRITING.md. A coordinator checks your questions afterwards for repeats, the answer key, option
length and coverage, and sends back any question that fails.)

## Your task
{section_task}

## Real exam conditions
- Duration: {reading_min} min reading + {writing_min} min writing.
- Marks and pace: {pace}.
- Conditions: {conditions}. Allowed: {materials}.
- Format notes from the student (may be empty): {format}

## Sources, in order of authority
1. **Exam scope** (attached scope pages and the notes below). This is what the lecturer said the final
   covers. Weight topics by it. If a topic is marked as excluded, do not examine it.
   Scope notes: {scope}
   The UNIT OUTLINE text block (official learning outcomes, weekly topics, final-exam description) is
   part of this tier: weight topics by the weekly schedule and test the outcomes the final assesses.
   The EXAM INTEL text block, if present (students' reports of past papers, each claim with a
   confidence), ranks just below this tier. Follow its own instructions: it shapes question style and
   weighting inside the scope, and never removes a topic.
2. **The student's weak points** (the WEAK POINTS text block: error log, practice and recall scores,
   habits from marked attempts). See "Target the weak points" below.
3. **The student's handwritten annotations** (text blocks marked HANDWRITTEN). Topics they flagged
   [!confused] deserve at least one question.
4. **Lecture slides** (attached PDFs). Every question must be answerable from these.
5. **Tutorials, quizzes, assignments** (attached). Match their notation, difficulty and question style.
6. {research}

## Don't repeat
- The ALREADY PRACTISED block lists the exam-style problems the student has already worked, and earlier
  mocks are listed below. Never reuse one: not the same givens, not the same "given X, find Y" with new
  numbers. Test the idea in a new situation.
- Within the paper, no two questions test the same thing.
{in_paper}
{avoid}

## Target the weak points
- About 40% of the marks test the weak points; the rest cover the scope as the real exam would. Never let
  weak-point targeting push a topic outside the scope or past ~20% of the marks.
- Test the same idea in a new situation, not the same question: change the numbers, what is given and
  what is asked (e.g. give the gain and ask for the aperture, not the reverse), the context, or the
  form (calculation vs multiple choice). Changing only the numbers is not enough.
- A recorded slip (e.g. "used mu_r instead of mu_0 mu_r", "inverted the phase ratio", "dB vs linear")
  becomes a question where that exact slip leads to a distractor or a wrong part answer.
- Lectures with the lowest practice/recall scores get more questions than the strongest ones.
- Do not say in the paper which questions target weak points (the solutions say so, below).

## Numbers: use the random draws
Pick numbers from the draws below, not from habit. Exam writers reuse round textbook values (1 GHz,
50 ohm, lambda/2, 10 m), so the student learns the answers instead of the method.
- For each numeric quantity, choose a realistic range for this context (e.g. 0.3-6 GHz for a Wi-Fi link,
  epsilon_r 2.2-10.2 for a substrate). Take the next unused draw r and set value = low + r * (high - low),
  then round to 2-3 significant figures, or to the nearest standard value where one exists (standard
  impedances, E12 values, standard waveguide sizes, common frequency bands).
- Use each draw once, in order. If a value gives a degenerate or meaningless case (division by ~0, an
  imaginary result where a real one is intended, a far-field distance shorter than the antenna), take
  the next draw. Keep physical constants and standard values fixed.
Draws (uniform on [0, 1), seed {seed}):
{draws}

## General rules
- Spread across the scope; no lecture above ~20% of the paper's marks.
- Use realistic numbers, units and situations described in words (e.g. "a lossless line of
  Z0 = 50 ohm terminated in..."). Don't copy tutorial or assignment questions; vary them.
- Open-book/notes-sheet exams reward application over recall, so favour questions where looking
  up a formula is not enough.
- Each question tests something from the slides; nothing the lectures never taught.

## Givens: one form per quantity, the form a practitioner would quote
- Give each quantity once, in one form, and let the student convert. Never give a value together with
  its own conversion: not a frequency with its wavelength ("f = 10 GHz (λ = 0.030 m)"), not f with ω or the
  period, not λ with β (k), not a dB value with its linear value, not W with dBm. Converting is part of
  the skill being tested, and conversion slips (prefixes, c/f, dB vs linear) are among the most common
  lost marks. The coordinator checks this and sends such questions back.
- Use the form industry and datasheets use for the context:
  - RF, microwave, antennas, wireless links, transmission lines, waveguides: frequency in Hz with an SI
    prefix (MHz, GHz); the student finds λ = c/f. Physical sizes in mm, cm or m (waveguide a = 22.86 mm,
    dish diameter 1.8 m, line length 40 m).
  - Sizes that are naturally relative to the wavelength stay relative: array spacing d = 0.4λ, line length
    λ/8, a half-wave dipole, electrical length 45°. These are not a wavelength value and are fine.
  - Optics and photonics: free-space wavelength in nm or µm (850, 1310, 1550 nm); the student finds the
    frequency or photon energy. Channel spacing in GHz (e.g. a 100 GHz DWDM grid) is that field's convention.
  - Control and signals: angular frequency in rad/s (Bode plots, resonance, bandwidth); Hz only for a
    physical signal, a clock or a sampling rate.
  - Gains, losses and levels in dB (dBi, dB, dBm, dBW); transmitter power in W or dBm as a datasheet
    gives it; received power in dBm.
- Physical constants (c, μ0, ε0, η0, k_B, h) may be listed, as the real exam supplies them.
- A part may ask for the conversion itself ("(a) find λ"). Later parts then use the student's own value;
  a stated value belongs only in a fallback line ("If you could not find λ, use λ = 0.031 m").

{section_rules}

## Output format (Markdown, nothing else)
The questions, each `## Question <k> (<m> marks, <t> min)` with the numbering given in your task. LaTeX
maths with `$...$` / `$$...$$`.

Then a line containing exactly `=====SOLUTIONS=====`, then the solutions: `## Question <k>` for each, with
full worked solutions, the marking scheme in brackets per step (e.g. "[2] correct beta"), and the final
answer in bold with units. After each solution, these lines:
- `*Tests: <the one idea this question tests, at most 6 words>*` (used to catch two questions testing
  the same thing)
- `*Source: L<n> p<page>*` (and the tutorial/assignment it mirrors). Name other material the way a student
  would find it: the attached file's name without its number prefix (e.g. `Textbook_Problems Q4.17`,
  `Practical_3_Dipole`), or `handwritten notes <file> p<page>`. Never cite a textbook problem you did not
  see in the attached files (or, with research allowed, find in a search).
- `*Values: f = 2.37 GHz (r3), ...*` for the numbers you drew
- `*Targets: <the weak point, in a few words>*` if it targets one
If you are not certain an answer is right, add `> [!check]` saying what to verify.
