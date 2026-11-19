# Question generation: write each paper, or build a question bank and assemble exams from it?

Status: research log (2026-11-15). No code yet. The trial plan below is part of Track B
(BLUEPRINT.md, "Q: Question-bank trial"). Related: [EXAM_WRITING.md](EXAM_WRITING.md), the research behind
today's mock writer.

## The question

Today `mock_writer.py` writes every mock from scratch: one AI writer per section, Python checks, targeted
rewrites, and a second AI pass to re-solve the solutions. The alternative is a specialised question
generator that fills a **bank** of questions, each tagged with its lecture, skill, marks and format. An
algorithm then **assembles** each exam from the bank against a blueprint (format, coverage, weak points).

## What the field does

These come from search results and abstracts, not full papers (2026-11-15). Verify a finding against the
paper before relying on it.

| Approach | What it is | Findings | Cost or limit |
|---|---|---|---|
| **Automatic item generation (AIG)**, template-based (Gierl & Lai) | A subject expert writes a *cognitive model* (the knowledge and skills a task needs), then an *item model*: a template whose slots and constraints software fills to generate items | One item model can yield hundreds or thousands of items. LLMs are now used to write and fill the templates (Sayin & Gierl, 2024) | High expert effort per cognitive model; that is the main recurring cost in the literature |
| **Parametrised question generators** (PrairieLearn, UIUC) | Each question is a small program: it draws random parameters, computes the correct answer, and renders the variant. Exams draw questions from pools | Chen, West & Zilles: pools of 3-4 generators, each with random parameters, deter collaborative cheating. Generators in one pool need similar difficulty, and attempt data is used to equalise it. PrairieLearn now has an AI feature (alpha) that drafts the question and its `server.py` | Writing and testing a generator takes longer than writing a static question |
| **Deployed variants** (STACK on Moodle) | Randomised maths questions whose variants are generated in advance; the question's own tests run on every variant before students see it | Each variant gets a quality gate instead of being trusted blindly | Variants must be checked to differ in substance, not only in their seed |
| **Automated test assembly (ATA)** (van der Linden, 2005, *Linear Models for Optimal Test Design*) | Assemble a test from a bank as a mixed-integer program: binary "include item" variables, constraints for content coverage, length, time and "enemy" items, and a difficulty target | Standard practice in large testing programs; solvers find forms satisfying all constraints quickly. Free tools exist (e.g. eatATA in R; PuLP or OR-Tools in Python) | Classical ATA treats item difficulty as known; ours would start as estimates |
| **LLM-written items, measured with real students** | Studies give LLM-written questions to students and compare them with expert items using item response theory | Isley et al. (2025, AAAI; 91 classes, about 1,700 students): with iterative generate, critique and revise cycles, AI items performed comparably to expert standardised-exam items, somewhat easier on average. Counter-evidence (Witsken et al., 2025, via a scoping review): scores about 9% lower and lower reliability (KR-20 0.75 vs 0.83). Rubric studies: the common flaws are awkward wording and conspicuous correct answers | Results vary by subject; automated quality metrics alone are not enough |
| **Predicting difficulty without field tests** | LLM-simulated students or text features estimate item difficulty (e.g. SMART, 2025; the BEA 2024 shared task on USMLE items) | Promising for screening | Not a replacement for real response data |

Two findings already in EXAM_WRITING.md matter here as well:
- Parametrised variants make each version a fresh problem (Chen, West & Zilles).
- Changing only the numbers doesn't test transfer (Fowler et al., SIGCSE 2020). A bank of templates must
  vary what is given and what is asked, not just the values.

## What this means for this project

ABCD1001-style exams are mostly calculations whose answers follow from the parameters. That suits
parametrised generators unusually well: **Python computes the answer, so correctness no longer depends on
an AI writing it and another AI checking it.** That removes the error class seen on 2026-11-15:
- solutions that belonged to other questions (4 in mock 2);
- a question premise that was physically wrong (mock 2 Q24);
- numbers that had to be recomputed by hand to be trusted.

Three architectures to compare:

