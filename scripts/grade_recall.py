"""Check your handwritten recall attempts (blurts) against the slides and the recall file.

You answer a lecture's free-recall prompts from memory on paper, numbered like the prompts, and save the
scan as `<unit OneDrive folder>/summary_recall/recall_L<n>.pdf`. Extra attempts are fine:
recall_L1_2.pdf, recall_L1_2026-11-20.pdf. The glob is `sources.recall_attempts` in the unit config.
For each new or changed attempt, one AI call (attempt PDF + that lecture's slides + its recall file):

  - <vault>/units/<id>/recall_checks/<stem>.md   verdict per prompt (correct partial wrong not attempted) with what you wrote,
    the check against the slides (page refs), the fix, misconceptions, and tonight's re-test list
  - practice/error_log.md                         wrong and partial items appended once each
  - 00_dashboard/progress.md                      the lecture is ticked if score ≥ `recall_pass` (0.6)
  - 00_dashboard/recall_checks.md                 every check, with scores (regenerated)

score = (correct + ½ partial) / total prompts

Usage:
    python scripts/grade_recall.py [--unit ABCD1001] [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import gemini
import progress
from common import ALLOW_SAT_HELP, work_units
from common import (CONFIG, PROMPTS, STATE, TZ, VAULT, Manifest, file_hash, file_uri, fill, find_sources,
                    lecture_labels, load_units, load_yaml, unit_path)

DEFAULT_GLOB = "summary_recall/recall_*.pdf"


def attempt_label(p: Path, labels: set[str]) -> str | None:
    """recall_L1.pdf → L1; recall_L6a_2.pdf → L6a; picks the longest matching lecture label."""
    m = re.match(r"recall_(.+)$", p.stem, re.I)
    if not m:
        return None
    rest = m.group(1)
    hits = [lab for lab in labels if re.match(rf"{re.escape(lab)}(?![0-9a-z])", rest, re.I)]
    return max(hits, key=len) if hits else None


def parse_result(text: str) -> tuple[str, dict]:
    m = re.search(r"```json\s*(\{.*?\})\s*```\s*$", text, re.S)
    if not m:
        return text, {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return text, {}
    return text[:m.start()].rstrip(), data


def score(d: dict) -> float | None:
    """(correct + ½ partial) / prompts not deferred to the notes sheet (open-book strategy isn't a miss)."""
    t = (d.get("total") or 0) - d.get("deferred", 0)
    return (d.get("correct", 0) + 0.5 * d.get("partial", 0)) / t if t > 0 else None


def append_notes_sheet(u, stem: str, label: str, d: dict) -> int:
    """Deferred items → a to-do list on the unit's notes sheet (append-only, once each)."""
    sheet = unit_path(u, "notes_sheet")
    if not sheet.exists():
        return 0
    text = "\n".join(l for l in sheet.read_text("utf-8").splitlines()     # re-check replaces old items,
                     if f"<!-- recall:{stem}:" not in l or l.startswith("- [x]")) + "\n"   # but keeps ones you ticked
    new = [f"- [ ] **{label} Q{it.get('n')}**: {it.get('fix', '').strip()} <!-- recall:{stem}:{it.get('n')} -->"
           for it in d.get("items", []) if it.get("verdict") == "deferred" and f"recall:{stem}:{it.get('n')}" not in text]
    if new:
        head = "" if "## To add (from recall checks)" in text else "\n\n## To add (from recall checks)\n"
        sheet.write_text(text.rstrip() + head + "\n" + "\n".join(new) + "\n", "utf-8")
    return len(new)


def append_errors(u, stem: str, label: str, d: dict, when: str) -> int:
    log = unit_path(u, "error_log")
    if not log.exists():
        return 0
    # re-checking an attempt replaces its earlier generated lines (they may hold superseded verdicts)
    text = "\n".join(l for l in log.read_text("utf-8").splitlines() if f"<!-- recall:{stem}:" not in l) + "\n"
    new = [f"- {when} · **{label} recall Q{it.get('n')}** ({it.get('verdict')}): {it.get('fix', '').strip()} "
           f"<!-- recall:{stem}:{it.get('n')} -->"
           for it in d.get("items", []) if it.get("verdict") in ("wrong", "partial")
           and f"recall:{stem}:{it.get('n')}" not in text]
    if new:
        head = "" if "## From recall checks" in text else "\n\n## From recall checks (appended by grade_recall.py)\n"
        log.write_text(text.rstrip() + head + "\n" + "\n".join(new) + "\n", "utf-8")
    return len(new)


def tick(u, label: str) -> bool:
    if not progress.PAGE.exists():
        return False
    text = progress.PAGE.read_text("utf-8")
    m = re.search(rf"^## {u['code']}\b.*$", text, re.M)
    if not m:
        return False
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    sec = text[m.end():end]
    new = re.sub(rf"^(\s*[-*]\s+)\[ \](\s+{re.escape(label)}(?:\s+·|\s*$))", r"\1[x]\2", sec, count=1, flags=re.M)
    if new == sec:
        return False
    progress.PAGE.write_text(text[:m.end()] + new + text[end:], "utf-8")
    return True


def dashboard() -> None:
    rows = []
    for u in load_units():
        for p in sorted(unit_path(u, "recall_checks").glob("*.md")):
            fm = dict(re.findall(r"^(\w+): (.*)$", p.read_text("utf-8").split("---", 2)[1], re.M)) if p.read_text("utf-8").startswith("---") else {}
            rows.append((u["code"], fm.get("lecture", "?"), fm.get("attempt_date", ""), fm.get("score", ""),
                         fm.get("tally", ""), f"[[units/{u['id']}/recall_checks/{p.stem}|{p.stem}]]"))
    L = ["---", "source: generated", "generator: pipeline_academic/scripts/grade_recall.py", "---", "",
         "# Recall checks", "", "> [!tip] Scan a from-memory attempt to `summary_recall/recall_L<n>.pdf` in the unit's "
         "OneDrive folder, then run `scripts\\nightly_ai.ps1` (or `grade_recall.py`). Redo each check's "
         "*Re-test tonight* items in the evening review.", "",
         "| Unit | Lecture | Attempt | Score | correct/partial/wrong/not attempted/deferred | Check |", "|---|---|---|---|---|---|"]
    L += [f"| {a} | {b} | {c} | {d} | {e} | {f} |" for a, b, c, d, e, f in rows] or ["| – | – | – | – | – | – |"]
    (VAULT / "00_dashboard" / "recall_checks.md").write_text("\n".join(L) + "\n", "utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    template = (PROMPTS / "grade_recall.md").read_text("utf-8")
    passmark = float(load_yaml(CONFIG / "study_plan.yaml").get("recall_pass", 0.6))
    manifest = Manifest("recall_checks")
    for u in work_units(args.unit, args.allow_sat):
        if not (u.get("sources") or {}).get("recall_attempts"):
            u.setdefault("sources", {})["recall_attempts"] = [DEFAULT_GLOB]
        lectures = find_sources(u, "lectures")
        labels = lecture_labels(lectures)
        by_label = {lab: p for p, lab in labels.items()}
        for att in find_sources(u, "recall_attempts"):
            label = attempt_label(att, set(by_label))
            if not label:
                print(f"  ? {u['code']}: {att.name}: no lecture label in the name (expected recall_L<n>.pdf)")
                continue
            h, key = file_hash(att), f"{u['code']}:{att.name}"
            out = unit_path(u, "recall_checks") / f"{u['code']}_{att.stem}.md"
            if not args.force and out.exists() and manifest.done(key, h):
                continue
            recall_file = unit_path(u, "recall") / f"{u['code']}_{label}.md"
            print(f"  {u['code']} {label}: checking {att.name}" + ("" if recall_file.exists() else " (no recall file yet)"))
            if args.dry_run:
                continue
            prompt = fill(template, unit_code=u["code"], unit_name=u["name"], label=label)
            texts = [f"RECALL FILE for {label}:\n\n{recall_file.read_text('utf-8')}"] if recall_file.exists() else []
            try:
                body = gemini.generate([att, by_label[label]], prompt, texts=texts, temperature=0.1)
            except gemini.DailyQuotaExhausted as e:
                print(f"STOPPED: {e}")
                return
            md, data = parse_result(body)
            sc = score(data)
            when = datetime.fromtimestamp(att.stat().st_mtime, TZ).strftime("%Y-%m-%d")
            tally = (f"{data.get('correct', 0)}/{data.get('partial', 0)}/{data.get('wrong', 0)}/{data.get('missing', 0)}"
                     f"/{data.get('deferred', 0)}" if data else "")
            front = ["---", f"unit: {u['code']}", "type: recall check", f"lecture: {label}", "source: recall-check", f"attempt_date: {when}",
                     f"score: {'' if sc is None else f'{sc:.0%}'}", f"tally: {tally}",
                     f"model: {gemini.used_model()}", f"attempt: \"{file_uri(att)}\"",
                     f"slides: \"{file_uri(by_label[label])}\"", "---", "",
                     f"> [!check] AI check of your handwriting against the slides. If a verdict looks wrong, "
                     f"the slide page is the authority. [Your attempt]({file_uri(att)}) · "
                     f"[[units/{u['id']}/recall/{u['code']}_{label}|recall prompts]]", "", ""]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(front) + md + "\n", "utf-8")
            if not data:                       # no JSON summary: keep the text, but don't file it as done
                print(f"    WARN {out.name}: the reply has no readable JSON summary, so no score, error-log entries "
                      "or tick. Saved as is; it is checked again on the next run.")
                continue
            manifest.mark(key, h, out=str(out), score=sc)
            n_err = append_errors(u, att.stem, label, data, when)
            n_sheet = append_notes_sheet(u, att.stem, label, data)
            ticked = sc is not None and sc >= passmark and tick(u, label)
            print(f"    ok {out.name}: score {'?' if sc is None else f'{sc:.0%}'} ({tally} correct/partial/wrong/not attempted/deferred), "
                  f"{n_err} item(s) → error_log, {n_sheet} → notes_sheet"
                  + (f", ticked {label} in progress.md" if ticked else f" (not ticked: below {passmark:.0%})"))
    if not args.dry_run:
        dashboard()


if __name__ == "__main__":
    main()
