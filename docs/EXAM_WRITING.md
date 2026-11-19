# How mock exams are written

This covers the research behind the mock-exam prompts and how `gen_practice_exam.py` puts a paper together.
The prompts are `prompts/practice_exam.md` (shared base), `practice_exam_mc.md` (multiple choice),
`practice_exam_written.md` (extended answer) and `practice_exam_revise.md` (targeted rewrites).
`practice_exam_verify.md` re-solves the final solutions.

Mocks are generated on request only (`python scripts\gen_practice_exam.py --unit X`), never on a schedule.

## Why mocks need care

There are no past papers, so the mocks are the main exam practice. A mock that can be passed by
test-taking tricks gives false confidence. Mock 1 for ABCD1001 (2026-11-14) showed this: the correct
option was strictly the longest in 13 of 20 multiple-choice questions (chance: about 5), and the correct
letter was B in 14 of 20. A Section A score on that paper probably overstates what was known.

## Research and the rule each finding became

### Multiple-choice item writing

| Finding | Rule in the prompts / check in code |
|---|---|
| Haladyna, Downing & Rodriguez (2002) reviewed 27 studies and textbooks and validated 31 item-writing guidelines, including: keep options about equal in length, one idea per option, minimise reading, avoid "all/none of the above", word stems positively | `practice_exam_mc.md`: equal length and detail, one idea per option, no all/none of the above, positive stems (a NOT in capitals) |
| The NBME item-writing guide lists "technical flaws" that help test-wise students: the correct answer is the longest or most complete, convergence (the answer shares the most with the other options), word repeats between stem and answer, grammatical cues, absolute terms | Prompt: no longest, convergence, word-overlap, grammar or absolute-word cues. Code: `mock_writer.mc_issues` sends back the questions whose correct option is strictly the longest when that happens more often than chance (25%) |
| Answer position: a writer who puts the key wherever it falls produces bunched letters (mock 1: B 14 of 20) | Code: the answer key is drawn in Python before writing (balanced over A-D, no letter three times running), the writer must follow it, and `mc_issues` / `key_mismatch` check it |
| Distractors work when they are plausible errors, not filler | Numerical distractors are the results of real slips (prefix, dB vs linear, missing 2 or pi, relative vs absolute permittivity, degrees vs radians); the solutions name the error behind each distractor |

### Engineering problems

| Finding | Rule |
|---|---|
| Felder & Brent: engineering exams usually run too long, because of heavy calculation and unfamiliar twists; test what was taught, against stated objectives | `practice_exam_written.md`: no untaught twists, calculator-sized arithmetic, size each part at the paper's pace per mark (cut a part if a clean solution needs more than about a third of its time) |
| A chain of dependent parts lets one early slip cost the whole problem | Independent parts where possible; otherwise a fallback value ("if you could not find Z_in, use ...") |
| Partial credit should reward correct method | Method marks for each key step plus an answer mark, with follow-through |
| Engineering exams should test application and judgement, not just formula recall (and open-book exams reward application) | Each problem models a situation, picks the method, computes and judges the result; one interpretation part per problem |

### Random numbers

