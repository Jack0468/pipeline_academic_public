"""Batch-export OneNote sections to PDF (zero tokens), ready for extract_handwriting.py.

Each unit config names its notebook and the sections worth transcribing:

    onenote:
      notebook: 2026_SC2_ABCD1001
      sections: [Lecture, exam]
      scope_pages: ["exam/INFO"]     # single pages about what the final covers

PDFs land in state/onenote/<unit id>/<CODE>_<section>.pdf (sections, transcribed as handwriting) and
state/onenote/<unit id>/scope/ (scope pages, read directly by gen_practice_exam.py). A section is only re-exported when
OneNote says it changed after the last export (or with --force), so re-runs don't change file
hashes and don't trigger re-transcription.

Usage:
    python scripts/export_onenote.py [--unit ABCD1001] [--force]
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


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        sys.exit(ps("-List"))

    jobs = []
    for u in work_units(args.unit, args.allow_sat):
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
    if not jobs:
        print("No `onenote:` sections configured in config/units/*.yaml")
        return

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(jobs, f)
    print(f"Exporting {len(jobs)} OneNote section(s) (opens OneNote if it isn't running)…", flush=True)
    sys.exit(ps("-JobFile", f.name))


if __name__ == "__main__":
    main()
