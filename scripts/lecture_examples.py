"""Every example and exercise on a unit's lecture slides, as one attempt-first note. One AI call per lecture.

    python scripts/lecture_examples.py --unit ABCD1001            # new or changed lectures only (cached)
    python scripts/lecture_examples.py --unit ABCD1001 --force    # every lecture again
    python scripts/lecture_examples.py --unit ABCD1001 --dry-run  # what would be sent

The recall files hold problems MODELLED on the slides (new numbers, 3-5 per lecture). This lists the slides' OWN
examples and exercises: per item the slide page, the question with its given values, and the page where the slides
answer it, never the answer itself, so you attempt first and check on the slide. Image-only slides are read by the
model (vision), which text extraction can't do.

Output: the unit's practice/lecture_examples.md (generated: an edited copy is moved to _archive/ before a rewrite),
with page links into the vault's slide copies (link_slides.py). Raw replies: state/lecture_examples/<unit id>/.
Skips lectures whose PDF hash is unchanged (state/manifests/manifest_lecture_examples.json). nightly_ai.ps1 runs
it for upcoming units; sat units are frozen (--allow-sat).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import gemini
from common import (
    ALLOW_SAT_HELP,
    PROMPTS,
    STATE,
    TZ,
    VAULT,
    Manifest,
    file_hash,
    fill,
    find_sources,
    lecture_labels,
    no_emoji,
    unit_path,
    work_units,
)

ITEM = re.compile(r"(?ms)^### p\.\s?(\d+) · (.+?)\n(.*?)(?=^### p\.|\Z)")
PAGES = re.compile(r"\bpp?\.\s?(\d+)(?:\s?[-–]\s?(\d+))?")


def pages_of(pdf: Path) -> int:
    import pymupdf
    with pymupdf.open(str(pdf)) as d:
        return d.page_count


def field(body: str, name: str) -> str:
    """The text after **name:** up to the next bold label."""
    m = re.search(rf"(?ms)^\*\*{re.escape(name)}:\*\*\s*(.*?)(?=^\*\*\w[\w ]*:\*\*|\Z)", body)
    return m.group(1).strip() if m else ""


def parse(reply: str, n_pages: int) -> tuple[list[dict], list[str]]:
    """Items from one reply; page numbers past the PDF's last page are dropped and reported."""
    reply = no_emoji(gemini.strip_fences(reply))
    if reply.strip().upper() == "NONE":
        return [], []
    items, bad = [], []
    for m in ITEM.finditer(reply):
        page, title, body = int(m.group(1)), m.group(2).strip(), m.group(3).strip()
        if not 1 <= page <= n_pages:
            bad.append(f"p.{page} '{title}' is past page {n_pages}")
            continue
        ans = field(body, "Answer on the slides")
        if any(int(x) > n_pages for g in PAGES.findall(ans) for x in g if x):
            bad.append(f"p.{page} '{title}': answer pages past page {n_pages}; kept as 'none'")
            ans = "none"
        items.append({"page": page, "title": title, "type": field(body, "Type") or "example",
                      "question": field(body, "Question"), "answer": ans})
    if not items and reply.strip():
        bad.append("reply had no items in the expected format")
    return items, bad


def link(u: dict, pdf: Path, page: int, label: str) -> str:
    copy = unit_path(u, "slides") / pdf.name                 # link_slides.py keeps these copies
    return f"[[{copy.relative_to(VAULT).as_posix()}#page={page}|{label}]]" if copy.exists() else label


def answer_links(u: dict, pdf: Path, text: str) -> str:
    def one(m):
        a, b = int(m.group(1)), m.group(2)
        first = link(u, pdf, a, f"p. {a}")
        return first + (f"-{link(u, pdf, int(b), b)}" if b else "")
    return PAGES.sub(one, text) if text.lower() != "none" else "not on the slides"


