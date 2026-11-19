"""Print one vault note to a PDF next to it. No AI, and nothing else changes.

    python scripts/print_note.py --unit ABCD1002 --note practice/worked_examples_LAN.md
    python scripts/print_note.py --unit ABCD1002 --note practice/worked_examples_LAN.md --title "LAN practice"

For one-off sheets. The handbook and the worked examples have their own builders (build_handbook.py --pdf,
worked_examples.py --pdf-only). Sat units are frozen (--allow-sat).
"""
from __future__ import annotations

import argparse
import re
import sys

import pdf_tools
from common import ALLOW_SAT_HELP, vault_unit_dir, work_units


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", required=True)
    ap.add_argument("--note", required=True, help="path inside the unit folder")
    ap.add_argument("--title", help="default: the note's first heading")
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    a = ap.parse_args()
    for u in work_units([a.unit], a.allow_sat):
        src = vault_unit_dir(u) / a.note
        if not src.is_file():
            sys.exit(f"{src} not found")
        md = pdf_tools.strip_front(src.read_text("utf-8"))
        broken = pdf_tools.math_problems(md)
        if broken:
            sys.exit(f"fix the maths in {src.name} first:\n  " + "\n  ".join(broken[:20]))
        heading = re.search(r"(?m)^# (.+)$", md)
        title = a.title or (heading.group(1).strip() if heading else src.stem)
        body = pdf_tools.callouts_to_bold(pdf_tools.strip_title(md))
        print(f"  ok {pdf_tools.pandoc_pdf(body, src.with_suffix('.pdf'), title, 'markdown-implicit_figures')}")


if __name__ == "__main__":
    main()
