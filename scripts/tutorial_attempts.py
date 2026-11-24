"""Which tutorial questions you have attempted, from your own handwritten working. Zero AI calls; link_tutorials.py
runs it (so refresh.ps1 and nightly_ai.ps1 do).

Detection, per page of your tutorial working (pages come from link_tutorials.notes_pages):
  1. a label: "Question 3" / "Q2" / "Problem 4" in the page heading, or "Q2", "1.)", "2)" or a circled 2 at the start of
     one of your lines;
  2. otherwise the page continues the question of the page before it, unless its words clearly match another
     question on that tutorial's sheet (distinctive words, weighted by how rare they are across all sheets);
  3. the first page of a tutorial with no evidence is Q1;
  4. a page filed under a tutorial only by inference ("(follows)") that matches another tutorial's question far
     better is moved there, with the weak pages after it.
Scans of your working that have no transcription yet (`sources.tutorial_work`, e.g. "tut_5_andnotes.pdf") count as
"scan on file" for their tutorial: which questions they cover isn't known until extract_handwriting.py reads them.

practice/tutorial_attempts.md is YOURS: a checklist, one line per question. The pipeline adds tutorials and questions
it hasn't listed, ticks a question the first time it finds your working for it, and refreshes the evidence after the
" · ". It never unticks a line, never ticks one you left unticked while its evidence was already shown, and keeps every
other line you write. Tick or untick freely: the tutorial index (practice/tutorials.md) reports your ticks.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from common import TZ, VAULT, no_emoji, unit_path

QSTART = re.compile(r"(?m)^\s*(\d{1,2})\.\s+\S")
TITLE_LABEL = re.compile(r"(?i)\b(?:question|q|problem)\s*\.?\s*(\d{1,2})\b")
LINE_LABEL = [re.compile(r"(?im)^>\s*(?:q(?:uestion)?|problem)\s*\.?\s*(\d{1,2})\b"),
              re.compile(r"(?m)^>\s*(\d{1,2})\s*\.?\)")]
CIRCLED = {c: i for i, c in enumerate("①②③④⑤⑥⑦⑧⑨", 1)}
STOP = set("""that this with from then there their which where when what will have been into such also than them they
your each only must should would could about over under these those using used find determine show given consider
following figure values value system systems model control controller closed loop transfer function""".split())
STRONG, MARGIN, MOVE, MOVE_RATIO = 10.0, 1.5, 15.0, 2.0
PAGE_SECTION = re.compile(r"(?ms)^### p\. (\d+) · .*?(?=^### p\. |\Z)")
LINE = re.compile(r"^- \[( |x|X)\] Q(\d+)\b(?: · (.*))?$")
EVIDENCE = ("your working", "no working found", "scan on file")


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


def sheet_questions(text: str) -> dict[int, str]:
    """{question number: its text}, from a sheet's text: top-level "1. ", "2. " ... in order (equation numbers and
    sub-parts don't start a line like that in sequence)."""
    starts = []
    for m in QSTART.finditer(text):
        if int(m.group(1)) == len(starts) + 1:
            starts.append(m.start())
    return {i + 1: text[s:(starts[i + 1] if i + 1 < len(starts) else len(text))] for i, s in enumerate(starts)}


def labels(title: str, body: str, nq: int) -> list[int]:
    found = {int(g) for g in TITLE_LABEL.findall(title)}
    for rx in LINE_LABEL:
        found |= {int(g) for g in rx.findall(body)}
    found |= {d for c, d in CIRCLED.items() if re.search(rf"(?m)^>\s*{c}", body)}
    return sorted(q for q in found if 1 <= q <= nq) if nq else sorted(found)


def page_texts(pages: dict[int, list[tuple]]) -> dict[tuple[str, int], str]:
    out, cache = {}, {}
    for items in pages.values():
        for tr, _pdf, page, _title, _inf in items:
            if tr not in cache:
                cache[tr] = {int(m.group(1)): m.group(0) for m in PAGE_SECTION.finditer(tr.read_text("utf-8"))}
            out[(tr.name, page)] = cache[tr].get(page, "")
    return out


