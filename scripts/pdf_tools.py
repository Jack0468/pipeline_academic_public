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
