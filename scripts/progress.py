"""Study progress check-in: <vault>/00_dashboard/progress.md (you edit it in Obsidian).

Format, one section per unit:
    ## ABCD1001 · exam Tue 17 Nov 09:00
    practice_blocks_done:: 0
    mocks_done:: 0
    - [x] L1 · [[ABCD1001_L1|recall]]
    - [ ] L2 · [[ABCD1001_L2|recall]]

plan_study.py reads it. Ticked lectures leave the coverage queue, and units with more
uncovered lectures get a bigger share of the time left. scaffold_vault.py creates the page and
appends lectures that are new since it was made, without touching existing ticks.
"""
from __future__ import annotations

import re

from common import VAULT, find_sources, lecture_labels

PAGE = VAULT / "00_dashboard" / "progress.md"
INTRO = """---
type: progress
note: "Yours to edit. plan_study.py reads it; scaffold_vault.py only appends new lectures."
---
# Progress check-in

> [!tip] How to use
> - Tick a lecture once its coverage block is done properly (blurt, fix gaps, recall questions, a problem or two).
> - Bump the counters after practice blocks and mock exams.
> - Then run `powershell -File scripts\\refresh.ps1`. The plan re-spreads what's left over the time
>   remaining (from *now*), and Google Calendar updates. Missed blocks simply drop off; nothing to clean up.
"""


def unit_lectures(u) -> list[str]:
    lec = find_sources(u, "lectures")
    labels = lecture_labels(lec)
    return [labels[p] for p in lec]


def section(u) -> list[str]:
    head = [f"## {u['code']} · exam {u['exam']['start_dt']:%a %d %b %H:%M}", "practice_blocks_done:: 0",
            "mocks_done:: 0", ""]
    return head + [f"- [ ] {lab} · [[{u['code']}_{lab}|recall]]" for lab in unit_lectures(u)] + [""]


def ensure_page(units) -> str | None:
    """Create the page, or append lectures/units that are missing. Returns a note of what changed."""
    if not PAGE.exists():
        PAGE.parent.mkdir(parents=True, exist_ok=True)
        PAGE.write_text(INTRO + "\n" + "\n".join(l for u in units for l in section(u)), "utf-8")
        return "created"
    text = PAGE.read_text("utf-8")
    prog = read(units)
    added = []
    for u in units:
        if u["code"] not in prog:
            text = text.rstrip() + "\n\n" + "\n".join(section(u))
            added.append(u["code"])
            continue
        missing = [lab for lab in unit_lectures(u) if lab not in prog[u["code"]]["listed"]]
        if missing:
            m = re.search(rf"^## {u['code']}\b.*$", text, re.M)
            nxt = re.search(r"^## ", text[m.end():], re.M)
            cut = m.end() + (nxt.start() if nxt else len(text) - m.end())
            block = "".join(f"- [ ] {lab} · [[{u['code']}_{lab}|recall]]\n" for lab in missing)
            text = text[:cut].rstrip() + "\n" + block + "\n" + text[cut:].lstrip("\n")
            added += [f"{u['code']} {lab}" for lab in missing]
    if added:
        PAGE.write_text(text, "utf-8")
        return "appended " + ", ".join(added)
    return None


def read(units=None) -> dict:
    """{code: {"covered": set(labels), "listed": set(labels), "practice": int, "mocks": int}}"""
    if not PAGE.exists():
        return {}
    out, cur = {}, None
    for line in PAGE.read_text("utf-8").splitlines():
        h = re.match(r"^##\s+([A-Z]{4}\d{4})\b", line)
        if h:
            cur = out.setdefault(h.group(1), {"covered": set(), "listed": set(), "practice": 0, "mocks": 0})
            continue
        if cur is None:
            continue
        c = re.match(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)(?:\s+·.*)?$", line)   # label = text before " · "
        if c:
            cur["listed"].add(c.group(2))
            if c.group(1).lower() == "x":
                cur["covered"].add(c.group(2))
        f = re.match(r"^(practice_blocks_done|mocks_done)::\s*(\d+)", line.strip())
        if f:
            cur["practice" if f.group(1).startswith("practice") else "mocks"] = int(f.group(2))
    return out
