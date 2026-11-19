You are writing worked examples for {unit_code} ({unit_name}): a reference the student takes into an open-book
exam, where they will copy the method under time pressure. One worked example per calculation type below.

## The exam
- {format}
- Pace: {pace}. Conditions: {conditions}. Allowed: {materials}.

## Write these types (and only these), in this order, with these numbers
{types}

## Each type, exactly this structure
`## WE<n>. <short type name> (<lectures>)`
**Recognise it:** the phrases an exam question uses for this type, comma-separated (they feed an index).
**Method:** numbered steps, terse imperatives, with each formula in LaTeX ($...$), its units and its condition,
and the slide it comes from: "(L5 p.28)" for a lecture slide page (from the RECALL NOTES or the slides), "(Notes p.N)"
for a page of the condensed notes (the FORMULA SHEET's page column).
**Worked example** (about 6-10 marks, in the exam's style, with parts (a), (b), (c) and the marks per part):
the question, then **Solution:** every step shown with numbers substituted, intermediate values, units, and the
final answer in bold. Show the working the marker wants (tables for Dijkstra, the full long division for CRC, the
binary or hex sums for the checksum).
**Reversed:** one short variant asking the other way round (given the result, find an input), with a brief
worked answer. The lecturer is said to reverse questions.
**Traps:** 2-4 bullets: the mistakes that lose marks on this type.

## Rules
- Use the course's own notation, method and conventions from the lecture slides and the tutorial solutions
  (attached). Where the condensed notes and the slides differ, follow the slides.
- Choose realistic numbers; never reuse a question from the ALREADY PRACTISED block or from the mock papers in the
  MOCK PAPERS block (the student hasn't sat those yet: don't give away their answers, not even with the same numbers).
- Every number in a solution must follow from the givens: compute carefully, show the intermediate results, and
  check the final answer a second way where one exists (CRC: the codeword divides by g(x) with remainder 0;
  checksum: the received words plus the checksum sum to FFFF; subnet: network + block - 1 = broadcast; Dijkstra:
  each final cost is a neighbour's cost plus the link cost; RSA: (m^e)^d mod n = m).
- No introduction, no front matter, no emojis. Output only the sections.