| | A. Whole-paper writing (today) | B. Static bank + assembly | C. Generator bank + assembly |
|---|---|---|---|
| What the AI writes | Each paper, each time | Single questions with solutions, once each | An *item model* once: stem template, parameter ranges in the industry form (MHz, nm, dB), a Python solver, distractor rules, tags |
| Correctness | AI-written, AI-checked | Checked once per question, then reused | Computed by Python for every variant; the solver itself is reviewed and tested once |
| Cost of a new mock | 3-7 long AI calls | Assembly only (no AI) | Assembly plus rendering (no AI) |
| Variety | High, but repeats need checking | Limited by the bank's size | Unlimited numbers; the variety of structures is limited by the number of item models |
| Coverage, format, time | Asked for in the prompt, checked afterwards | Exact constraints in the assembler | Exact constraints in the assembler |
| Weak-point targeting | The prompt reads the error log | Tags: "at least 40% of the marks from weak-point skills" as a constraint | The same, plus a fresh variant of a weak item model for spaced re-tests |
| Difficulty control | None beyond the prompt | Per-item estimate, refined by your attempts | Per-model estimate; parameter ranges kept away from degenerate cases |
| Conceptual multiple choice | Good | Good | Poor fit: these stay static (B) items |
| Up-front cost | None (exists) | Moderate | High: Gierl & Lai and PrairieLearn both report that authoring generators takes the effort |
| Sharing with other students | Mocks only | The bank is shareable and reviewable | Item models are code: reviewable and versioned, a natural fit for the students-for-students idea |

The biggest gain may be outside mock exams. With item models, every lecture's exam-style problems become
generators. That gives:
- a re-test of an error-log item with new numbers in the evening review;
- spaced re-scans that are never the same paper twice;
- a mock assembled from models the student hasn't practised (or from practised models with a different
  unknown).

That serves the design principle in the README (retrieval and practice every week) more directly than
better whole papers.

Risks:
- **Template sameness:** students learn the template, not the method (Fowler et al.). Mitigate with
  several item models per skill, item models that can ask for different unknowns (gain from aperture, or
  aperture from gain), and varied contexts.
- **Authoring cost:** an AI drafts each item model and its solver; a review plus a unit test against a
  worked example from the slides makes it trustworthy. Budget 20-40 minutes per model.
- **Difficulty differs between variants:** keep parameter ranges realistic, and check with your own
  attempt times and marks.
- **Rendering and LaTeX quality:** templates must produce the same Markdown format the vault already
  uses (the `MockPaper` model in REFACTOR_PLAN R2).

## Recommendation

Move to a **hybrid**:
- **C** (generator bank) for calculation questions;
- **B** (static bank) for conceptual multiple choice;
- **assembly by a small integer program** (PuLP) against the unit's exam format, coverage cap, weak-point
  share, time budget and the "not practised" rule;
- **today's Python checks** (repeats, answer key, option-length cues, givens) kept as the quality gate on
  every assembled paper;
- **A** (whole-paper writing) kept as the fallback and as the comparison baseline until the trial below
  shows the hybrid is better.

## Trial plan (Track B, after REFACTOR_PLAN R2-R3)

It needs R2's `Question`/`MockPaper` model and R3's validated AI replies.

1. **Item schema** (YAML or JSON per item). It covers:
   - the item itself: id, unit, lecture, learning outcome, skill tags, format (mc/written), marks and
     minutes;
   - the numbers: parameters with ranges, units and the industry form;
   - the maths: a solver (a Python function), answer precision, and distractor rules (named errors: dB vs
     linear, a missing factor of 2, a prefix slip);
   - the text: stem and solution templates;
   - bookkeeping: provenance, review status, and statistics from attempts.
2. **Ten ABCD1001 item models**: Friis link budget, directivity from U(θ), dish diameter and HPBW, patch
   design, small loop, array nulls and steering, distortionless line, quarter-wave transformer, horn
   far-field distance, total efficiency.
   - An AI drafts each model and its solver.
   - Claude or the student reviews it.
   - A unit test checks the solver against a worked example from the slides.
3. **Assembler**: PuLP with constraints from `exam.format`:
   - at most 20% of the marks per lecture;
   - at least 40% of the marks from weak-point tags;
   - no item model the student has practised unless the unknown changes;
   - the total time at the exam's pace.