| Finding | Rule |
|---|---|
| Zilles and colleagues (Chen, West & Zilles) and the PrairieLearn platform: parameterised questions (random values in realistic ranges) make each version a fresh problem; pools of 3-4 variants deter copying | Python draws 40 uniform values per mock from a seeded RNG (seed in the mock's frontmatter). The writer maps each quantity onto a realistic range and rounds to 2-3 significant figures or a standard value, instead of habitual round numbers |
| Fowler et al. (SIGCSE 2020): surface-level changes (permuted numbers) alone don't test transfer | Weak points and repeats are re-tested in a new situation: change what is given and what is asked, not just the numbers. The overlap check flags same-structure questions even with new numbers |

### Targeting weak points

This is the pipeline's own design (retrieval practice on the errors the student actually makes), not a
cited finding: about 40% of the marks go to the WEAK POINTS block. That block holds practice and recall
scores weakest first, the error log, and habits and misconceptions from marked attempts. A recorded slip
becomes a distractor or a wrong part answer. Targeted questions are tagged `*Targets:*` in the solutions.

## How a paper is put together (`scripts/mock_writer.py`)

```
gen_practice_exam.py (coordinator)
  plan sections from exam.format         ABCD1001: A = 20 multiple choice, B = 40 marks written
  draw seed → numbers + MC answer key
  1. written-answer writer (agy call)    big questions claim the core topics first
  2. multiple-choice writer (agy call)   sees Section B ("already in this paper")
  checks after each writer (Python, no AI):
     - overlap: this paper, earlier mocks, every practised recall exam-style problem
     - MC: answer key, longest-option cue, four options
     - missing questions
  failing questions only → back to that writer with the reason (practice_exam_revise.md),
     up to 3 rounds; the rest are kept as they are
  coverage: no lecture over 20% of the marks (from each solution's *Source: L<n>* line)
  solution check (practice_exam_verify.md), then final key and cue check
  anything still open → a warning box at the top of the mock + open_issues in the frontmatter
```

- **Why two writers instead of one call:** each writer gets rules for one kind of question only, and its
  output is half the paper, which keeps it under the model's output-token limit (the handbook draft hit
  that limit as one call). Both get the same sources (slides, scope, practice PDFs).
- **Why sequential, not parallel:** the multiple-choice writer sees Section B, so most overlap is avoided
  at writing time; the check catches the rest.
- **Overlap test** (`same_question`): content-word Jaccard overlap at least 0.45, or at least 0.25 plus 2 or
  more shared specific numbers (the same givens reused), or the same `*Tests:*` idea. Calibrated on mock 1
  against the 50 practised problems: unrelated pairs have a median overlap of 0.03; real repeats scored
  0.47-0.53 (mock 1 Q4 reused L2 problem 2's 30 and 45 degree beamwidths). Questions on the same topic
  that ask something different pass.
- **Cost:** 2 writer calls, plus up to 2 rounds of rewrites per section, plus 1 solution check: 3 to 7 agy
  calls per mock.
- **The writers are agy processes**, coordinated by the Python script. So mocks build from the command
  line without a Claude session; a Claude session can still run the script and review the result.

## Sources

- Haladyna, T. M., Downing, S. M., & Rodriguez, M. C. (2002). A review of multiple-choice item-writing
  guidelines for classroom assessment. *Applied Measurement in Education*, 15(3), 309-334.
  https://doi.org/10.1207/s15324818ame1503_5 (summary:
  https://cpl.health.unm.edu/AssetListing/Writing-Questions-for-Learning-and-Assessment-2573/Rules-for-Multiple-Choice-Items-Haladyna-et-al-2002-5943)
- NBME, *Constructing Written Test Questions for the Basic and Clinical Sciences* / NBME Item-Writing
  Guide. https://ucns.org/common/Uploaded%20files/Help/NBME%20Item%20Writing%20Guide.pdf
- Felder, R. M. (2002). Designing tests to maximize learning. *J. Professional Issues in Engineering
  Education and Practice*, 128(1). https://web1.eng.famu.fsu.edu/~schreiber/ech2050/articles/education_article_04.htm ;
  Brent & Felder, assessment workshop: https://aapt.org/Conferences/newfaculty/upload/Assessment-Physics-Brent-Felder-v2.pdf ;
  PhysPort: https://www.physport.org/recommendations/Entry.cfm?ID=148001
- Chen, B., West, M., & Zilles, C. How much randomization is needed to deter collaborative cheating on
  asynchronous exams? https://experts.illinois.edu/en/publications/how-much-randomization-is-needed-to-deter-collaborative-cheating-/ ;
  PrairieLearn exams: https://www.prairielearn.com/catalog/assessments/exam
- Fowler, M., et al. (2020). Surface features and transfer, SIGCSE 2020.
  https://zilles.web.engr.illinois.edu/papers/fowler_surface_features_SIGCSE_2020.pdf

All findings above come from abstracts and secondary summaries; the full papers weren't read.

Next step under consideration: a question bank of parametrised item models with exams assembled from it,
instead of writing each paper. Research, comparison and trial plan: [QUESTION_GENERATION_RESEARCH.md](QUESTION_GENERATION_RESEARCH.md).