def render(u: dict, done: list[tuple[str, Path, list[dict]]]) -> str:
    n = sum(len(items) for _, _, items in done)
    lines = ["---", f"unit: {u['code']}", "type: lecture examples", "source: ai", f"model: {gemini.used_model()}",
             f"generated: {datetime.now(TZ):%Y-%m-%d}", "generator: pipeline_academic/scripts/lecture_examples.py",
             "share: never", "---", "", f"# {u['code']} lecture examples and exercises", "",
             f"Every example and exercise on the lecture slides ({n} in {len(done)} lectures), read from the slides by "
             "an AI model, so check a question against its slide. **Attempt first**: each item says where the slides "
             "answer it, never the answer. The recall files have more problems in the same style with new numbers.",
             ""]
    for label, pdf, items in done:
        lines.append(f"## {label} · {pdf.stem}")
        if not items:
            lines += ["_No examples or exercises on these slides._", ""]
            continue
        for it in items:
            where = link(u, pdf, it["page"], "p. " + str(it["page"]))
            lines += [f"### {where} · {it['title']}",
                      f"**{it['type'].capitalize()}.** {it['question']}", "",
                      f"*Answer on the slides: {answer_links(u, pdf, it['answer'])}*", ""]
    return no_emoji("\n".join(lines).rstrip() + "\n")


def write_note(u: dict, text: str, manifest: Manifest) -> str:
    out = unit_path(u, "lecture_examples")
    key = f"{u['id']}/lecture_examples.md"
    if out.exists():
        old = out.read_text("utf-8")
        if re.sub(r"(?m)^generated:.*\n", "", old) == re.sub(r"(?m)^generated:.*\n", "", text):
            return "unchanged"
        if manifest.data.get(key, {}).get("hash") != hashlib.sha256(old.encode()).hexdigest()[:16]:
            arch = unit_path(u, "archive")
            arch.mkdir(parents=True, exist_ok=True)
            out.replace(arch / f"lecture_examples_{datetime.now(TZ):%Y-%m-%d_%H%M}_edited.md")
            print(f"  {u['code']}: lecture_examples.md was edited by hand; that copy is in _archive/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, "utf-8")
    manifest.mark(key, hashlib.sha256(text.encode()).hexdigest()[:16])
    return "written"


def read_one(u: dict, pdf: Path, label: str, raw: Path, template: str, manifest: Manifest) -> Path:
    """One AI call: the lecture's examples, saved raw (the note is rebuilt from these)."""
    prompt = fill(template, unit_code=u["code"], unit_name=u["name"], label=label)
    reply = gemini.pdf_to_markdown(pdf, prompt, temperature=0.1)
    raw.write_text(reply, "utf-8")
    manifest.mark(f"{u['code']}:{pdf.name}", file_hash(pdf), model=gemini.used_model())
    return pdf


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--force", action="store_true", help="read every lecture again")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    template = (PROMPTS / "lecture_examples.md").read_text("utf-8")
    manifest = Manifest("lecture_examples")
    for u in work_units(a.unit, a.allow_sat):
        lectures = find_sources(u, "lectures")
        if not lectures:
            continue
        labels = lecture_labels(lectures)
        raw_dir = STATE / "lecture_examples" / u["id"]
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw = {p: raw_dir / f"{labels[p]}.md" for p in lectures}
        todo = [p for p in lectures if a.force or not raw[p].exists()
                or not manifest.done(f"{u['code']}:{p.name}", file_hash(p))]
        print(f"  {u['code']}: {len(lectures)} lecture(s), {len(todo)} to read")
        if a.dry_run:
            for p in todo:
                print(f"    {labels[p]:6} {p.name}")
            continue
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(read_one, u, p, labels[p], raw[p], template, manifest): p for p in todo}
            for f in as_completed(futs):
                try:
                    f.result()
                    print(f"    ok {labels[futs[f]]}", flush=True)
                except gemini.DailyQuotaExhausted as e:
                    print(f"    STOPPED: {e}")
                    break
                except Exception as e:
                    print(f"    FAIL {labels[futs[f]]}: {str(e)[:160]}")
        done = []
        for pdf in lectures:
            if not raw[pdf].exists():
                continue
            items, bad = parse(raw[pdf].read_text("utf-8"), pages_of(pdf))
            for b in bad:
                print(f"    WARN {labels[pdf]}: {b}")
            done.append((labels[pdf], pdf, items))
        status = write_note(u, render(u, done), manifest)
        print(f"  {u['code']}: {sum(len(i) for _, _, i in done)} example(s) and exercise(s) from {len(done)} "
              f"lecture(s); practice/lecture_examples.md {status}")


if __name__ == "__main__":
    main()
