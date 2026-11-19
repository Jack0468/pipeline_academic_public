"""Word on the street: sort what students say about a unit's exam into claims you can weigh.

Drop anything you hear about the exam into the unit's OneDrive folder, under exam_info_research/:
screenshots of Reddit, Discord or Ed posts and group chats (.png/.jpg/.webp), saved pages (.pdf), and your
own notes of what a friend or tutor said (.md/.txt, one per conversation, with who and when). An AI's
analysis of a post can go in too; it is treated as commentary, not as extra evidence.

One agy call per unit reads every item with the official information (unit outline, Canvas announcements,
the exam config) and the formulas per lecture from the recall files, then writes
    vault/units/<unit id>/exam_intel.md
with: the bottom line; every item classified (first-hand, second-hand, analysis, rumour) and checked for
independence (an analysis of a post is not a second source); the past paper as described, mapped to
lectures and slides; a claims table with confidence (strong / likely / weak / conflicts) against the
official information; what it means for study (drill, print in full, lower priority, expected variation);
suggested exam.format/scope text for the config (never applied automatically); open questions; and
verbatim transcripts of the screenshots (no usernames).

Where it is used (common.intel_block):
- gen_practice_exam.py: mocks follow the described paper's style and mark split inside the official scope,
  never copying a described question; official scope always wins.
- build_handbook.py --draft: decides which formulas and worked methods get full detail.
The note is `share: never` and share_vault.py never exports it; mock Source lines that cite it are cleaned.

Re-runs skip a unit whose items haven't changed (state/manifests/manifest_exam_intel.json); --force redoes it.
Raw replies are kept in state/exam_intel/<unit id>/.

Usage:
    python scripts/ingest_intel.py --unit ABCD1002
    python scripts/ingest_intel.py --dry-run           # list the items each unit would send
Config (optional): sources.intel in config/units/<unit>.yaml; default exam_info_research/**/*.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

import gemini
from common import ALLOW_SAT_HELP, work_units
from common import (PROMPTS, STATE, TZ, Manifest, VAULT, file_hash, file_uri, fill, find_sources,
                    load_units, scope_files, unit_path, unit_link)
from gen_practice_exam import _section, with_retries

ATTACH = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}      # sent as files (agy reads images and PDFs)
TEXT = {".md", ".txt"}                                              # sent as text blocks
MAX_ATTACH_MB = 40
NEEDED = ("## Bottom line", "## Sources", "## Claims", "## What it means for study")


def items(u: dict) -> tuple[list[Path], list[Path], list[Path]]:
    """(attachments, text notes, skipped) for a unit's intel folder."""
    found = find_sources(u, "intel")             # default exam_info_research/**/* (common.DEFAULT_SOURCES)
    attach = [p for p in found if p.suffix.lower() in ATTACH]
    text = [p for p in found if p.suffix.lower() in TEXT]
    skipped = [p for p in found if p not in attach and p not in text and not p.name.startswith(("~$", "."))
               and p.name.lower() != "desktop.ini"]
    total, kept = 0, []
    for p in attach:
        total += p.stat().st_size
        (kept if total <= MAX_ATTACH_MB * 2**20 else skipped).append(p)
    return kept, text, skipped


def items_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda p: p.name.lower()):
        h.update(f"{p.name}:{file_hash(p)};".encode())
    return h.hexdigest()[:16]


def item_list(attach: list[Path], text: list[Path]) -> tuple[str, list[str]]:
    """The numbered item list for the prompt, plus the text blocks for the text items."""
    lines, blocks, n = [], [], 0
    for p in attach:
        n += 1
        kind = "PDF" if p.suffix.lower() == ".pdf" else "image"
        lines.append(f"- #{n} `{p.name}` ({kind}, attached; its file name ends in this name)")
    for p in text:
        n += 1
        lines.append(f"- #{n} `{p.name}` (text, in an INTEL ITEM block)")
        blocks.append(f"INTEL ITEM (#{n}, {p.name}): written down by the student\n\n"
                      + p.read_text("utf-8", errors="replace").strip())
    return "\n".join(lines) or "(none)", blocks


def lecture_formulas(u: dict) -> str:
    out = []
    for p in sorted(unit_path(u, "recall").glob("*.md")):
        md = p.read_text("utf-8")
        body = _section(md, "Notes-sheet candidates")
        if body:
            body = re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", body)        # [[link|label]] → label
            out.append(f"### {p.stem}\n" + re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body))
    return "LECTURE FORMULAS (per lecture, from the slides, with pages):\n\n" + "\n\n".join(out) if out else ""


def official_texts(u: dict) -> list[str]:
    texts, outline, canvas = [], unit_path(u, "outline"), unit_path(u, "canvas")
    if outline.exists():
        texts.append("UNIT OUTLINE (official, sydney.edu.au):\n\n" + outline.read_text("utf-8"))
    if canvas.exists():
        texts.append("CANVAS (official course site; the exam-related announcements are the lecturer's own words):\n\n"
                     + canvas.read_text("utf-8"))
    lf = lecture_formulas(u)
    if lf:
        texts.append(lf)
    return texts


