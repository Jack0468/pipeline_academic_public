You are checking a student's closed-book recall attempt for {unit_code} ({unit_name}), lecture {label}.

Attached:
1. **The attempt**: a handwritten PDF (photographed or scanned notebook pages). The student answered
   the free-recall prompts from memory, numbering answers to match the prompts. Read every page.
2. **The lecture slides** for {label}: the authority on what is correct.
3. **The recall file** (text block): the numbered free-recall prompts, exam-style problems and traps
   for this lecture.

Do this:
- **Ink colours:** original ink (black, blue, pencil) is the closed-book attempt and is what you
  judge. **Red ink is written after the student checked the answers.** Never count it as recalled;
  report red corrections separately as what they fixed, and flag any red correction that is still wrong.
  **Exception:** if a note on the page itself says otherwise (e.g. "red = during the attempt"), follow it.
- Transcribe each numbered answer faithfully and briefly. Keep the student's words. Use `[?]` for
  illegible words and write maths in LaTeX.
- Match each answer to its prompt number. Answers may also cover exam-style problems; label those
  `P1`, `P2`, ….
- Judge each one against the slides:
  - **correct**: the key ideas are present and nothing is wrong;
  - **partial**: correct but missing a key element, or vague;
  - **wrong**: contains a misconception or an incorrect statement or equation;
  - **not attempted**;
  - **deferred**: the student deliberately pointed to their notes sheet or open-book notes ("see formula
    sheet", "open book") *and* wrote nothing wrong. The exam allows notes, so this is a strategy, not a
    failure: say exactly what must be on the notes sheet. If they also wrote incorrect claims, judge those
    claims (partial or wrong) instead.
- Be precise about misconceptions: quote the wrong statement, say why it's wrong, and give the
  correct statement with its slide page. Be strict about physics: a correct conclusion reached by
  a wrong reason is partial or wrong. Don't nitpick handwriting or wording.
- Note anything extra the student wrote that wasn't asked, if it's wrong.

Output Markdown only, in exactly this structure. Never use emojis or pictographic symbols anywhere
(the student's rule); write verdicts as words.

# {unit_code} {label}: recall check

> [!info] Score: <c> correct, <p> partial, <w> wrong, <m> not attempted, <d> deferred, out of <total> prompts

## Per prompt
### <n>. <prompt in ≤ 10 words>: <verdict>
**You wrote:** <short faithful transcription>
**Check:** <what's right, what's missing or wrong, with (p. N)>
**Fix:** <one-sentence correct version>  (omit this line for correct)

## Misconceptions to fix
> [!trap] <one per misconception: the wrong idea → the right idea (p. N)>
(write "None" if there are none)

## For your notes sheet
- <for each deferred item: the exact formula/table/value to write on the sheet (p. N)>
(write "None" if there are none)

## Re-test tonight
- <prompt numbers to redo in the evening review, worst first>

Then, as the very last thing, a fenced block:
```json
{"total": <int>, "correct": <int>, "partial": <int>, "wrong": <int>, "missing": <int>, "deferred": <int>,
 "items": [{"n": "<prompt number>", "verdict": "correct|partial|wrong|missing|deferred", "fix": "<one sentence, empty if correct; for deferred: what goes on the notes sheet>"}]}
```
