"""Batch-export OneNote sections to PDF (zero tokens), ready for extract_handwriting.py.

Each unit config names its notebook and the sections worth transcribing:

    onenote:
      notebook: 2026_SC2_ABCD1001
      sections: [Lecture, exam]
      scope_pages: ["exam/INFO"]     # single pages about what the final covers

PDFs land in state/onenote/<unit id>/<CODE>_<section>.pdf (sections, transcribed as handwriting) and
state/onenote/<unit id>/scope/ (scope pages, read directly by gen_practice_exam.py).

Attempt sections (`attempt_sections: [recall]`): your from-memory attempts, ONE PDF PER PAGE in
state/onenote/<unit id>/attempts/, named from the page title: "L 1" → recall_L1.pdf (grade_recall.py), "L 1 qs" →
L1_practice_1.pdf (grade_practice.py). Only pages OneNote says changed since their last export are published, so a
new attempt costs one small export and one marking call, never the whole section. A section is only re-exported when
OneNote says it changed after the last export (or with --force), so re-runs don't change file
hashes and don't trigger re-transcription.

Usage:
    python scripts/export_onenote.py [--unit ABCD1001] [--force] [--attempts-only]
    python scripts/export_onenote.py --list        # show every notebook and section name
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile

from common import ALLOW_SAT_HELP, work_units
from common import REPO, STATE, load_units

HELPER = REPO / "scripts" / "onenote_publish.ps1"


def safe(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name).strip("_")


def ps(*args: str) -> int:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPER), *args]
    return subprocess.run(cmd, encoding="utf-8", errors="replace").returncode


# A page in an attempt section, by its title: "L 1" (a free-recall blurt) → recall_L1.pdf for grade_recall.py;
# "L 1 qs" / "L1 qs 2" / "L 6a practice" (the recall file's exam-style problems) → L1_practice_1.pdf for grade_practice.py
ATTEMPT_TITLE = re.compile(r"(?i)^\s*L\s*(\d{1,2}[a-z]?)\s*(?:(qs?|questions?|practi[cs]e|problems?)\s*(\d+)?)?\s*$")


def attempt_name(title: str) -> str | None:
    m = ATTEMPT_TITLE.match(title)
    if not m:
        return None
    label = f"L{m.group(1).lower()}"
    return f"{label}_practice_{m.group(3) or 1}.pdf" if m.group(2) else f"recall_{label}.pdf"


def attempt_jobs(units: list[dict], force: bool) -> list[dict]:
    """One job per changed page of each `onenote.attempt_sections` section. OneNote's own per-page modified time
    decides what changed (a re-exported PDF's bytes and even its page size differ every time, so file hashes can't),
    so an unchanged page is never exported again and never re-marked."""
    from datetime import datetime, timezone
    asks = [{"notebook": u["onenote"]["notebook"], "section": s, "unit": u}
            for u in units for s in (u.get("onenote") or {}).get("attempt_sections") or []]
    if not asks:
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump([{"notebook": a["notebook"], "section": a["section"]} for a in asks], f)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPER), "-PageList", f.name]
    out = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace").stdout
    pages = [json.loads(ln) for ln in out.splitlines() if ln.strip().startswith("{")]
    jobs = []
    for a in asks:
        u, base = a["unit"], STATE / "onenote" / a["unit"]["id"] / "attempts"
        for p in (x for x in pages if x["notebook"] == a["notebook"] and x["section"] == a["section"]):
            name = attempt_name(p["name"])
            if not name:
                print(f"  ? {u['code']} {a['section']}/{p['name']}: not an attempt title; name it 'L 3' (free recall) "
                      "or 'L 3 qs' (the exam-style problems)")
                continue
            dst = base / name
            changed = datetime.fromisoformat(p["modified"].replace("Z", "+00:00"))
            if dst.exists() and not force and datetime.fromtimestamp(dst.stat().st_mtime, timezone.utc) > changed:
                continue
            settle = float((u.get("onenote") or {}).get("attempt_settle_minutes", 60))
            age = (datetime.now(timezone.utc) - changed).total_seconds() / 60
            if age < settle and not force:         # still being written: marking a half page records false misses
                print(f"  … {u['code']} {a['section']}/{p['name']}: changed {age:.0f} min ago; exported once it has "
                      f"been left for {settle:.0f} min (--force to export now)")
                continue
            jobs.append({"notebook": a["notebook"], "section": a["section"], "page_id": p["id"], "out": str(dst),
                         "force": True})
    return jobs


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--attempts-only", action="store_true",
                    help="only the changed pages of attempt sections (no section or scope exports)")
    args = ap.parse_args()
    if args.list:
        sys.exit(ps("-List"))

    jobs = []
    for u in ([] if args.attempts_only else work_units(args.unit, args.allow_sat)):
        on = u.get("onenote") or {}
        base = STATE / "onenote" / u["id"]
        for section in on.get("sections") or []:
            out = base / f"{u['code']}_{safe(section)}.pdf"
            jobs.append({"notebook": on["notebook"], "section": section, "out": str(out), "force": args.force})
        for ref in on.get("scope_pages") or []:          # "Section/Page title" → scope/ (not transcribed)
            section, page = ref.split("/", 1)
            out = base / "scope" / f"{u['code']}_{safe(section)}_{safe(page)}.pdf"
            jobs.append({"notebook": on["notebook"], "section": section, "page": page, "out": str(out),
                         "force": args.force})
    jobs += attempt_jobs(work_units(args.unit, args.allow_sat), args.force)
    if not jobs:
        print("No `onenote:` sections configured in config/units/*.yaml (or nothing changed)")
        return

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(jobs, f)
    print(f"Exporting {len(jobs)} OneNote section(s) (opens OneNote if it isn't running)…", flush=True)
    sys.exit(ps("-JobFile", f.name))


if __name__ == "__main__":
    main()
