"""Convert pages of a third-party study PDF (a shared formula sheet, condensed notes) into faithful Markdown in the
vault, so build_handbook.py --draft can use it as the handbook's backbone. Works for any unit.

    python scripts/convert_reference.py --unit ABCD1002 --pdf "xtra_resources/X.pdf" --pages 2,73-76 \
        --name condensed --author "unknown"

Output: <vault>/units/<id>/sources/<CODE>_formulas_<name>.md, with provenance front matter (source PDF, pages,
author, the model that converted it). build_handbook.py reads every sources/*formulas*.md as a FORMULA SHEET.
One agy call. Re-running overwrites only this generated file; check page 1 of the output by hand, as for
ABCD1001's sheet.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import gemini
from common import unit_path, ALLOW_SAT_HELP, work_units
from common import PROMPTS, TZ, file_uri, fill, load_units, no_emoji, unit_dir, vault_unit_dir


def pages(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--pdf", required=True, help="path relative to the unit's OneDrive folder, or absolute")
    ap.add_argument("--pages", required=True, help="PDF page numbers (1-based), e.g. 2,73-76")
    ap.add_argument("--name", required=True, help="short name for the output file, e.g. condensed")
    ap.add_argument("--author", default="unknown", help="who wrote the original (for provenance and sharing)")
    a = ap.parse_args()
    import fitz
    units = work_units([a.unit], a.allow_sat)
    if not units:
        return
    u = units[0]
    src = Path(a.pdf) if Path(a.pdf).is_absolute() else unit_dir(u) / a.pdf
    want = pages(a.pages)
    with tempfile.TemporaryDirectory(prefix="ref_") as tmp:
        part = Path(tmp) / f"{src.stem}_pages.pdf"
        doc, out_doc = fitz.open(src), fitz.open()
        for n in want:
            out_doc.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
        out_doc.save(part)
        print(f"  {u['code']}: converting {len(want)} page(s) of {src.name} with {gemini.AGY_MODEL if gemini.BACKEND == 'agy' else 'API'}…",
              flush=True)
        md = gemini.generate([part], fill((PROMPTS / "reference_convert.md").read_text("utf-8"),
                                          unit_code=u["code"], unit_name=u["name"]), temperature=0.1)
    out = unit_path(u, "sources") / f"{u['code']}_formulas_{a.name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    front = ["---", f"unit: {u['code']}", "type: reference notes", "source: shared-notes", f"author: {a.author}",
             f"original: \"{file_uri(src)}\"", f"pages: \"{a.pages}\"",
             f"converted_by: {gemini.used_model()} (faithful conversion; check page 1 by hand)",
             f"converted: {datetime.now(TZ):%Y-%m-%d}", "share: never", "generator: pipeline_academic/scripts/convert_reference.py", "---", ""]
    note = (f"> [!info] Faithful Markdown conversion of PDF pages {a.pages} of [{src.name}]({file_uri(src)}) "
            f"(author: {a.author}). The backbone for build_handbook.py; edit the handbook, not this file.\n\n")
    out.write_text("\n".join(front) + note + no_emoji(md).strip() + "\n", "utf-8")
    print(f"  ok {out.relative_to(vault_unit_dir(u).parent.parent)} ({len(md)} chars)")


if __name__ == "__main__":
    main()