def link_from_hub(u: dict) -> None:
    """Add one line to the unit hub's Working files list (once; the hub is otherwise yours)."""
    hub = unit_path(u, "hub")
    if not hub.exists():
        return
    md = hub.read_text("utf-8")
    if "exam_intel" in md or "## Working files\n" not in md:
        return
    line = (f"- {unit_link(u, 'intel', 'Exam intel (word on the street)')} (unverified student reports, "
            "sorted by trust: read the bottom line and the claims' confidence)\n")
    hub.write_text(md.replace("## Working files\n", "## Working files\n" + line, 1), "utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--force", action="store_true", help="redo even if the items haven't changed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    template = (PROMPTS / "exam_intel.md").read_text("utf-8")
    manifest = Manifest("exam_intel")
    for u in work_units(args.unit, args.allow_sat):
        attach, text, skipped = items(u)
        for p in skipped:
            print(f"  WARN {u['code']}: skipped {p.name} (not an image, PDF, .md or .txt, or over {MAX_ATTACH_MB} MB "
                  "in total): save it as a PDF or paste its text into a .md")
        if not attach and not text:
            if args.unit:
                print(f"  {u['code']}: no intel items (drop them in exam_info_research/ in the unit's OneDrive folder)")
            continue
        out = unit_path(u, "intel")
        out.parent.mkdir(parents=True, exist_ok=True)
        official = scope_files(u)
        key, h = f"{u['code']}:intel", items_hash(attach + text + official)
        if not args.force and out.exists() and manifest.done(key, h):
            print(f"  {u['code']}: {len(attach) + len(text)} intel item(s), unchanged since the last run "
                  "(use --force to redo)")
            continue
        print(f"  {u['code']}: sorting {len(attach) + len(text)} intel item(s): "
              + ", ".join(p.name for p in attach + text), flush=True)
        if args.dry_run:
            continue
        listing, blocks = item_list(attach, text)
        ex = u["exam"]
        prompt = fill(template, unit_code=u["code"], unit_name=u["name"], items=listing,
                      official_files=", ".join(f"`{p.name}`" for p in official) or "(none)",
                      conditions=ex.get("conditions", "not recorded"), materials="; ".join(ex.get("materials") or []),
                      reading_min=ex.get("reading_min", 10), writing_min=ex.get("writing_min", 120),
                      total_marks=ex.get("total_marks", "not recorded"),
                      format=(ex.get("format") or "").strip() or "(none)",
                      scope=(ex.get("scope") or "").strip() or "(none)")
        raw_dir = STATE / "exam_intel" / u["id"]
        raw_dir.mkdir(parents=True, exist_ok=True)
        body = None
        try:
            for attempt in (1, 2):
                reply = with_retries(gemini.generate, attach + official, prompt, official_texts(u) + blocks,
                                     temperature=0.2)
                raw = raw_dir / f"reply_{datetime.now(TZ):%Y%m%d-%H%M%S}_{attempt}.md"
                raw.write_text(reply, "utf-8")
                missing = [s for s in NEEDED if s not in reply]
                if not missing:
                    body = reply.strip()
                    break
                print(f"  attempt {attempt}: reply lacks {', '.join(missing)} (saved as "
                      f"{raw.relative_to(STATE.parent)})" + ("; asking again" if attempt == 1 else ""), flush=True)
        except gemini.DailyQuotaExhausted as e:
            print(f"STOPPED: {e}")
            return
        except RuntimeError as e:
            print(f"  FAIL {u['code']}: {str(e)[:300]}; nothing saved")
            continue
        if body is None:
            print(f"  FAIL {u['code']}: no usable reply after 2 attempts; raw replies in {raw_dir.relative_to(STATE.parent)}")
            continue
        body = re.sub(r"\A#\s[^\n]*\n+", "", body)          # the script writes the title
        files = "\n".join(f"- [{p.name}]({file_uri(p)})" for p in attach + text)
        out.write_text("\n".join([
            "---", f"unit: {u['code']}", "type: exam intel", "source: hearsay + ai", "share: never",
            f"items: {len(attach) + len(text)}", f"model: {gemini.used_model()}",
            f"generated: {datetime.now(TZ):%Y-%m-%d}", "generator: pipeline_academic/scripts/ingest_intel.py", "---", "",
            f"# {u['code']}: exam intel (word on the street)", "",
            "> [!warning] Unverified: student reports, rumours and analyses of them, sorted by an AI. The unit outline, "
            "Canvas and the lecturer outrank everything here. Use it to weight practice inside the official scope, "
            "never to skip a topic. Generated: re-run `ingest_intel.py` after adding items, don't edit.", "",
            body, "", "## Items on file", files, ""]), "utf-8")
        manifest.mark(key, h, out=str(out))
        link_from_hub(u)
        n_claims = len(re.findall(r"^\|(?!\s*:?-)(?!\s*Claim\s*\|)", _section(body, "Claims"), re.M))
        print(f"  ok {out.relative_to(VAULT)}: {n_claims} claim(s); mocks and the handbook draft now use it", flush=True)


if __name__ == "__main__":
    main()
