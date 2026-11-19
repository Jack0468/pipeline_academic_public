"""Mock-exam coordinator: one writer agent per section, then checks and targeted rewrites until clean.

gen_practice_exam.py calls build(). Per mock:
  1. plan the sections from the exam format (e.g. ABCD1001: A = 20 multiple choice, B = 40 marks written);
  2. the WRITTEN-answer writer goes first (the big questions claim the core topics), then the MULTIPLE-
     CHOICE writer, which is shown Section B so it tests other things. Each writer is one agy call with
     the slides, scope and practice PDFs, its own rules file and its own random draws;
  3. checks (Python, no AI): overlap with the rest of this paper, earlier mocks and the exam-style
     problems already practised; for multiple choice, the pre-drawn answer key and the longest-answer
     cue; coverage (no lecture over ~20% of the marks);
  4. any question that fails goes back to its writer with the reason (only those questions are
     rewritten), up to MAX_ROUNDS rounds; anything left is reported in the mock as a warning;
  5. the caller then assembles the paper and runs the solution check.
Research behind the rules: docs/EXAM_WRITING.md.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path

from common import PROMPTS, fill, unit_path

SPLIT = "=====SOLUTIONS====="
MAX_ROUNDS = 3
# Calibrated on mock 1 against the 50 practised recall problems (2026-11-14): unrelated pairs have a median
# overlap of 0.03; true repeats scored 0.47-0.53 (Q4 = L2 problem 2 with the same 30 and 45 degree beamwidths;
# Q12 = the L13 TE10 cutoff problem with new numbers).
SIM_FLAG = 0.45          # content-word overlap (Jaccard) at which two questions count as the same question
SIM_WITH_NUMBERS = 0.25  # lower bar when they also share >= 2 specific numbers (the same givens reused)
MAX_LECTURE_SHARE = 0.20
QBLOCK = re.compile(r"^## Question (\d+)[^\n]*\n.*?(?=^## Question |^# |\Z)", re.S | re.M)
STOP = set("""about above after again against also because been before being below between both each from have
having here into itself more most only other over same should some such than that their them then there these
they this those through under until very what when where which while with would your given find calculate
determine compute using show value values question marks mark part following answer correct assume total
state explain shown write""".split())


# --- parsing ---------------------------------------------------------------------------------------

def blocks(md: str) -> dict[int, str]:
    return {int(m.group(1)): m.group(0).rstrip() for m in QBLOCK.finditer(md)}


def heading(md: str) -> str:
    m = re.search(r"^# Section[^\n]*\n(?:(?!^## ).*\n)*", md, re.M)
    return m.group(0).rstrip() if m else ""


def content_words(t: str) -> set[str]:
    t = re.sub(r"\$[^$]*\$", " ", t)
    t = re.sub(r"\*(Source|Values|Targets|Tests):[^\n]*", " ", t)
    return {w for w in re.findall(r"[a-z][a-z\-]{3,}", t.lower()) if w not in STOP}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


TRIVIAL = {"0", "1", "2", "3", "4", "5", "10", "100", "0.5", "8", "12", "20", "90", "180", "360"}


def numbers(t: str) -> set[str]:
    """Specific given values (not 0, 1, 2, powers of ten or right angles), normalised: '30.0' == '30'."""
    out = set()
    for x in re.findall(r"(?<![A-Za-z_^{])\d+(?:\.\d+)?", re.sub(r"## Question \d+[^\n]*", "", t)):
        x = x.rstrip("0").rstrip(".") if "." in x else x
        if x not in TRIVIAL:
            out.add(x)
    return out


def same_question(a: str, b: str) -> bool:
    s = jaccard(content_words(a), content_words(b))
    return s >= SIM_FLAG or (s >= SIM_WITH_NUMBERS and len(numbers(a) & numbers(b)) >= 2)


def tag(sol: str) -> set[str]:
    m = re.search(r"\*Tests:\s*([^*\n]+)\*", sol)
    return content_words(m.group(1)) if m else set()


def practised(u) -> list[tuple[str, str]]:
    """(label, text) of every exam-style problem in the recall files: questions the student has seen."""
    out = []
    for p in sorted(unit_path(u, "recall").glob("*.md")):
        sec = re.search(r"^## Exam-style problems.*?(?=^## (?!Exam-style)|\Z)", p.read_text("utf-8"), re.S | re.M)
        if not sec:
            continue
        for m in re.finditer(r"^(\d+)\. (.*?)(?=^\d+\. |\Z)", sec.group(0), re.S | re.M):
            body = re.split(r"\*\*Problem total|> \[!answer\]", m.group(2))[0]
            out.append((f"{p.stem.split('_')[-1]} problem {m.group(1)}", body))
    return out


def earlier(paths: list[Path]) -> list[tuple[str, str]]:
    out = []
    for p in paths:
        for k, b in blocks(p.read_text("utf-8")).items():
            out.append((f"{p.stem} Q{k}", b))
    return out


# --- sections --------------------------------------------------------------------------------------

def plan(u, mc_count: int) -> list[dict]:
    ex = u["exam"]
    total = int(ex.get("total_marks") or 100)
    fmt = " ".join(str(ex.get("format") or "").split())
    secs = []
    if mc_count:
        m = re.search(r"(\d+(?:\.\d+)?)\s*marks?\s+each", fmt)
        per = float(m.group(1)) if m else 1.0
        secs.append(dict(kind="mc", count=mc_count, per=per, marks=round(mc_count * per)))
    m = re.search(r"(\d+)\s*extended", fmt, re.I)
    if not m and not mc_count:                      # a written-only paper: "3 questions, ... 10 marks each"
        m = re.search(r"\b(\d+)\s+questions?\b", fmt, re.I)
    rest = total - sum(s["marks"] for s in secs)
    if rest > 0:
        secs.append(dict(kind="written", count=int(m.group(1)) if m else None, marks=rest))
    if len(secs) > 1:
        for letter, s in zip("ABCD", secs):
            s["id"] = letter
    first = 1
    for s in secs:                                   # numbering runs through the paper in section order
        s["first"] = first
        first += s["count"] or 0
    return secs


def task(s: dict, n_sections: int) -> str:
    sec = f"Section {s['id']}" if n_sections > 1 else "the whole paper"
    head = (f"Start with the heading `# Section {s['id']}: {'Multiple choice' if s['kind'] == 'mc' else 'Extended answer'}"
            f" ({s['marks']} marks)`, then the questions." if n_sections > 1 else "Start directly with the questions.")
    if s["kind"] == "mc":
        return (f"Write ONLY {sec}: {s['count']} multiple-choice questions, {s['per']:g} mark each ({s['marks']} marks), "
                f"numbered Question {s['first']} to Question {s['first'] + s['count'] - 1}. {head}")
    n = f"{s['count']} extended-answer questions" if s["count"] else "4-7 questions with parts (a), (b), (c)"
    return (f"Write ONLY {sec}: {n}, {s['marks']} marks in total, numbered from Question {s['first']}, with the marks "
            f"on each part. {head}")


# --- checks ----------------------------------------------------------------------------------------

def overlap_issues(qs: dict[int, str], sols: dict[int, str], against: list[tuple[str, str]]) -> dict[int, str]:
    """Question number → reason, for questions that repeat another question in this paper, an earlier mock or
    a practised problem (same content words, or the same *Tests:* tag)."""
    issues = {}
    nums = sorted(qs)
    for i, k in enumerate(nums):
        for j in nums[:i]:
            tk, tj = tag(sols.get(k, "")), tag(sols.get(j, ""))
            if same_question(qs[k], qs[j]) or (tk and jaccard(tk, tj) >= 0.75):
                issues[k] = f"tests the same thing as Question {j} of this paper"
                break
        if k in issues:
            continue
        for label, text in against:
            if same_question(qs[k], text):
                issues[k] = f"too close to {label} (already {'in this paper' if label.startswith('this paper') else 'seen'})"
                break
    return issues


def mc_issues(qs: dict[int, str], sols: dict[int, str], key: dict[int, str]) -> dict[int, str]:
    """Wrong letter for the pre-drawn key, or the correct option is the longest (when that happens more
    often than chance, every such question is sent back)."""
    issues, longest = {}, []
    for k, body in qs.items():
        opts = dict(re.findall(r"^\s*(?:\*\*)?\(?([A-D])[.)](?:\*\*)?\s+(.+)$", body, re.M))
        m = re.search(r"correct\s+answer\W{0,6}\s*\(?([A-D])\b", sols.get(k, ""), re.I)
        got = m.group(1).upper() if m else None
        if key.get(k) and got != key[k]:
            issues[k] = f"the correct answer must be option {key[k]} (it is {got or 'not stated'})"
            continue
        if len(opts) == 4 and got:
            size = {L: len(re.sub(r"\$[^$]*\$", "xxxx", t).strip()) for L, t in opts.items()}
            if size[got] > max(v for L, v in size.items() if L != got):     # equal length is the goal
                longest.append(k)
        elif len(opts) != 4:
            issues[k] = "needs exactly four options A-D, each on its own line"
    if len(longest) > max(1, round(0.25 * len(qs))):
        for k in longest:
            issues.setdefault(k, "the correct option is the longest; make all four options the same length and detail")
    return issues


# --- givens: no value together with its own conversion ------------------------------------------------
C0 = 2.998e8
_NUM = r"(\d+(?:\.\d+)?)(?:\s*(?:\\times|×)\s*10\^\{?(-?\d+)\}?)?"
_GAP = r"(?:\s|\\[,;:!]|~|\\text\{|\\mathrm\{|[{}$])*"
_FREQ = re.compile(_NUM + _GAP + r"([kMGT]?)Hz\b")
_LAMBDA = re.compile(r"(?:(?:\\lambda|λ)(?:_\{?0\}?)?\s*(?:=|\\approx|≈)|wavelength\s+(?:of\s+|is\s+)?)\s*" + _GAP
                     + _NUM + _GAP + r"(nm|\\mu\s*m|µm|um|mm|cm|m)\b")
_OMEGA = re.compile(r"(?:\\omega|ω)(?:_\{?0\}?)?\s*=\s*" + _GAP + _NUM)
_BETA = re.compile(r"(?:\\beta|β|k_\{?0\}?)(?:_\{?0\}?)?\s*=\s*" + _GAP + _NUM)
_POWER_PAIR = re.compile(r"(?:[kmµ]?W\b|\\text\{\s*[kmµ]?W\s*\})" + _GAP + r"\(" + _GAP + r"[+\-−]?\d[^)]*dB[mW]"
                         r"|dB[mW]\b[^()\n]{0,12}\(" + _GAP + r"\d[^)]*[kmµ]?W\b"
                         r"|dB[id]?\b" + _GAP + r"\(" + _GAP + r"(?:linear\s*)?\d+(?:\.\d+)?" + _GAP + r"\)")
_PREFIX = {"": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
_LEN = {"nm": 1e-9, "um": 1e-6, "µm": 1e-6, "mm": 1e-3, "cm": 1e-2, "m": 1}


def _val(m: re.Match) -> float:
    return float(m.group(1)) * (10 ** int(m.group(2)) if m.group(2) else 1)


def conversion_issue(text: str) -> str | None:
    """Why a question's givens include a value together with its own conversion (f with λ = c/f, f with
    ω = 2πf, λ with β = 2π/λ, W with dBm, dB with linear), or None. Compares the numbers, so unrelated
    pairs (a 1550 nm laser modulated at 10 GHz) pass. Fallback lines ("If you could not find ...") are ignored."""
    text = re.sub(r"(?im)^[^\n]*if you could not[^\n]*$", "", text)
    freqs = [_val(m) * _PREFIX[m.group(3)] for m in _FREQ.finditer(text)]
    lams = [_val(m) * _LEN["um" if "mu" in m.group(3) else m.group(3)] for m in _LAMBDA.finditer(text)]
    omegas = [_val(m) for m in _OMEGA.finditer(text)]
    betas = [_val(m) for m in _BETA.finditer(text)]
    for f in freqs:
        for lam in lams:
            if 0.3 <= f * lam / C0 <= 1.05:                 # free space, or guided in a dielectric up to ~eps_r 11
                return "gives a value together with its own conversion: the frequency and its wavelength"
        for w in omegas:
            if abs(w / (2 * math.pi * f) - 1) < 0.05:
                return "gives a value together with its own conversion: f and omega = 2 pi f"
    for lam in lams:
        for b in betas:
            if 0.95 <= b * lam / (2 * math.pi) <= 3.4:
                return "gives a value together with its own conversion: the wavelength and beta = 2 pi / lambda"
    if _POWER_PAIR.search(text):
        return "gives a value together with its own conversion: a level in dB and its linear value (or W and dBm)"
    return None


def given_issues(qs: dict[int, str], kind: str) -> dict[int, str]:
    """Question number → reason, for questions that hand the student a conversion (checks the stem only for
    multiple choice, since the options are meant to be values)."""
    out = {}
    for k, body in qs.items():
        stem = re.split(r"(?m)^\s*(?:\*\*)?\(?A[.)]", body, maxsplit=1)[0] if kind == "mc" else body
        why = conversion_issue(stem)
        if why:
            out[k] = why + "; give only the form a practitioner would quote and let the student convert"
    return out


def coverage(all_q: dict[int, str], all_s: dict[int, str]) -> dict[str, float]:
    """Share of the paper's marks per lecture, from each question's marks and its *Source: L<n>* line."""
    share, total = defaultdict(float), 0.0
    for k, q in all_q.items():
        m = re.search(r"\((\d+(?:\.\d+)?)\s*marks?", q)
        mk = float(m.group(1)) if m else 1.0
        total += mk
        labs = sorted(set(re.findall(r"\bL(\d+)\b", re.search(r"\*Source:[^\n]*", all_s.get(k, "")).group(0)
                                      if re.search(r"\*Source:[^\n]*", all_s.get(k, "")) else "")))
        for lab in labs:
            share[f"L{lab}"] += mk / len(labs)
    return {k: v / total for k, v in share.items()} if total else {}


# --- the loop --------------------------------------------------------------------------------------

# --- generation log: what was sent back, and what replaced it ---------------------------------------
_GIVEN = re.compile(r"\d+(?:\.\d+)?\s*(?:\\times\s*10\^\{?-?\d+\}?)?\s*(?:\\,|\\;|~|\\text\{\s*|\\mathrm\{\s*)?"
                    r"(?:[kMGTmµunp]?(?:Hz|m|W|V|A|s|F|H|S)\b|dB[imWd]?\b|\\Omega|Ω|ohms?\b|%|\^\\circ|°)")


def profile(q: str | None, s: str | None = None) -> dict | None:
    """Simple, comparable difficulty proxies for one question and its solution, so a replacement can be
    compared with what it replaced. Proxies only: the real test is your marks on it (practice checks)."""
    if not q:
        return None
    stem = re.sub(r"^## Question[^\n]*\n", "", q)
    prose = re.sub(r"\$\$.*?\$\$|\$[^$]*\$", " ", stem, flags=re.S)
    m = re.search(r"\((\d+(?:\.\d+)?)\s*marks?", q)
    s = s or ""
    return dict(marks=float(m.group(1)) if m else None,
                stem_words=len(re.findall(r"[A-Za-z]{2,}", prose)),
                parts=len(set(re.findall(r"(?m)^\s*\(([a-h])\)", stem))),
                subparts=len(re.findall(r"(?m)^\s*\((?:i{1,3}|iv|v|vi)\)", stem)),
                givens=len(_GIVEN.findall(stem)),
                mark_steps=len(re.findall(r"\[\d+(?:\.\d+)?\]", s)),
                display_equations=s.count("$$") // 2,
                solution_words=len(re.findall(r"[A-Za-z]{2,}", re.sub(r"\$\$.*?\$\$|\$[^$]*\$", " ", s, flags=re.S))))


def provenance(sid: str, qs: dict[int, str], history: list[dict]) -> dict[int, dict]:
    """For each final question of a section: first pass, or replaced (in which rounds), or left unresolved."""
    out = {}
    for k in sorted(qs):
        recs = [h for h in history if h["section"] == sid and h["question"] == k and not h.get("abandoned")]
        replaced = [h["round"] for h in recs if h["status"] == "replaced"]
        status = ("unresolved" if any(h["status"] == "unresolved" for h in recs)
                  else f"replaced (round {', '.join(map(str, replaced))})" if replaced
                  else "sent back, not replaced" if recs else "first pass")
        out[k] = dict(section=sid, status=status, times_sent_back=len([h for h in recs if h["status"] != "unresolved"]),
                      reasons=[h["reason"] for h in recs])
    return out


# The separator the prompts ask for, plus the near-misses models write instead ("===== SOLUTIONS =====",
# "**=====SOLUTIONS=====**", a "# Solutions" heading).
SPLIT_RE = re.compile(r"(?im)^[ \t*_>]*=+[ \t]*SOLUTIONS?[ \t]*=+[ \t*_]*$|^#{1,2}[ \t]+(?:worked[ \t]+)?solutions?\b[^\n]*$")


def split_reply(text: str) -> tuple[str, str]:
    m = SPLIT_RE.search(text)                  # whole separator line, including any bold or spacing around it
    if m:
        return text[:m.start()].strip(), text[m.end():].strip()
    if SPLIT in text:                          # the exact separator inside a longer line
        q, s = text.split(SPLIT, 1)
        return q.strip(), s.strip()
    raise ValueError("no solutions separator in the reply")


def save_checkpoint(path: Path | None, base: dict, done: dict, unresolved: list, rng, journal: dict | None = None) -> None:
    """Finished sections + the RNG state + the generation journal (history, calls), so a run that fails later
    (agy timeout, quota) resumes instead of starting over. gen_practice_exam.py deletes the file once the mock
    is saved."""
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"seed": base["seed"], "n": base["n"], "unresolved": unresolved, "rng": rng.getstate(),
            "journal": journal or {},
            "sections": {sid: {"q": d["q"], "qs": d["qs"], "sols": d["sols"], "key": d["key"]}
                         for sid, d in done.items()}}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), "utf-8")
    tmp.replace(path)


def load_checkpoint(path: Path | None, base: dict, secs: list[dict], rng) -> tuple[dict, list, dict]:
    if not path or not path.exists():
        return {}, [], {}
    data = json.loads(path.read_text("utf-8"))
    if data.get("seed") != base["seed"]:
        return {}, [], {}
    by_id = {s.get("id", ""): s for s in secs}
    ints = lambda m: {int(k): v for k, v in m.items()}
    done = {sid: dict(sec=by_id[sid], q=d["q"], qs=ints(d["qs"]), sols=ints(d["sols"]), key=ints(d["key"]))
            for sid, d in data["sections"].items() if sid in by_id}
    st = data["rng"]
    rng.setstate((st[0], tuple(st[1]), st[2]))
    return done, list(data.get("unresolved", [])), dict(data.get("journal") or {})


def lecture_cap(secs: list[dict]) -> float:
    """Most of the marks one lecture may carry: 20%, or one whole question on a paper of few large questions
    (3 x 10 marks: a question built on one lecture is 33%, which the real paper does too)."""
    sizes = [s["marks"] / s["count"] for s in secs if s.get("kind") == "written" and s.get("count")]
    total = sum(s["marks"] for s in secs) or 1
    return max([MAX_LECTURE_SHARE] + [m / total for m in sizes])


def build(u, secs: list[dict], base: dict, pdfs: list[Path], texts: list[str], generate, rng, draws_fn,
          earlier_mocks: list[Path], log=print, checkpoint: Path | None = None) -> dict:
    """Run the writers and the check-and-rewrite loop. `generate(pdfs, prompt, texts)` is the model call.
    Returns questions/solutions per section, the answer key, the seeds' draws and any unresolved issues.
    With `checkpoint`, each finished section is saved there, and finished sections found there are reused."""
    template = (PROMPTS / "practice_exam.md").read_text("utf-8")
    rules = {k: (PROMPTS / f"practice_exam_{k}.md").read_text("utf-8") for k in ("mc", "written")}
    revise_t = (PROMPTS / "practice_exam_revise.md").read_text("utf-8")
    seen = earlier(earlier_mocks) + [(f"practised {a}", b) for a, b in practised(u)]
    order = sorted(secs, key=lambda s: s["kind"] != "written")   # written first, then multiple choice
    done, unresolved, journal = load_checkpoint(checkpoint, base, secs, rng)
    history: list[dict] = journal.get("history", [])      # one record per question sent back, per round
    calls: list[dict] = journal.get("calls", [])          # one record per model call (what, how long)
    prov: dict[str, dict] = journal.get("provenance", {})
    for h in history:            # records from a section a failed run never finished: that section is rewritten
        if h["section"] not in done:
            h["abandoned"] = True
    save = lambda: save_checkpoint(checkpoint, base, done, unresolved, rng,
                                   journal=dict(history=history, calls=calls, provenance=prov))

    def timed(what: str, section: str, rnd: int, files, prompt, txt):
        t = time.monotonic()
        record = lambda ok: calls.append(dict(what=what, section=section, round=rnd,
                                              seconds=round(time.monotonic() - t, 1), ok=ok))
        try:
            out = generate(files, prompt, txt)
        except BaseException:
            record(False)
            save()               # keep the journal (this failed call, this section's records) for the resume
            raise
        record(True)
        return out

    if done:
        log(f"    resuming from {checkpoint.name}: " + ", ".join(f"Section {k}" if k else "paper" for k in done)
            + " already written")
    for s in order:
        sid = s.get("id", "")
        if sid in done:
            continue
        key = {}
        if s["kind"] == "mc":
            from gen_practice_exam import answer_key
            letters = answer_key(s["count"], rng)
            key = {s["first"] + i: L for i, L in enumerate(letters)}
        other = "\n\n".join(heading(d["q"]) + "\n" + "\n\n".join(d["qs"].values()) for d in done.values())
        in_paper = ("ALREADY IN THIS PAPER (the other section; do not repeat these questions; you may test other "
                    "aspects of the same topics):\n\n" + other) if other else "(this is the first section written)"
        sec_rules = fill(rules[s["kind"]], mc_key=", ".join(f"Q{k} {L}" for k, L in key.items()) or "(none)")
        prompt = fill(template, **base, draws=draws_fn(rng), section_task=task(s, len(secs)),
                      section_rules=sec_rules, in_paper=in_paper)
        label = f"Section {sid}" if sid else "paper"
        log(f"    {label} ({s['kind']}): writing…")
        for attempt in range(2):
            try:
                q_md, s_md = split_reply(timed("write", sid, 0, pdfs, prompt, texts))
                break
            except ValueError:
                if attempt:
                    raise
                log(f"    {label}: the reply had no solutions separator; asking again")
        qs, sols = blocks(q_md), blocks(s_md)
        against = seen + [(f"this paper Q{k}", b) for d in done.values() for k, b in d["qs"].items()]
        for rnd in range(1, MAX_ROUNDS + 1):
            issues = overlap_issues(qs, sols, against)
            for k, why in given_issues(qs, s["kind"]).items():
                issues.setdefault(k, why)
            if s["kind"] == "mc":
                for k, why in mc_issues(qs, sols, key).items():
                    issues.setdefault(k, why)
            expected = range(s["first"], s["first"] + s["count"]) if s["count"] else []
            for k in expected:
                if k not in qs:
                    issues[k] = "missing: write this question"
            if not issues:
                break
            if rnd == MAX_ROUNDS:
                unresolved += [f"{label} Q{k}: {why}" for k, why in sorted(issues.items())]
                history += [dict(section=sid, kind=s["kind"], round=rnd, question=k, reason=why, status="unresolved",
                                 before=dict(question=qs.get(k), solution=sols.get(k),
                                             profile=profile(qs.get(k), sols.get(k))), after=None)
                            for k, why in sorted(issues.items())]
                break
            log(f"    {label}: round {rnd}: rewriting {len(issues)} question(s): "
                + ", ".join(f"Q{k}" for k in sorted(issues)))
            recs = {k: dict(section=sid, kind=s["kind"], round=rnd, question=k, reason=why, status="pending",
                            before=dict(question=qs.get(k), solution=sols.get(k), profile=profile(qs.get(k), sols.get(k))),
                            after=None)
                    for k, why in sorted(issues.items())}
            history += recs.values()
            listing = "\n".join(f"- Question {k}: {why}" + (f" (correct answer must be {key[k]})" if k in key else "")
                                for k, why in sorted(issues.items()))
            keep = "\n\n".join(b for k, b in sorted(qs.items()) if k not in issues)
            rprompt = fill(revise_t, issues=listing, keep=keep, section_rules=sec_rules, draws=draws_fn(rng))
            try:
                rq, rs = split_reply(timed("rewrite", sid, rnd, pdfs, rprompt,
                                           texts + [in_paper, "SEEN BEFORE (do not repeat):\n\n"
                                                    + "\n\n".join(b for _, b in seen)[:30000]]))
            except ValueError:
                log(f"    {label}: round {rnd}: the rewrite had no solutions separator; nothing changed")
                for r in recs.values():
                    r["status"] = "reply unusable (no solutions separator)"
                continue
            nq, ns = blocks(rq), blocks(rs)
            took = [k for k in issues if k in nq and k in ns]      # a question only together with its solution
            for k in took:
                qs[k], sols[k] = nq[k], ns[k]
                recs[k].update(status="replaced", after=dict(question=nq[k], solution=ns[k], profile=profile(nq[k], ns[k])))
            partial = sorted(set(issues) - set(took))
            for k in partial:
                recs[k]["status"] = "incomplete rewrite (kept the old version)"
            if partial:
                log(f"    {label}: round {rnd}: no complete rewrite (question and solution) for "
                    + ", ".join(f"Q{k}" for k in partial) + "; kept as they were")
        done[sid] = dict(sec=s, q=q_md, qs=qs, sols=sols, key=key)
        prov.update({str(k): v for k, v in provenance(sid, qs, history).items()})
        save()
    all_q = {k: b for d in done.values() for k, b in d["qs"].items()}
    all_s = {k: b for d in done.values() for k, b in d["sols"].items()}
    cap = lecture_cap(secs)
    heavy = {lab: f for lab, f in coverage(all_q, all_s).items() if f > cap + 1e-9}
    if heavy:
        unresolved.append("coverage: " + ", ".join(f"{lab} {f:.0%} of the marks" for lab, f in sorted(heavy.items()))
                          + f" (aim: at most {cap:.0%} per lecture)")
    return dict(sections=[done[s.get("id", "")] for s in secs], unresolved=unresolved,
                key="".join(L for d in done.values() for _, L in sorted(d["key"].items())),
                history=history, calls=calls, provenance={int(k): v for k, v in prov.items()})


def assemble(result: dict) -> tuple[str, str]:
    """Paper and solutions in question order, with section headings."""
    q_parts, s_parts = [], []
    for d in result["sections"]:
        h = heading(d["q"])
        q_parts.append((h + "\n\n" if h else "") + "\n\n".join(b for _, b in sorted(d["qs"].items())))
        s_parts.append("\n\n".join(b for _, b in sorted(d["sols"].items())))
    return "\n\n".join(q_parts), "\n\n".join(s_parts)