4. **Head-to-head**: one mock from the hybrid and one from A for the same unit, sat under exam conditions.

**Data collection started 2026-11-16.** The hypothesis: a question the coordinator sends back is
replaced by a harder one. From mock 3 onwards, `mock_log.py` records every question sent back (round,
reason, the question and solution before and after, difficulty proxies) and where each final question
came from, in `state/mock_logs/`. `grade_practice.py` stores the marks per part of every attempt in
`state/practice_check_data/`. Joining the two by mock and question number answers the hypothesis with your
own marks, not just the proxies. The same data feeds the head-to-head below.

Real difficulty data from more than one student: classmates' marks on the shared mocks, pooled anonymously
(REFACTOR_PLAN.md, "Planned feature: pooled class difficulty data"). The research above says field data is
what calibrates difficulty; this is how the app gets some.

Metrics:
- errors found per mock when the solutions are recomputed independently;
- AI calls and minutes of review per new mock;
- the score and time used against the exam pace;
- repeat and overlap flags;
- a blind rating of which paper feels more like the real exam;
- after results come out: how mock scores track the real exam mark.

Decision rule: adopt the hybrid for a unit if solution errors fall to about zero, the per-mock cost falls,
and the paper is rated no worse than A. Otherwise keep A and use generators only for practice re-tests.

Rough effort: schema and assembler 6-8 h; about 5 h for ten item models with AI drafting; the head-to-head
is one exam sitting plus marking.

## Sources

- Gierl, M. J., & Lai, H. Automated item generation, NCME ITEMS Module 34:
  https://ncme.org/wp-content/uploads/2025/10/Module-34-Automated-Item-Generation-Gierl-Lai.pdf ;
  Gierl, Lai & Tanygin (2021), *Advanced Methods in Automatic Item Generation*, Routledge:
  https://www.schweitzer-online.de/en/buch/Gierl/Advanced-Methods-Automatic-Item-Generation/9780367458324/A58606598/ ;
  overview: https://assess.com/?p=30006
- PrairieLearn question generators: https://docs.prairielearn.com/question/server/ ; AI question generation:
  https://docs.prairielearn.com/question/ai-generation/ ; exams: https://www.prairielearn.com/catalog/assessments/exam
- Chen, West & Zilles, randomisation and cheating:
  https://zilles.web.engr.illinois.edu/papers/chen_paper_async_randomization_las_2018.pdf ;
  predicting generator difficulty: https://zilles.web.engr.illinois.edu/papers/chen_predicting_difficulty_las_2019.pdf
- Research prototype, AI-assisted generator authoring: https://mfsilva22.github.io/research/ai_question_generation
- STACK deployed variants: https://docs.stack-assessment.org/en/STACK_question_admin/Deploying_matched_variants/ ;
  https://blogs.ucl.ac.uk/digital-education/2022/05/04/moodle-stack-quiz-question-type-deploying-variants-to-avoid-quiz-crashing
- Automated test assembly: HumRRO summary of van der Linden (2005):
  https://www.humrro.org/blog/automated-test-assembly-can-an-algorithm-build-a-better-test/ ;
  item-set assembly: https://research.utwente.nl/en/publications/optimal-assembly-of-tests-with-item-sets-3/ ;
  MIP modelling approaches and solvers: https://next.sciencegate.app/document/10.1111/jedm.12262
- Isley et al. (2025), Assessing the quality of AI-generated exams: a large-scale field study (AAAI):
  https://arxiv.org/abs/2508.08314
- Student-centred evaluation of an LLM-generated test: https://arxiv.org/pdf/2505.06591 ; scoping review of
  LLM-generated STEM items: https://aquila.usm.edu/cgi/viewcontent.cgi?article=1950&context=jetde ; MCQ rubric:
  https://edtecharchives.org/conference_proceeding/2551/25360
- Difficulty prediction: SMART (2025): https://arxiv.org/pdf/2507.05129 ; overview:
  https://www.emergentmind.com/topics/automated-item-difficulty-prediction
- Fowler et al. (SIGCSE 2020), surface features and transfer:
  https://zilles.web.engr.illinois.edu/papers/fowler_surface_features_SIGCSE_2020.pdf
