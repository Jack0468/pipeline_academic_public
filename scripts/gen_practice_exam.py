"""Generate full mock final exams (no past papers exist), then check the solutions in a second pass.

Per mock, mock_writer.py coordinates one writer agent per section (written answers first, then
multiple choice), checks the draft (repeats of this paper, earlier mocks and practised problems; the
pre-drawn answer key; longest-option cues; coverage) and sends failing questions back for rewriting,
up to 3 rounds. Numbers and the answer key come from a seeded RNG. A text-only call then re-solves every
part and corrects the solutions. With --research, the writers may also use Google Search.
Research and design: docs/EXAM_WRITING.md.

Output (vault/units/<unit>/practice/):
    mock_exam_<n>.md             questions only: sit it under exam conditions. Nothing in it gives answers
                                 away: no answer key or seed in the front matter, open issues only as a
                                 count, and any stray "Correct Answer: X" line is stripped before saving
    mock_exam_<n>_solutions.md   checked worked solutions + marking scheme; the answer key (mc_key), the
                                 seed and the full list of open issues
Generation log (mock_log.py), in state/mock_logs/<unit id>/mock_exam_<n>.{json,md} plus index.md/.csv:
every model call, every question sent back (round, reason, before and after, difficulty proxies), where
each final question came from, and what the solution check changed. It contains answers: read it after
sitting the mock.

Usage:
    python scripts/gen_practice_exam.py --unit ABCD1001            # one new mock
    python scripts/gen_practice_exam.py --unit ABCD1001 --n 3 --research
    python scripts/gen_practice_exam.py --if-missing               # only units with no mock yet
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime

from common import ALLOW_SAT_HELP, work_units
from common import PROMPTS, STATE, TZ, fill, find_sources, intel_block, load_units, md_section, scope_files, unit_path
import gemini
import mock_log
import mock_writer
import unit_inputs
from pace_recall import pace

SPLIT = "=====SOLUTIONS====="
MAX_PRACTICE_MB = 60          # cap on tutorial/quiz/assignment PDFs per call
MAX_OWN_MB = 40               # cap on the student's own submitted work per call
MAX_REFERENCE_MB = 30         # cap on third-party reference notes (xtra_resources/) per call
REFERENCE_NOTE = ("REFERENCE NOTES (third-party condensed notes or formula sheets, not the lecturer's; attached PDFs: "
                  "{names}). Use them for notation and to see which calculation types the course has. They rank "
                  "below the lecture slides: where they disagree, follow the slides. Never copy their worked examples "
                  "or the tutorial problems they solve; test the same skill in a new situation.")
OWN_NOTE = ("STUDENT'S OWN SUBMITTED WORK (typed answer sheets, assignment submissions, lab reports): the PDFs "
            "listed below. Compare their methods and answers with the marker comments in the CANVAS block and "
            "with the slides. Where the student's method was wrong, incomplete or shaky, write questions that "
            "test exactly that skill again, in a new context. Files: {names}")


def _front(md: str) -> dict:
    m = re.match(r"\A---\n(.*?)\n---", md, re.S)
    return dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M)) if m else {}


_section = md_section


def weak_points(u, limit: int = 14000) -> str:
    """WEAK POINTS block: scores per lecture (weakest first), error-log entries, habits from marked attempts."""
    rows = []
    for p in sorted(unit_path(u, "practice_checks").glob("*.md")):
        f = _front(p.read_text("utf-8"))
        m = re.search(r"\((\d+)%\)", f.get("marks", ""))
        if m:
            rows.append((int(m.group(1)), f"practice: {f.get('reference', p.stem)} {f['marks']}"))
    for p in sorted(unit_path(u, "recall_checks").glob("*.md")):
        f = _front(p.read_text("utf-8"))
        m = re.match(r"(\d+)%", f.get("score", ""))
        if m:
            rows.append((int(m.group(1)), f"recall check: {f.get('lecture', p.stem)} {f['score']}"))
    out = ["Scores, weakest first:"] + [f"- {t}" for _, t in sorted(rows)]
    log = unit_path(u, "error_log")
    if log.exists():
        items = []
        for ln in log.read_text("utf-8").splitlines():
            ln = re.sub(r"<!--.*?-->", "", ln).strip()
            if ln.startswith("- ") or (ln.startswith("|") and not re.match(r"^\|[\s:|\-]+\|$", ln)
                                       and "Re-tested" not in ln and not ln.rstrip("| ").endswith("ok")):
                items.append(ln)
        out += ["", "Error log (each line is a lost mark and its fix):"] + items[-80:]
    habits = []
    for p in sorted(unit_path(u, "practice_checks").glob("*.md")) + sorted(unit_path(u, "recall_checks").glob("*.md")):
        md = p.read_text("utf-8")
        for h in ("Habits to fix", "Misconceptions to fix"):
            habits += [f"{x.strip()} ({p.stem})" for x in _section(md, h).splitlines() if x.strip().startswith("-")]
    if habits:
        out += ["", "Habits and misconceptions from marked attempts:"] + habits
    text = "\n".join(out)
    return text[:limit] + ("\n(truncated)" if len(text) > limit else "")


def mc_count(u) -> int:
    m = re.search(r"(\d+)\s*multiple[- ]choice", str(u["exam"].get("format") or ""), re.I)
    return int(m.group(1)) if m else 0


def answer_key(n: int, rng) -> list[str]:
    """Balanced (each letter about n/4 times), shuffled, no letter three times in a row."""
    base = (list("ABCD") * (n // 4 + 1))[:n]
    while True:
        rng.shuffle(base)
        if not any(base[i] == base[i + 1] == base[i + 2] for i in range(n - 2)):
            return base


def draws(rng, k: int = 40) -> str:
    vals = [f"r{i + 1}={rng.random():.3f}" for i in range(k)]
    return "\n".join("  " + ", ".join(vals[j:j + 10]) for j in range(0, k, 10))


def key_mismatch(solutions: str, key: list[str]) -> list[str]:
    got = {}
    for q, body in re.findall(r"^## Question (\d+)[^\n]*\n(.*?)(?=^## Question |\Z)", solutions, re.S | re.M):
        m = re.search(r"correct\s+answer\W{0,6}\s*\(?([A-D])\b", body, re.I)
        if m:
            got[q] = m.group(1).upper()
    return [f"Q{i + 1} {got.get(str(i + 1), '?')} (key {k})" for i, k in enumerate(key) if got.get(str(i + 1)) != k]


def mc_cues(questions: str, solutions: str) -> dict:
    """Test-wiseness check on the multiple-choice questions: how often the correct option is the longest,
    and how the correct letters are spread. Chance level for 'longest' is 25%."""
    qs = dict(re.findall(r"^## Question (\d+)[^\n]*\n(.*?)(?=^## Question |\Z)", questions, re.S | re.M))
    sol = dict(re.findall(r"^## Question (\d+)[^\n]*\n(.*?)(?=^## Question |\Z)", solutions, re.S | re.M))
    n = longest = shortest = 0
    letters = []
    for k, body in qs.items():
        opts = dict(re.findall(r"^\s*(?:\*\*)?\(?([A-D])[.)](?:\*\*)?\s+(.+)$", body, re.M))
        m = re.search(r"(?:correct\s+)?answer\W{0,6}\s*\(?([A-D])\b", sol.get(k, ""), re.I)
        if len(opts) < 4 or not m:
            continue
        right = m.group(1).upper()
        size = {L: len(re.sub(r"\$[^$]*\$", "xxxx", t).strip()) for L, t in opts.items()}
        others = [v for L, v in size.items() if L != right]
        n += 1
        letters.append(right)
        longest += size[right] > max(others)                   # strictly the longest
        shortest += size[right] < min(others)
    return dict(n=n, longest=longest, shortest=shortest,
                letters={L: letters.count(L) for L in "ABCD"})


def cue_note(c: dict) -> str:
    """'' if the multiple choice looks fair, else a warning line."""
    if c["n"] < 4:
        return ""
    bad = []
    if c["longest"] / c["n"] > 0.35:
        bad.append(f"the correct option is the longest in {c['longest']}/{c['n']} questions "
                   f"(chance is about {c['n'] / 4:.0f})")
    top = max(c["letters"].values())
    if top / c["n"] > 0.45:
        bad.append("correct letters are bunched: " + ", ".join(f"{L} {v}" for L, v in c["letters"].items()))
    return "; ".join(bad)


# Lines that state an answer ("**Correct Answer: B**", "Answer: (C)", "Answer key: DDAB..."), not stems that merely
# say "select the correct option".
ANSWER_LEAK = re.compile(r"(?im)^[\s>*_]*(?:(?:correct\s+)?(?:answer|option)|answer\s+key|mc_key)[\s*_]*[:=][^\n]*(?:\n|\Z)"
                         r"|^[^\n]*=====SOLUTIONS=====[^\n]*(?:\n|\Z)")


def strip_answer_leaks(paper: str, key: str = "") -> tuple[str, int]:
    """Remove lines from the question paper that would give answers away: a writer's stray "Correct Answer: B"
    or answer-key line, the solutions separator, or the drawn key string itself. Returns (paper, lines removed)."""
    out, n = ANSWER_LEAK.subn("", paper)
    if len(key) >= 5 and key in out:
        lines = out.splitlines(keepends=True)
        kept = [ln for ln in lines if key not in ln]
        n, out = n + len(lines) - len(kept), "".join(kept)
    return out, n


def readable(p) -> bool:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(p)).pages) > 0
    except Exception:
        return False


def capped(paths, mb):
    out, total = [], 0
    for p in paths:
        size = p.stat().st_size
        if total + size <= mb * 2**20 and readable(p):
            out.append(p)
            total += size
    return out


def inputs(u, inp: "unit_inputs.Inputs | None" = None):
    """Everything a mock is written from, resolved by unit_inputs.resolve() (the single source of truth for inputs)."""
    inp = inp or unit_inputs.resolve(u)
    scope, lectures, hand = inp.scope, inp.lectures, inp.transcriptions
    practice = capped(inp.practice, MAX_PRACTICE_MB)
    own = capped(inp.own_work, MAX_OWN_MB)
    texts = [f"HANDWRITTEN (the student's own notes, rank 1): {h.name}\n\n{h.read_text('utf-8')}" for h in hand]
    if own:
        texts.insert(0, OWN_NOTE.format(names=", ".join(p.name for p in own)))
    reference = capped(inp.reference, MAX_REFERENCE_MB)
    if reference:
        texts.insert(0, REFERENCE_NOTE.format(names=", ".join(p.name for p in reference)))
    intel = intel_block(u, "mock")             # word on the street (ingest_intel.py): below the official tier
    if intel:
        texts.insert(0, intel)
    canvas = inp.notes.get("canvas")
    if canvas:                        # lecturer's exam announcements + where you actually lost marks
        texts.insert(0, "CANVAS (official course site): the lecturer's exam-related announcements are part of the "
                        "exam scope (top tier). 'Where marks were lost' lists the student's real weaknesses: include "
                        f"at least one question that tests each of them.\n\n{canvas.read_text('utf-8')}")
    outline = inp.notes.get("outline")
    if outline:                                # official LOs, weekly topics, final-exam description
        texts.insert(0, "UNIT OUTLINE (official, sydney.edu.au): learning outcomes, weekly topics and the final "
                        "exam's description. Cover the learning outcomes the final exam assesses, in proportion "
                        f"to the weekly schedule.\n\n{outline.read_text('utf-8')}")
    seen = mock_writer.practised(u)
    if seen:
        texts.append("ALREADY PRACTISED (exam-style problems the student has worked; never reuse them):\n\n"
                     + "\n\n".join(f"[{a}] {b.strip()}" for a, b in seen))
    texts.append("WEAK POINTS (the student's own record: practice and recall scores, error log, habits). Use as "
                 "the prompt's 'Target the weak points' says.\n\n" + weak_points(u))
    return scope, lectures, practice + reference, own, hand, texts


def mock_no(p) -> int | None:
    """mock_exam_7.md → 7; solutions, attempts and anything else → None."""
    m = re.fullmatch(r"mock_exam_(\d+)", p.stem)
    return int(m.group(1)) if m else None


def mocks(outdir) -> list:
    """The unit's mock papers in numeric order (mock_exam_10 after mock_exam_9)."""
    return sorted((p for p in outdir.glob("mock_exam_*.md") if mock_no(p) is not None), key=mock_no)


