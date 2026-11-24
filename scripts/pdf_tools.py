"""Shared steps for every script that prints a vault note to PDF.

Each build happens in a temporary folder and only a finished PDF replaces the old one, so a failed build never
leaves a damaged or missing file in the vault. Every PDF has one home, next to its source note (paths.py).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PANDOC_PAGE = ["-V", "geometry:a4paper,margin=15mm", "-V", "fontsize=10pt", "-V", "mainfont=Calibri",
               "-V", "documentclass=article", "-V", "colorlinks=true"]


def strip_front(md: str) -> str:
    return re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", md, flags=re.S)


def strip_title(md: str) -> str:
    """Drop the note's first `# ` heading: the PDF title says it."""
    return re.sub(r"(?m)^# .*\n", "", md, count=1)


def callouts_to_bold(md: str) -> str:
    """Obsidian callouts have no meaning in print: `> [!tip] x` becomes `> **Tip:** x`."""
    return re.sub(r"^> \[!(\w+)\][+-]? ?", lambda m: f"> **{m.group(1).capitalize()}:** ", md, flags=re.M)


def math_problems(md: str) -> list[str]:
    """Lines whose maths would stop the PDF build: an unmatched $, or unbalanced braces inside $...$."""
    out, fenced = [], False
    for i, ln in enumerate(md.splitlines(), 1):
        if ln.lstrip().startswith("```"):
            fenced = not fenced
        if fenced:
            continue
        plain = re.sub(r"`[^`]*`", "", ln).replace(r"\$", "")
        if plain.count("$") % 2:
            out.append(f"line {i}: unmatched $: {ln.strip()[:80]}")
            continue
        for m in re.findall(r"\$\$?([^$]+)\$\$?", plain):
            t = m.replace(r"\{", "").replace(r"\}", "")
            if t.count("{") != t.count("}"):
                out.append(f"line {i}: unbalanced braces: {m.strip()[:80]}")
    return out


PAGE_REF = re.compile(r"#page=(\d+)|\bpp?\.\s?(\d+)(?:\s?[-–]\s?(\d+))?")


def referenced_pages(md: str) -> set[int]:
    """Slide pages a note cites: "(p. 12)", "pp. 3-7", "[[...pdf#page=12|p. 12]]"."""
    out: set[int] = set()
    for link, a, b in PAGE_REF.findall(md):
        if link:
            out.add(int(link))
        elif a:
            lo, hi = int(a), int(b or a)
            out |= set(range(lo, min(hi, lo + 30) + 1))
    return out


def slide_subset(pdf: Path, pages: set[int], pad: int = 1) -> Path:
    """Only the slide pages an AI call needs (each cited page and `pad` either side), as a small cached PDF, so a
    marking call doesn't send the whole deck again. The whole deck when nothing is cited or the subset isn't smaller.
    Cached in state/slide_subsets/ by the deck's hash and the page set."""
    import hashlib

    import pymupdf
    from paths import STATE
    with pymupdf.open(str(pdf)) as d:
        n = d.page_count
        keep = sorted({q for p in pages for q in range(p - pad, p + pad + 1) if 1 <= q <= n})
        if not keep or len(keep) >= n:
            return pdf
        key = hashlib.sha256(pdf.read_bytes()).hexdigest()[:12] + "_" + hashlib.sha256(str(keep).encode()).hexdigest()[:8]
        out = STATE / "slide_subsets" / f"{pdf.stem}_{key}.pdf"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            sub = pymupdf.open()
            for q in keep:
                sub.insert_pdf(d, from_page=q - 1, to_page=q - 1)
            sub.save(str(out), garbage=4, deflate=True)
            sub.close()
    return out if out.stat().st_size < 0.8 * pdf.stat().st_size else pdf   # shared fonts can make a subset no smaller


def publish(built: Path, dest: Path) -> Path:
    """Put a finished PDF in place in one step."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    shutil.copyfile(built, partial)
    try:
        os.replace(partial, dest)
    except PermissionError:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"{dest} is open in another program: close it and run this again (nothing was changed)")
    return dest


def pandoc_pdf(md: str, dest: Path, title: str, fmt: str = "markdown", extra: list[str] | None = None) -> Path:
    """Markdown text to an A4 PDF at `dest`, with pandoc and xelatex."""
    with tempfile.TemporaryDirectory() as tmp:
        src, built = Path(tmp) / "note.md", Path(tmp) / "note.pdf"
        src.write_text(md, "utf-8")
        cmd = ["pandoc", str(src), "-f", fmt, "-o", str(built), "--pdf-engine=xelatex", *PANDOC_PAGE,
               "-V", f"title={title}", *(extra or [])]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise SystemExit(f"pandoc failed: {r.stderr[-1500:]}")
        return publish(built, dest)
