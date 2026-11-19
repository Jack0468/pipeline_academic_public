"""Generate active-recall material (recall prompts, exam-style problems, notes-sheet candidates,
traps) for every lecture, one Gemini call per lecture PDF.

Output: <vault>/units/<unit>/recall/<CODE>_<label>.md  (frontmatter source: ai, rank: 6)
Skips lectures whose PDF hash is unchanged since the last run (state/manifests/manifest_recall.json).

Usage:
    python scripts/gen_recall.py                      # all units, in exam order
    python scripts/gen_recall.py --unit ABCD1001 --limit 1
    python scripts/gen_recall.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from common import ALLOW_SAT_HELP, work_units
from common import PROMPTS, TZ, Manifest, fill, file_hash, file_uri, find_sources, lecture_labels, load_units, unit_path
import gemini

_lock = threading.Lock()


_stop = threading.Event()   # set when the daily quota runs out: queued jobs skip instead of failing


def job(u, pdf, label, manifest, template):
    if _stop.is_set():
        return None
    out = unit_path(u, "recall") / f"{u['code']}_{label}.md"
    prompt = fill(template, unit_code=u["code"], unit_name=u["name"], label=label,
                  conditions=u["exam"]["conditions"])
    try:
        body = gemini.pdf_to_markdown(pdf, prompt)
    except gemini.DailyQuotaExhausted:
        _stop.set()
        raise
    if not body.strip():
        raise RuntimeError("empty response")
    front = "\n".join([
        "---", f"unit: {u['code']}", "type: recall", f"lecture: {label}", "source: ai", "rank: 6",
        f"model: {gemini.used_model()}", f"generated: {datetime.now(TZ):%Y-%m-%d}",
        f"slides: \"{file_uri(pdf)}\"", "---", "",
        "> [!check] AI-generated from the slides. Answer before expanding; if your answer disagrees, "
        f"check the slide page, not this file. [Open slides]({file_uri(pdf)})", "", ""])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(front + body + "\n", "utf-8")
    with _lock:
        manifest.mark(f"{u['code']}:{pdf.name}", file_hash(pdf), out=str(out), model=gemini.used_model())
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*", help="unit codes, e.g. ABCD1001 ABCD1002")
    ap.add_argument("--limit", type=int, help="max lectures to process this run")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--force", action="store_true", help="regenerate even if unchanged")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    template = (PROMPTS / "recall_questions.md").read_text("utf-8")
    manifest = Manifest("recall")
    todo = []
    for u in work_units(args.unit, args.allow_sat):
        lectures = find_sources(u, "lectures")
        labels = lecture_labels(lectures)
        for pdf in lectures:
            out = unit_path(u, "recall") / f"{u['code']}_{labels[pdf]}.md"
            if not args.force and out.exists() and manifest.done(f"{u['code']}:{pdf.name}", file_hash(pdf)):
                continue
            todo.append((u, pdf, labels[pdf]))
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} lecture(s) to process with {' → '.join(gemini.models())}")
    if args.dry_run:
        for u, pdf, label in todo:
            print(f"  {u['code']} {label:5} {pdf.name}")
        return

    ok = fail = skipped = 0
    quota_msg = None
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(job, u, pdf, label, manifest, template): (u["code"], label) for u, pdf, label in todo}
        for f in as_completed(futs):
            code, label = futs[f]
            try:
                res = f.result()
                if res is None:
                    skipped += 1
                    continue
                print(f"  ok {code} {label} → {res.name}", flush=True)
                ok += 1
            except gemini.DailyQuotaExhausted as e:
                quota_msg, skipped = str(e), skipped + 1
            except Exception as e:
                print(f"  FAIL {code} {label}: {str(e)[:200]}", flush=True)
                fail += 1
    print(f"done: {ok} ok, {fail} failed, {skipped} left for the next run")
    if quota_msg:
        print(f"STOPPED: {quota_msg}. Re-run then; finished lectures are skipped.")


if __name__ == "__main__":
    main()
