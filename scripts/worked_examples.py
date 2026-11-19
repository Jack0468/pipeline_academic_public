"""Worked examples by calculation type: one per type, for the exam-room binder. Any unit, only on request.

    python scripts/worked_examples.py --unit ABCD1002            # write + check (6 agy calls), then the PDF
    python scripts/worked_examples.py --unit ABCD1002 --pdf-only # re-print worked_examples.md after your edits

The types come from the unit's worked_examples_types.md (exam_room/ on the standard layout; a table: # | Type | Lectures | Why), which you or Claude
write and you may edit. Each type gets: how to recognise it (feeds the contents), the method with slide refs, an
exam-style worked example with every step, a reversed variant, and traps.

Context: the lecture slides and the course's own tutorial solutions (PDFs); the formula sheet(s) in
sources/*formulas*.md; the recall notes' formulas; exam_intel.md; and, so nothing is given away, the practised
problems and every mock paper the student hasn't been told to reuse.

Checking: a second pass re-solves every example and variant and corrects it (with a "Corrected:" note). That pass
is still AI, so spot-check the numbers before printing (CRC, checksums, subnets and RSA can be verified with code).

Output: worked_examples.md (share: never; front matter records the model, the context and the check) and the PDF next
to it, with a contents page listing each type and its page. An earlier set moves to _archive/. Raw replies: state/worked_examples/<id>/. Sat units are frozen (--allow-sat), and --unit is required.
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import gemini
import mock_writer
import pdf_tools
import unit_inputs
from common import ALLOW_SAT_HELP, PROMPTS, STATE, TZ, fill, intel_block, no_emoji, unit_path, work_units
from gen_practice_exam import _section, with_retries
from pace_recall import pace
from pdf_tools import math_problems

BATCH = 5


def types(u) -> list[tuple[int, str, str]]:
    p = unit_path(u, "worked_examples_types")
    if not p.exists():
        sys.exit(f"{u['code']}: write {p} first (a table: # | Type | Lectures | Why); Claude can draft it from the exam spec")
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", p.read_text("utf-8"), re.M)
    return [(int(n), t, lec) for n, t, lec in rows]


def context(u, inp) -> tuple[list[Path], list[str]]:
    sols = [p for p in inp.practice if re.search(r"(?i)solution", p.name)]
    pdfs = inp.lectures + sols
    texts = [f"FORMULA SHEET ({s.stem}):\n\n{s.read_text('utf-8')}" for s in sorted(unit_path(u, "sources").glob("*formulas*.md"))]
    notes = []
    for p in inp.recall:
        body = _section(p.read_text("utf-8"), "Notes-sheet candidates")
        if body:
            notes.append(f"### {p.stem}\n" + re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", body))
    if notes:
        texts.append("RECALL NOTES (formulas per lecture, with slide pages):\n\n" + "\n\n".join(notes))
    intel = intel_block(u, "mock")
    if intel:
        texts.append(intel)
    seen = mock_writer.practised(u)
    if seen:
        texts.append("ALREADY PRACTISED (never reuse):\n\n" + "\n\n".join(f"[{a}] {b.strip()}" for a, b in seen))
    papers = sorted(p for p in unit_path(u, "practice").glob("mock_exam_*.md") if re.fullmatch(r"mock_exam_\d+", p.stem))
    if papers:
        texts.append("MOCK PAPERS (the student hasn't necessarily sat them: never reuse their questions or numbers):\n\n"
                     + "\n\n".join(p.read_text("utf-8") for p in papers))
    return pdfs, texts


def headings(md: str) -> list[int]:
    return [int(n) for n in re.findall(r"^## WE(\d+)\.", md, re.M)]


def write(u, rows, pdfs, texts, raw_dir: Path, no_verify: bool) -> tuple[str, list[str]]:
    ex = u["exam"]
    base = dict(unit_code=u["code"], unit_name=u["name"], pace=f"{ex.get('total_marks', 100)} marks, {pace(u)[1]}",
                format=" ".join(str(ex.get("format") or "not published").split()), conditions=ex.get("conditions", ""),
                materials="; ".join(ex.get("materials") or []))
    t_write = (PROMPTS / "worked_examples.md").read_text("utf-8")
    t_check = (PROMPTS / "worked_examples_verify.md").read_text("utf-8")
    batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    stamp = f"{datetime.now(TZ):%Y%m%d-%H%M%S}"

    def one(k_b):
        k, b = k_b
        listing = "\n".join(f"- WE{n}: {t} ({lec})" for n, t, lec in b)
        reply = with_retries(gemini.generate, pdfs, fill(t_write, types=listing, **base), texts, temperature=0.3)
        (raw_dir / f"{stamp}_write_{k}.md").write_text(reply, "utf-8")
        want = [n for n, _, _ in b]
        notes = []
        missing = sorted(set(want) - set(headings(reply)))
        if missing:
            notes.append(f"batch {k}: WE{', WE'.join(map(str, missing))} missing from the reply")
        if no_verify:
            return reply, notes + [f"batch {k}: unchecked (--no-verify)"]
        fixed = with_retries(gemini.generate, [], fill(t_check, **base), ["WORKED EXAMPLES TO CHECK:\n\n" + reply],
                             temperature=0.1)
        (raw_dir / f"{stamp}_check_{k}.md").write_text(fixed, "utf-8")
        fixed = re.sub(r"(?m)^WORKED EXAMPLES TO CHECK:\s*\n+", "", fixed)     # the checker sometimes echoes its input label
        if set(headings(reply)) <= set(headings(fixed)):
            n_fix = len(re.findall(r"^> \[!check\] Corrected:", fixed, re.M))
            return fixed, notes + [f"batch {k}: checked, {n_fix} correction(s)"]
        return reply, notes + [f"batch {k}: the check's reply was incomplete; kept the unchecked text"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(one, enumerate(batches, 1)))
    body = "\n\n".join(no_emoji(r).strip() for r, _ in results)
    return body, [n for _, ns in results for n in ns]


def assemble(u, rows, body: str, notes: list[str], n_pdfs: int, texts: list[str]) -> str:
    taken: set[str] = set()
    ctx = [gemini.context_name(t, taken) for t in texts]
    contents = ["| WE | Type | Lectures |", "|---|---|---|"] + [f"| {n} | {t} | {lec} |" for n, t, lec in rows]
    front = ["---", f"unit: {u['code']}", "type: worked examples", "source: ai", "share: never",
             f"model: {gemini.used_model()}", f"generated: {datetime.now(TZ):%Y-%m-%d}", f"types: {unit_path(u, 'worked_examples_types').name}",
             f"context: [{', '.join(ctx)}]", f"pdfs: {n_pdfs}", f"check: \"{'; '.join(notes)}\"",
             "generator: pipeline_academic/scripts/worked_examples.py", "---", ""]
    head = [f"# {u['code']} worked examples by calculation type", "",
            "> [!warning] AI-written (see `model`) and checked by a second AI pass, so errors are still possible. "
            "Before relying on a number, check it against the cited slide. A wrong worked example is worse than "
            "none.", "", "## Contents", *contents, ""]
    return "\n".join(front + head) + "\n" + body.strip() + "\n"


def print_markdown(u, md_path: Path, flowing: bool = False) -> str:
    """worked_examples.md as print Markdown. The checker's "Corrected" notes stay in the .md (the record) and don't
    print. Normally each type starts a page; `flowing` (the combined handbook) lets them run on and labels each one."""
    md = md_path.read_text("utf-8")
    broken = math_problems(md)
    if broken:
        raise SystemExit(f"{u['code']}: fix the maths in {md_path.name} first:\n  " + "\n  ".join(broken[:20]))
    model = re.search(r"(?m)^model: (.*)$", md)
    md = pdf_tools.strip_title(pdf_tools.strip_front(md))
    md = md.replace("(see `model`)", f"({model.group(1)})" if model else "")
    md = re.sub(r"(?ms)^## Contents\n.*?(?=^## )", "", md)                          # pandoc's toc replaces it
    md = re.sub(r"(?m)^> \[!check\] Corrected:.*\n?", "", md)
    md = re.sub(r"(?m)^(?!\|)(.*\S.*)\n(?=\|)", r"\1\n\n", md)                      # a table needs a blank line above it
    md = re.sub(r"(?m)^---\s*$", "", md)                                            # section rules
    if flowing:
        md = re.sub(r"(?m)^## (WE(\d+)\..*?)\s*$", r"## \1 {#we\2}", md)
    else:
        md = re.sub(r"(?m)^(?=## WE\d+\.)", "\\\\newpage\n\n", md)
    return pdf_tools.callouts_to_bold(md).replace("µ", "$\\mu$")


def to_pdf(u, md_path: Path) -> Path:
    """A4 print: a contents page (each type and its page), then each type on a new page."""
    return pdf_tools.pandoc_pdf(print_markdown(u, md_path), unit_path(u, "worked_examples_pdf"),
                                f"{u['code']} worked examples by calculation type",
                                "markdown+hard_line_breaks+lists_without_preceding_blankline",   # steps and parts keep their lines
                                ["--toc", "--toc-depth=2", "-V", "toc-title=Calculation types"])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="+", required=True)
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--pdf-only", action="store_true", help="only re-print worked_examples.md (no AI)")
    a = ap.parse_args()
    for u in work_units(a.unit, a.allow_sat):
        md_path = unit_path(u, "worked_examples")
        if not a.pdf_only:
            rows = types(u)
            inp = unit_inputs.resolve(u)
            crit, _ = unit_inputs.problems(u, inp)
            if crit:
                print(f"  STOPPED {u['code']}: " + "; ".join(crit))
                continue
            pdfs, texts = context(u, inp)
            raw_dir = STATE / "worked_examples" / u["id"]
            raw_dir.mkdir(parents=True, exist_ok=True)
            print(f"  {u['code']}: {len(rows)} type(s) in {-(-len(rows) // BATCH)} batch(es); context {len(pdfs)} PDF(s) + "
                  f"{len(texts)} text block(s) (exam intel: {'yes' if any(t.startswith('EXAM INTEL') for t in texts) else 'NO'})",
                  flush=True)
            try:
                body, notes = write(u, rows, pdfs, texts, raw_dir, a.no_verify)
            except gemini.DailyQuotaExhausted as e:
                print(f"STOPPED: {e}")
                return
            except (RuntimeError, OSError) as e:
                print(f"  FAIL {u['code']}: {str(e)[:300]}; nothing saved (raw replies so far: {raw_dir})")
                continue
            if md_path.exists():                 # never overwrite: the earlier set goes to the archive
                archive = unit_path(u, "archive")
                archive.mkdir(exist_ok=True)
                md_path.rename(archive / f"worked_examples_{datetime.now(TZ):%Y-%m-%d_%H%M}_previous.md")
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(assemble(u, rows, body, notes, len(pdfs), texts), "utf-8")
            got = headings(body)
            print(f"  ok {md_path.name}: {len(got)}/{len(rows)} worked example(s); " + "; ".join(notes), flush=True)
        print(f"  ok {to_pdf(u, md_path)}")


if __name__ == "__main__":
    main()