def assign(pages: dict[int, list[tuple]], questions: dict[int, dict[int, str]]) -> tuple[dict, dict]:
    """(pages regrouped by tutorial, {(transcription name, page): (tutorial, [questions], how)})."""
    qtok = {(t, q): tokens(text) for t, qs in questions.items() for q, text in qs.items()}
    df = Counter(w for ws in qtok.values() for w in ws)
    idf = {w: math.log(len(qtok) / df[w]) for w in df} if qtok else {}
    score = lambda ws, key: sum(idf[w] for w in ws & qtok[key])           # noqa: E731
    texts = page_texts(pages)
    regrouped: dict[int, list[tuple]] = {}
    qmap: dict[tuple[str, int], tuple[int, list[int], str]] = {}
    for n in sorted(pages):
        nq = len(questions.get(n, {}))
        cur, carry = None, None                # carry: (tutorial, question) a moved run of pages belongs to
        for item in pages[n]:
            tr, _pdf, page, title, inferred = item
            body = texts.get((tr.name, page), title)
            ws = tokens(body)
            own = sorted(((score(ws, (n, q)), q) for q in questions.get(n, {})), reverse=True)
            best_own = own[0][0] if own else 0.0
            lab = labels(title, body, nq)
            other = max(((score(ws, k), k) for k in qtok if k[0] != n), default=(0.0, None))
            if inferred and not lab and other[1] and other[0] >= MOVE and other[0] >= MOVE_RATIO * best_own:
                carry = other[1]
                how = f"matches Tutorial {carry[0]} Q{carry[1]} by its words (filed under Tutorial {n})"
            elif inferred and not lab and carry and best_own < STRONG:
                how = f"continues the Tutorial {carry[0]} pages before it"
            else:
                carry = None
            if carry:
                regrouped.setdefault(carry[0], []).append(item)
                qmap[(tr.name, page)] = (carry[0], [carry[1]], how)
                continue
            if lab:
                qs, how = lab, "labelled"
            elif own and best_own >= STRONG and (len(own) < 2 or best_own >= MARGIN * own[1][0]) and own[0][1] != cur:
                qs, how = [own[0][1]], "matched by its words"
            elif cur:
                qs, how = [cur], "continues the page before"
            elif nq:
                qs, how = [1], "first page of the tutorial"
            else:
                qs, how = [], "question not identified"
            cur = qs[-1] if qs else cur
            regrouped.setdefault(n, []).append(item)
            qmap[(tr.name, page)] = (n, qs, how)
    return regrouped, qmap


def evidence(u: dict, questions: dict[int, dict[int, str]], pages: dict[int, list[tuple]], qmap: dict,
             copies: dict[Path, Path], scans: dict[int, list[Path]]) -> dict[tuple[int, int], str]:
    """{(tutorial, question): evidence text} for every question on a sheet, plus questions only your notes name."""
    vrel = lambda p: p.relative_to(VAULT).as_posix()                       # noqa: E731
    found: dict[tuple[int, int], list[str]] = {}
    for _n, items in pages.items():
        for tr, pdf, page, _title, _inf in items:
            t, qs, _how = qmap[(tr.name, page)]
            link = f"[[{vrel(copies[pdf])}#page={page}|p. {page}]]" if pdf in copies else f"p. {page}"
            for q in qs:
                found.setdefault((t, q), []).append(link)
    keys = {(t, q) for t, qs in questions.items() for q in qs} | set(found)
    out = {}
    for t, q in sorted(keys):
        if found.get((t, q)):
            out[(t, q)] = "your working " + ", ".join(dict.fromkeys(found[(t, q)]))
        elif scans.get(t):
            out[(t, q)] = "scan on file (" + ", ".join(p.name for p in scans[t]) + "): which questions it covers is " \
                                                                                    "unknown until it is transcribed"
        else:
            out[(t, q)] = "no working found"
    return out


