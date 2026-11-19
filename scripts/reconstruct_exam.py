"""Reconstruct a real exam from your post-exam recall notes, with worked and checked solutions.

Straight after an exam, write down what you remember in the unit's vault folder:
    units/<unit id>/EXAM_<year>/<anything with "recall" in the name>.md
(questions, the numbers you remember, anything about the format). This script turns those notes into the
paper in the exam's real format plus worked solutions, the same way mock exams are made (one agy call to
write, a second one to re-solve and check the solutions). It works for any unit with a config in
config/units/: the format, marks, pace and lecture slides come from there.

Rules the reconstruction follows (prompts/exam_reconstruct.md):
- only the questions in your notes, with every recalled value kept exactly;
- anything it had to add is tagged [inferred]; ambiguous questions are solved for both readings;
- marks per part add up to the real section totals.

Output, next to the recall file:
    exam_<year>_reconstructed.md            the paper
    exam_<year>_reconstructed_solutions.md  worked solutions, marking scheme, assumptions
Both are marked `share: never`: they reproduce real exam content, so they are for your own revision only
(share_vault.py never exports EXAM_* folders). Re-running skips recall files that haven't changed since the
last reconstruction (state/manifests/manifest_exam_reconstructions.json); --force redoes them.

Usage:
    python scripts/reconstruct_exam.py --unit ABCD1001                 # every EXAM_*/ recall file of the unit
    python scripts/reconstruct_exam.py --unit ABCD1001 --recall "units/.../EXAM_2026/notes recall.md"
    python scripts/reconstruct_exam.py --dry-run                       # list what would be done, all units
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import gemini
import mock_writer
from common import unit_path, PROMPTS, STATE, TZ, Manifest, VAULT, file_hash, fill, find_sources, load_units, scope_files, vault_unit_dir
from gen_practice_exam import SPLIT, strip_answer_leaks, with_retries
from pace_recall import pace

DEFAULT_GLOB = "EXAM_*/*recall*.md"


def recall_files(u: dict, explicit: str | None) -> list[Path]:
    if explicit:
        p = Path(explicit)
        return [p if p.is_absolute() else VAULT / p]
    pattern = (u.get("exam") or {}).get("recall_glob") or DEFAULT_GLOB
    return sorted(p for p in vault_unit_dir(u).glob(pattern)
                  if "reconstructed" not in p.stem and p.is_file())


def year_of(p: Path) -> str:
    m = re.search(r"(20\d\d)", p.parent.name) or re.search(r"(20\d\d)", p.stem)
    return m.group(1) if m else f"{datetime.now(TZ):%Y}"


def section_b_marks(u: dict) -> str:
    fmt = " ".join(str(u["exam"].get("format") or "").split())
    m = re.search(r"Section B[^.]*?(\d+)\s*marks", fmt, re.I)
    return m.group(1) if m else "the remaining"


def section_scope(which: str, b_marks: str) -> tuple[str, str]:
    """(scope rule, paper layout) for the prompt: both sections, or only A or only B."""
    a_head = "a `# Section A: Multiple choice (recalled questions)` heading with its questions"
    b_head = f"`# Section B: Extended answer ({b_marks} marks)` with its questions"
    if which == "A":
        return ("reconstruct only the multiple-choice questions (Section A); ignore everything else in the notes.",
                a_head + ".")
    if which == "B":
        return ("reconstruct only the extended-answer questions (Section B); leave out the multiple-choice "
                "questions completely, in the paper and in the solutions.", b_head + ".")
    return "reconstruct both sections.", f"{a_head}, then {b_head}."


def context_texts(u: dict) -> list[str]:
    texts = []
    hb = unit_path(u, "handbook")
    if hb.exists():                 # the student's formula conventions, so solutions match their notes
        texts.append("HANDBOOK (the student's formula sheet: use its conventions and symbols):\n\n"
                     + hb.read_text("utf-8")[:60000])
    outline = unit_path(u, "outline")
    if outline.exists():
        texts.append("UNIT OUTLINE (official):\n\n" + outline.read_text("utf-8"))
    return texts


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--recall", help="one recall file (absolute, or relative to the vault)")
    ap.add_argument("--sections", choices=["A", "B", "AB"], default="AB",
                    help="which sections to reconstruct (B = extended answer only); the output name says which")
    ap.add_argument("--force", action="store_true", help="redo even if the recall file hasn't changed")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    template = (PROMPTS / "exam_reconstruct.md").read_text("utf-8")
    verify_t = (PROMPTS / "practice_exam_verify.md").read_text("utf-8")
    manifest = Manifest("exam_reconstructions")
    for u in load_units(args.unit):
        for rec in recall_files(u, args.recall):
            year = year_of(rec)
            tag = "" if args.sections == "AB" else f"_section_{args.sections.lower()}"
            q_out = rec.parent / f"exam_{year}_reconstructed{tag}.md"
            s_out = rec.parent / f"exam_{year}_reconstructed{tag}_solutions.md"
            key, h = f"{u['code']}:{rec.relative_to(VAULT).as_posix()}:{args.sections}", file_hash(rec)
            if not args.force and q_out.exists() and manifest.done(key, h):
                print(f"  {u['code']}: {rec.name}: unchanged since the last reconstruction (use --force to redo)")
                continue
            lectures = find_sources(u, "lectures")
            scope = scope_files(u)
            print(f"  {u['code']}: reconstructing the {year} exam from {rec.name} "
                  f"({len(lectures)} lectures, {len(scope)} scope pages)", flush=True)
            if args.dry_run:
                continue
            ex = u["exam"]
            sections_rule, paper_layout = section_scope(args.sections, section_b_marks(u))
            prompt = fill(template, year=year, unit_code=u["code"], unit_name=u["name"],
                          sections_rule=sections_rule, paper_layout=paper_layout,
                          recall=rec.read_text("utf-8", errors="replace").strip(),
                          reading_min=ex.get("reading_min", 10), writing_min=ex.get("writing_min", 120),
                          pace=f"{ex.get('total_marks', 100)} marks total, {pace(u)[1]}",
                          conditions=ex.get("conditions", ""), materials="; ".join(ex.get("materials") or []),
                          format=(ex.get("format") or "").strip() or "(not recorded: infer it from the notes)",
                          section_b_marks=section_b_marks(u))
            raw_dir = STATE / "exam_reconstructions" / u["id"]
            raw_dir.mkdir(parents=True, exist_ok=True)
            questions = solutions = None
            try:
                for attempt in (1, 2):                 # a second try if the reply can't be split
                    reply = with_retries(gemini.generate, scope + lectures, prompt, context_texts(u))
                    raw = raw_dir / f"exam_{year}_reply_{datetime.now(TZ):%Y%m%d-%H%M%S}.md"
                    raw.write_text(reply, "utf-8")      # kept, so a failed reply can be inspected or salvaged
                    try:
                        questions, solutions = mock_writer.split_reply(reply)
                        break
                    except ValueError:
                        print(f"  attempt {attempt}: no solutions separator in the reply (saved as "
                              f"{raw.relative_to(STATE.parent)})" + ("; asking again" if attempt == 1 else ""), flush=True)
            except gemini.DailyQuotaExhausted as e:
                print(f"STOPPED: {e}")
                return
            except RuntimeError as e:
                print(f"  FAIL {u['code']}: {str(e)[:300]}; nothing saved")
                continue
            if questions is None:
                print(f"  FAIL {u['code']}: no usable reply after 2 attempts; the raw replies are in "
                      f"{raw_dir.relative_to(STATE.parent)}")
                continue
            questions, leaks = strip_answer_leaks(questions)
            if leaks:
                print(f"  WARN removed {leaks} answer line(s) from the paper", flush=True)
            checked = "unchecked"
            if not args.no_verify:
                print("  checking the solutions…", flush=True)
                try:
                    fixed = with_retries(gemini.generate, [], fill(verify_t, unit_code=u["code"], unit_name=u["name"]),
                                         texts=["QUESTIONS (the reconstructed exam paper):\n\n" + questions,
                                                "SOLUTIONS TO CHECK:\n\n" + solutions], temperature=0.1).strip()
                    if fixed.startswith("#") and set(mock_writer.blocks(solutions)) <= set(mock_writer.blocks(fixed)):
                        solutions, checked = fixed, "checked by a second Gemini pass"
                    else:
                        print("  WARN the solution check's reply was incomplete; kept the unchecked solutions")
                        checked = "unchecked (the check's reply was incomplete)"
                except (gemini.DailyQuotaExhausted, RuntimeError) as e:
                    print(f"  (solutions left unchecked: {str(e)[:150]})")
            front = lambda kind: "\n".join(["---", f"unit: {u['code']}", f"type: {kind}", f"year: {year}",
                                            "source: recall + ai", "rank: 6", "share: never",
                                            f"recall: \"{rec.name}\"", f"model: {gemini.used_model()}",
                                            f"generated: {datetime.now(TZ):%Y-%m-%d}", "---", ""])
            warn = ("> [!warning] Reconstructed from memory after the exam. Wording, marks and every value tagged "
                    "[inferred] are best guesses. Real exam content: for your own revision only, never share or post it.\n\n")
            q_out.write_text(front("exam reconstruction") + "\n" + warn
                             + f"# {u['code']} {year} final exam (reconstructed)\n\n> [!info] {ex.get('reading_min', 10)} min "
                             f"reading + {ex.get('writing_min', 120)} min writing. {ex.get('conditions', '')}. "
                             f"{ex.get('total_marks', 100)} marks; {pace(u)[1]}.\n\n" + questions + "\n", "utf-8")
            s_out.write_text(front("exam reconstruction solutions") + "\n" + warn
                             + f"> [!check] AI-written solutions, {checked}. Where your answer disagrees, check the cited "
                             "slide before trusting either.\n\n"
                             + f"# {u['code']} {year} final exam (reconstructed): solutions\n\n" + solutions + "\n", "utf-8")
            manifest.mark(key, h, out=str(q_out))
            n_inf = len(re.findall(r"\[inferred\]", questions))
            print(f"  ok {q_out.name} + {s_out.name}: {len(mock_writer.blocks(questions))} question(s), "
                  f"{n_inf} inferred value(s); solutions {checked}", flush=True)


if __name__ == "__main__":
    main()