def free_number(outdir, after: int) -> int:
    """First number above `after` whose paper and solutions files are both unused. Never fills a gap left by a
    deleted mock (its attempt checks still refer to the old paper), never overwrites."""
    n = after + 1
    while (outdir / f"mock_exam_{n}.md").exists() or (outdir / f"mock_exam_{n}_solutions.md").exists():
        n += 1
    return n


def with_retries(fn, *a, tries: int = 3, wait_s: int = 30, **kw):
    """A model call, retried on transient failures (agy timeout or crash). Quota exhaustion is not retried."""
    for attempt in range(1, tries + 1):
        try:
            return fn(*a, **kw)
        except gemini.DailyQuotaExhausted:
            raise
        except RuntimeError as e:
            if attempt == tries:
                raise
            print(f"    model call failed ({str(e)[:150]}); retry {attempt}/{tries - 1} in {wait_s} s", flush=True)
            time.sleep(wait_s)


def outline_readings(u) -> list[str]:
    p = STATE / "outlines" / f"{u['code']}.json"
    if not p.exists():
        return []
    r = json.loads(p.read_text("utf-8")).get("readings", "")
    return [f"Unit outline readings: {r}"] if r else []


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--n", type=int, default=1, help="number of new mocks per unit")
    ap.add_argument("--research", action="store_true", help="allow Google Search grounding")
    ap.add_argument("--if-missing", action="store_true", help="skip units that already have a mock")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ignore-input-check", action="store_true",
                    help="write the mock even if unit_inputs.py reports a critical input problem")
    args = ap.parse_args()

    verify_t = (PROMPTS / "practice_exam_verify.md").read_text("utf-8")
    for u in work_units(args.unit, args.allow_sat):
        outdir = unit_path(u, "practice")
        existing = mocks(outdir)
        if args.if_missing and existing:
            print(f"{u['code']}: {len(existing)} mock(s) exist, skipping")
            continue
        inp = unit_inputs.resolve(u)
        scope, lectures, practice, own, hand, texts = inputs(u, inp)
        has = lambda k: "yes" if inp.notes.get(k) else "NO"
        print(f"{u['code']}: {len(scope)} scope page(s), {len(lectures)} lectures, {len(practice)} practice PDFs, "
              f"{len(own)} own-work PDFs, {len(hand)} handwritten transcription(s); exam intel: {has('intel')}, "
              f"outline: {has('outline')}, canvas: {has('canvas')}  (full list: python scripts/unit_inputs.py "
              f"--unit {u['code']})")
        crit, warn = unit_inputs.problems(u, inp)
        for m in warn:
            print(f"  WARN {m}")
        for m in crit:
            print(f"  CRITICAL {m}")
        if crit and not args.ignore_input_check and not args.dry_run:
            print(f"  STOPPED {u['code']}: fix the input problem above first (or --ignore-input-check)")
            continue
        if not (u["exam"].get("format") or "").strip():
            print(f"  WARN {u['code']}: exam.format is empty in the unit config, so the writers guess the paper's "
                  "structure (sections, number of questions) and missing questions can't be detected. Paste the "
                  "format from the exam-info page or Canvas first if you have it.")
        if args.dry_run:
            for p in scope + practice:
                print(f"   {p.name}")
            for p in own:
                print(f"   [own] {p.name}")
            continue

        ex = u["exam"]
        refs = (u.get("references") or []) + outline_readings(u)
        research = ("You may use Google Search for textbook-style problems and other universities' exams on the "
                    "same topics, to vary question style. Stay inside the scope. Preferred references: "
                    + ("; ".join(refs) if refs else "standard textbooks for this subject")) if args.research \
            else "No web research: use only the attached material."
        pdfs = scope + lectures + practice + own
        taken: set[str] = set()
        context = [gemini.context_name(t, taken) for t in texts]     # the names agy sees for the text blocks
        print(f"  context sent to the writers: {len(pdfs)} PDF(s) + text blocks {', '.join(context)}", flush=True)
        write = lambda files, prompt, txt: with_retries(gemini.generate, files, prompt, txt, search=args.research)
        for i in range(args.n):
            n = free_number(outdir, mock_no(existing[-1]) if existing else 0)
            avoid = ""
            if existing:
                prev = "\n\n".join(p.read_text("utf-8")[:6000] for p in existing[-3:])
                avoid = "EARLIER MOCKS (do not repeat their questions):\n" + prev
            ckpt = STATE / "mock_drafts" / f"{u['id']}_mock_{n}.json"
            draft = json.loads(ckpt.read_text("utf-8")) if ckpt.exists() else {}
            # recorded in the mock, so draws can be redone; a draft from a failed run keeps its seed and resumes
            seed = draft.get("seed") or random.SystemRandom().randrange(1, 10**6)
            rng = random.Random(seed)
            secs = mock_writer.plan(u, mc_count(u))
            base = dict(n=n, unit_code=u["code"], unit_name=u["name"], seed=seed,
                        reading_min=ex.get("reading_min", 10), writing_min=ex.get("writing_min", 120),
                        conditions=ex.get("conditions", ""), materials="; ".join(ex.get("materials") or []),
                        format=(ex.get("format") or "").strip() or "(none)",
                        scope=(ex.get("scope") or "").strip() or "(none: use the scope pages, else all lectures)",
                        research=research, avoid=avoid,
                        pace=f"{ex.get('total_marks', 100)} marks total, {pace(u)[1]}")
            print(f"  mock {n}: {len(secs)} section(s): " + ", ".join(
                f"{s.get('id') or 'paper'} {s['kind']} {s['marks']} marks" for s in secs), flush=True)
            resume = f"Finished sections are kept in state/mock_drafts/{ckpt.name}: run the same command to resume."
            started = datetime.now(TZ)
            try:
                res = mock_writer.build(u, secs, base, pdfs, texts, write, rng, draws, existing,
                                        log=lambda m: print(m, flush=True), checkpoint=ckpt)
            except gemini.DailyQuotaExhausted as e:
                print(f"STOPPED: {e}. {resume}")
                return
            except ValueError as e:
                print(f"  FAIL mock {n}: {e}; not saved. {resume}")
                return
            except RuntimeError as e:                 # agy failed even after retries
                print(f"  FAIL mock {n}: {str(e)[:300]}. {resume}")
                return
            except OSError as e:                      # an input file moved or became unreadable mid-run
                print(f"  FAIL mock {n}: input file problem: {e}. Check `python scripts/unit_inputs.py --unit "
                      f"{u['code']}`, then {resume[0].lower() + resume[1:]}")
                return
            questions, solutions = mock_writer.assemble(res)
            checked = "unchecked"
            unchecked = solutions                     # kept for the generation log: what did the check change?
            if not args.no_verify:
                print(f"  mock {n}: checking solutions…", flush=True)
                try:
                    fixed = with_retries(gemini.generate, [], fill(verify_t, unit_code=u["code"], unit_name=u["name"]),
                                         texts=["QUESTIONS (the mock exam paper):\n\n" + questions,
                                                "SOLUTIONS TO CHECK:\n\n" + solutions], temperature=0.1).strip()
                    want, got = set(mock_writer.blocks(solutions)), set(mock_writer.blocks(fixed))
                    if fixed.startswith("#") and want <= got:
                        solutions, checked = fixed, "checked by a second Gemini pass"
                    else:
                        lost = ", ".join(f"Q{k}" for k in sorted(want - got)) or "its format"
                        print(f"  WARN mock {n}: the solution check's reply was incomplete (missing {lost}); "
                              "kept the unchecked solutions", flush=True)
                        checked = "unchecked (the check's reply was incomplete)"
                except gemini.DailyQuotaExhausted as e:
                    print(f"  (solutions left unchecked: {e})")
                except RuntimeError as e:
                    print(f"  (solutions left unchecked: the check failed: {str(e)[:150]})")
            key = list(res["key"])
            off = key_mismatch(solutions, key) if key else []       # the solution check may change an answer
            cues = cue_note(mc_cues(questions, solutions))
            issues = res["unresolved"] + ([f"answers off the key after the solution check: {', '.join(off)}"] if off else []) \
                + ([f"multiple choice may be guessable: {cues}"] if cues else [])
            for msg in issues:
                print(f"  WARN mock {n}: {msg}", flush=True)
            questions, leaks = strip_answer_leaks(questions, res["key"])
            if leaks:
                print(f"  WARN mock {n}: removed {leaks} line(s) from the paper that gave answers away", flush=True)

            def front(kind: str) -> str:
                # The answer key and the seed (which regenerates the key) go in the solutions only, never the paper.
                secret = ([f"seed: {seed}"] + ([f"mc_key: {res['key']}"] if key else [])
                          + [f"context: [{', '.join(context)}]", f"pdfs: {len(pdfs)}"]) if kind == "mock solutions" else []
                return "\n".join(["---", f"unit: {u['code']}", f"type: {kind}", f"mock: {n}", "source: ai", "rank: 6",
                                  f"model: {gemini.used_model()}", f"research: {str(args.research).lower()}",
                                  f"generated: {datetime.now(TZ):%Y-%m-%d}"] + secret
                                 + [f"open_issues: {len(issues)}", "---", ""])
            outdir.mkdir(parents=True, exist_ok=True)
            if (outdir / f"mock_exam_{n}.md").exists() or (outdir / f"mock_exam_{n}_solutions.md").exists():
                taken, n = n, free_number(outdir, n)   # another run saved this number meanwhile: never overwrite
                print(f"  WARN mock_exam_{taken} appeared while this one was written; saving as mock_exam_{n}", flush=True)
            q = outdir / f"mock_exam_{n}.md"
            s = outdir / f"mock_exam_{n}_solutions.md"
            mats = "; ".join(ex.get("materials") or []) or "none listed"
            head = (f"# {u['code']} mock exam {n}\n\n> [!info] Exam conditions\n> {ex.get('reading_min', 10)} min reading + "
                    f"{ex.get('writing_min', 120)} min writing. {ex.get('conditions', '')}. Allowed: {mats}. "
                    f"{ex.get('total_marks', 100)} marks; {pace(u)[1]}.\n\n")
            # The issues can name answers ("the correct answer must be option D"), so the paper only gets a count.
            paper_note = (f"> [!warning] {len(issues)} open issue(s) from the coordinator's checks. They are listed in "
                          f"[[{s.stem}]]; read them after sitting the paper, as they can give answers away.\n\n"
                          if issues else "")
            sol_note = ("> [!warning] Not fully clean after the coordinator's checks:\n"
                        + "\n".join(f"> - {x}" for x in issues) + "\n\n" if issues else "")
            q.write_text(front("mock exam") + "\n> [!tip] Sit this timed, with only your allowed materials. "
                         f"Then mark it with [[{s.stem}]] and log every lost mark in [[error_log]].\n\n"
                         + paper_note + head + questions + "\n", "utf-8")
            s.write_text(front("mock solutions") + f"\n> [!check] AI-written solutions, {checked}. Where your "
                         "answer disagrees, check the cited slide before trusting either.\n\n" + sol_note
                         + f"# {u['code']} mock exam {n}: solutions\n\n" + solutions + "\n", "utf-8")
            existing.append(q)
            ckpt.unlink(missing_ok=True)
            print(f"  ok {q.name} + {s.name}", flush=True)
            try:                                      # the mock is saved; a logging problem must not lose it
                norm = lambda b: " ".join(b.split())
                before_s, after_s = mock_writer.blocks(unchecked), mock_writer.blocks(solutions)
                verify = dict(status=checked,
                              changed=[k for k, b in sorted(before_s.items()) if norm(after_s.get(k, "")) != norm(b)],
                              corrected=[k for k, b in sorted(after_s.items()) if "Corrected:" in b])
                meta = dict(seed=seed, model=gemini.used_model(), research=args.research,
                            started=started.isoformat(timespec="seconds"),
                            minutes=round((datetime.now(TZ) - started).total_seconds() / 60, 1))
                logged = mock_log.write(u, n, res, meta, verify, issues)
                print(f"  generation log: {logged.relative_to(STATE.parent)} (contains answers: read it after sitting)",
                      flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  WARN generation log not written: {e}", flush=True)


if __name__ == "__main__":
    main()