def update_checklist(u: dict, ev: dict[tuple[int, int], str]) -> tuple[dict[tuple[int, int], bool], str]:
    """Merge the detected evidence into practice/tutorial_attempts.md (yours) and return your ticks."""
    path = unit_path(u, "tutorial_attempts")
    old = path.read_text("utf-8") if path.exists() else ""
    lines = old.splitlines()
    if not lines:
        lines = ["---", f"unit: {u['code']}", "type: tutorial attempts", "source: yours",
                 f"created: {datetime.now(TZ):%Y-%m-%d}", "---", "", f"# {u['code']} tutorial attempts", "",
                 "Yours: tick each tutorial question you have attempted. `link_tutorials.py` adds questions it hasn't "
                 "listed, ticks a question the first time it finds your working for it, and refreshes the evidence "
                 "after the ·. It never unticks a line or changes your ticks, and keeps anything else you write here. "
                 "The summary is in [[" + unit_path(u, "practice").joinpath("tutorials").relative_to(VAULT).as_posix()
                 + "|the tutorial index]].", ""]
    section, where = None, {}
    for i, ln in enumerate(lines):
        h = re.match(r"^## Tutorial (\d+)\s*$", ln)
        if h:
            section = int(h.group(1))
            where.setdefault(section, {"head": i})
            continue
        m = LINE.match(ln)
        if m and section is not None:
            where[section][int(m.group(2))] = i
    ticks: dict[tuple[int, int], bool] = {}
    for (t, q), text in sorted(ev.items()):
        has = text.startswith("your working")
        if t in where and q in where[t]:
            i = where[t][q]
            m = LINE.match(lines[i])
            box, rest = m.group(1).lower() == "x", m.group(3) or ""
            if not box and has and rest.startswith(("no working found", "scan on file")):
                box = True                                 # new working since the last run
            if rest == "" or rest.startswith(EVIDENCE):
                rest = text                                # refresh only the pipeline's own evidence text
            lines[i] = f"- [{'x' if box else ' '}] Q{q} · {rest}"
            ticks[(t, q)] = box
            continue
        if t not in where:                                 # a new tutorial: its heading goes in number order
            later = [s for s in where if s > t]
            at = where[min(later)]["head"] if later else len(lines)
            block = [f"## Tutorial {t}", ""]
            lines[at:at] = block
            where = shift(where, at, len(block))
            where[t] = {"head": at}
        last = max([v for k, v in where[t].items() if k != "head"] or [where[t]["head"]])
        at = last + 1
        lines.insert(at, f"- [{'x' if has else ' '}] Q{q} · {text}")
        where = shift(where, at, 1)
        where[t][q] = at
        ticks[(t, q)] = has
    new = no_emoji("\n".join(lines).rstrip() + "\n")
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, "utf-8")
        return ticks, "updated" if old else "created"
    return ticks, "unchanged"


def shift(where: dict, at: int, by: int) -> dict:
    """Line numbers after an insertion at `at`."""
    return {t: {k: (v + by if v >= at else v) for k, v in d.items()} for t, d in where.items()}


def summary(ticks: dict[tuple[int, int], bool], ev: dict[tuple[int, int], str]) -> list[str]:
    """The table at the top of the tutorial index, from your ticks."""
    tuts = sorted({t for t, _ in ticks})
    rows = ["| Tutorial | Attempted | Not yet | Status |", "|---|---|---|---|"]
    done_q = sum(ticks.values())
    full = 0
    for t in tuts:
        qs = sorted(q for tt, q in ticks if tt == t)
        yes = [q for q in qs if ticks[(t, q)]]
        no = [q for q in qs if not ticks[(t, q)]]
        scan = any(ev[(t, q)].startswith("scan on file") for q in no)
        status = ("attempted" if not no else "partly" if yes else "scan on file, not transcribed" if scan
                  else "not attempted")
        full += not no
        rows.append(f"| {t} | {', '.join(f'Q{q}' for q in yes) or '-'} | {', '.join(f'Q{q}' for q in no) or '-'} "
                    f"| {status} |")
    return [f"**{full} of {len(tuts)} tutorials fully attempted; {done_q} of {len(ticks)} questions.** From your ticks "
            "in the attempts checklist (the pipeline ticks what it finds in your notes; correct it there).", ""] + rows
