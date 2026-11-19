"""Transcribe the student's handwritten ink from OneNote PDF exports (rank 1 source).

OneNote ink can't be exported as text by any tool, so: OneNote → File → Export → PDF (per section),
list the PDF under `sources.handwritten` in the unit config, then run this. Pages are split into
chunks and sent to Gemini vision; only handwriting is transcribed, as [!mine] / [!confused] /
[!check] callouts.

Output: <vault>/units/<unit>/sources/handwritten_<stem>.md (plus a confusion summary at the top)

Usage:
    python scripts/extract_handwriting.py [--unit ABCD1004] [--chunk 20] [--workers 3]
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from common import ALLOW_SAT_HELP, work_units
from common import PROMPTS, STATE, TZ, Manifest, file_hash, file_uri, fill, find_sources, load_units, unit_path
import gemini

_lock = threading.Lock()


def split(pdf: Path, size: int, tag: str) -> list[tuple[int, int, Path]]:
    reader = PdfReader(str(pdf))
    n = len(reader.pages)
    outdir = STATE / "chunks" / tag
    outdir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for a in range(0, n, size):
        b = min(a + size, n)
        dst = outdir / f"p{a + 1:03d}-{b:03d}.pdf"
        if not dst.exists():
            w = PdfWriter()
            for i in range(a, b):
                w.add_page(reader.pages[i])
            w.compress_identical_objects()
            with open(dst, "wb") as f:
                w.write(f)
        chunks.append((a + 1, b, dst))
    return chunks


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--chunk", type=int, default=20)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    template = (PROMPTS / "handwriting_annotations.md").read_text("utf-8")
    manifest = Manifest("handwriting")
    stop = threading.Event()
    for u in work_units(args.unit, args.allow_sat):
        for pdf in find_sources(u, "handwritten"):
            src_hash = file_hash(pdf)
            tag = f"{pdf.stem}_{src_hash[:8]}"     # a re-export gets fresh chunks/parts, never stale ones
            chunks = split(pdf, args.chunk, tag)
            part_dir = STATE / "handwriting_parts" / tag
            part_dir.mkdir(parents=True, exist_ok=True)
            print(f"{u['code']}: {pdf.name}: {len(chunks)} chunks of ≤{args.chunk} pages")

            def run(chunk):
                a, b, cpdf = chunk
                key, part = f"{pdf.name}:{a}-{b}", part_dir / f"p{a:03d}-{b:03d}.md"
                if part.exists() and manifest.done(key, src_hash):
                    return f"  = p{a}-{b} (cached)"
                if stop.is_set():
                    return f"  … p{a}-{b} left for the next run"
                prompt = fill(template, first=a, last=b, offset=a - 1, unit_code=u["code"], unit_name=u["name"])
                try:
                    text = gemini.pdf_to_markdown(cpdf, prompt, temperature=0.1)
                except gemini.DailyQuotaExhausted as e:
                    stop.set()
                    return f"  STOPPED at p{a}-{b}: {e}"
                part.write_text(text + "\n", "utf-8")
                with _lock:
                    manifest.mark(key, src_hash)
                return f"  ok p{a}-{b}"

            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                for f in [ex.submit(run, c) for c in chunks]:
                    try:
                        print(f.result(), flush=True)
                    except Exception as e:
                        print(f"  FAIL {str(e)[:200]}", flush=True)

            parts = sorted(part_dir.glob("p*.md"))
            body = "\n\n".join(p.read_text("utf-8").strip() for p in parts)
            confused = re.findall(r"^> \[!confused\]\s*\n((?:>.*\n?)+)", body, re.M)
            out = unit_path(u, "sources") / f"handwritten_{pdf.stem}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            head = ["---", f"unit: {u['code']}", "type: transcription", "source: handwritten", "rank: 1",
                    f"transcribed_by: {gemini.model_name()}", f"generated: {datetime.now(TZ):%Y-%m-%d}",
                    f"original: \"{file_uri(pdf)}\"", f"chunks: {len(parts)}/{len(chunks)}", "---", "",
                    f"# {u['code']}: your handwritten annotations",
                    "", f"Machine transcription of your ink from [{pdf.name}]({file_uri(pdf)}). "
                    "Your words outrank slide content. Where a transcription looks wrong, the PDF is the truth.", "",
                    f"## Your open questions ({len(confused)})",
                    "Things you marked as unclear while learning. Resolve each one before the exam.", ""]
            head += [f"- [ ] {' '.join(l.lstrip('> ').strip() for l in c.strip().splitlines())}" for c in confused]
            out.write_text("\n".join(head) + "\n\n## Page by page\n\n" + body + "\n", "utf-8")
            print(f"[written] {out}  ({len(parts)}/{len(chunks)} chunks, {len(confused)} open questions)")


if __name__ == "__main__":
    main()
