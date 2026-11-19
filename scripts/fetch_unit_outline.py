"""Fetch each unit's official outline from the public USyd site → vault `units/<unit>/_unit_outline.md`.

URL convention: https://www.sydney.edu.au/units/<CODE>/<YEAR>-<SESSION>-<MODE>-<CAMPUS>
    e.g. ABCD1001, 2026, S2C (semester 2), ND (normal day), CC (Camperdown/Darlington)
    → https://www.sydney.edu.au/units/ABCD1001/2026-S2C-ND-CC
The session comes from the unit config (`session: 2026_sc2` → 2026-S2C). Override with
`outline_url:` in the unit config for anything unusual (intensives, other campuses).

Zero AI calls: the page is server-rendered HTML and is parsed directly. Output per unit:
  - <vault>/units/<id>/_unit_outline.md   tutor context (Gem / NotebookLM / agents): details,
    description, learning outcomes, assessments (weights, exam format, hurdles), readings, schedule
  - state/outlines/<CODE>.json             the same data, structured, for other scripts
  - state/outlines/<CODE>.html             the raw page (cache)
It also checks the outline's final-exam weight against state/marks.json (results.xlsx) and flags
hurdles.

Usage:
    python scripts/fetch_unit_outline.py [--unit ABCD1001] [--if-missing] [--offline]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime

from common import ALLOW_SAT_HELP, work_units
from common import STATE, TZ, load_units, unit_path

BASE = "https://www.sydney.edu.au/units/{code}/{year}-{session}-ND-CC"
OUT = STATE / "outlines"


def outline_url(u: dict) -> str:
    if u.get("outline_url"):
        return u["outline_url"]
    m = re.match(r"(\d{4})_s[c]?(\d)", str(u.get("session", "")).lower())
    if not m:
        raise SystemExit(f"{u['code']}: can't derive the outline URL from session {u.get('session')!r}; set outline_url")
    return BASE.format(code=u["code"], year=m.group(1), session=f"S{m.group(2)}C")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "pipeline_academic (personal study tool)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("​", "")).replace("''", "'").strip()


def section_tables(soup, heading: str):
    h = soup.find(lambda t: t.name in ("h2", "h3") and clean(t.get_text()) == heading)
    return h, (h.find_all_next("table") if h else [])


def parse(html: str) -> dict:
    from bs4 import BeautifulSoup
    s = BeautifulSoup(html, "html.parser")
    d = {"title": clean(s.find("h1").get_text()) if s.find("h1") else "",
         "session": clean(s.find("h1").find_next("h3").get_text()) if s.find("h1") else ""}

    ov = s.find(lambda t: t.name == "h2" and clean(t.get_text()) == "Overview")
    desc = ""
    if ov:
        for el in ov.find_all_next(["div", "p"]):
            txt = clean(el.get_text(" "))
            if len(txt) > 120 and "Unit details" not in txt[:40]:
                desc = txt
                break
    d["description"] = desc

    details = {}
    h, tables = section_tables(s, "Unit details and rules")
    if tables:
        for tr in tables[0].find_all("tr"):
            c = [clean(x.get_text(" ")).rstrip(" ?") for x in tr.find_all(["th", "td"])]
            if len(c) >= 2:
                details[c[0].rstrip(" ?")] = c[1]
    d["details"] = details
    h, tables = section_tables(s, "Teaching staff")
    d["staff"] = [clean(tr.get_text(" ")) for tr in tables[0].find_all("tr")] if tables else []

    assess = []
    h, _ = section_tables(s, "Assessment summary")
    table = next((t for t in s.find_all("table")              # find it by its header row, not position
                  if [clean(x.get_text(" ")) for x in t.find("tr").find_all(["th", "td"])][:3]
                  == ["Type", "Description", "Weight"]), None)
    if table:
        cur = None
        for tr in table.find_all("tr")[1:]:
            c = [clean(x.get_text(" ")) for x in tr.find_all(["th", "td"])]
            if len(c) >= 4:
                cur = dict(zip(["type", "description", "weight", "due", "length", "ai"], c))
                assess.append(cur)
            elif cur and c and c[0].startswith("Outcomes assessed"):
                cur["outcomes"] = c[0].replace("Outcomes assessed:", "").strip()
    d["assessments"] = assess
    notes = []
    if h:
        for el in h.find_all_next(["p", "li"]):
            prev = el.find_previous(["h2", "h3"])
            if prev and clean(prev.get_text()) not in ("Assessment summary",):
                break
            t = clean(el.get_text(" "))
            if len(t) > 40 and t not in notes:
                notes.append(t)
    d["assessment_notes"] = notes

    weeks = []
    for t in s.find_all("table"):
        head = [clean(x.get_text(" ")) for x in t.find("tr").find_all(["th", "td"])]
        if head[:2] == ["WK", "Topic"]:
            for tr in t.find_all("tr")[1:]:
                c = [clean(x.get_text(" ")) for x in tr.find_all(["th", "td"])]
                if len(c) >= 3:
                    weeks.append(dict(zip(["week", "topic", "activity", "outcomes"], c + [""])))
    d["schedule"] = weeks

    sc = s.find(lambda t: t.name == "h3" and clean(t.get_text()) == "Study commitment")
    d["study_commitment"] = clean(sc.find_next("p").get_text(" ")) if sc else ""
    rr = s.find(lambda t: t.name == "h3" and clean(t.get_text()) == "Required readings")
    d["readings"] = clean(rr.find_next(["div", "p", "ul"]).get_text(" ")) if rr else ""

    los = []
    for li in s.find_all("li"):
        m = re.match(r"^(LO\d+)\s*\.?\s*(.+)", clean(li.get_text(" ")))
        if m and li.find("b") and m.group(1) not in [x[0] for x in los]:
            los.append((m.group(1), m.group(2)[0].upper() + m.group(2)[1:]))
    d["learning_outcomes"] = los
    return d


def final_exam(d: dict) -> dict | None:
    for a in d["assessments"]:
        if re.search(r"final exam", a.get("description", ""), re.I):
            return a
    return None


def hurdles(d: dict) -> list[str]:
    text = " ".join([a.get("description", "") for a in d["assessments"]] + d["assessment_notes"])
    return [s.strip() for s in re.split(r"(?<=[.!])\s+", text) if re.search(r"hurdle|necessary.*pass|must pass", s, re.I)]


def readings_md(r: str) -> str:
    r = re.sub(r"\s*(Textbooks:|Recommended for reading:)\s*", r"\n\n**\1**\n", r)
    r = re.sub(r"\s(\d+)\.\s", r"\n\1. ", r)
    return r.replace(";\n", "\n").strip()


def render(u: dict, d: dict, url: str, marks: dict) -> str:
    ex = final_exam(d)
    hs = hurdles(d)
    L = ["---", f"unit: {u['code']}", "type: unit outline", "source: usyd-unit-outline", f"url: {url}", f"fetched: {datetime.now(TZ):%Y-%m-%d}",
         "generator: pipeline_academic/scripts/fetch_unit_outline.py", "---", "",
         f"# Unit Setup Context: {d['title'] or u['code'] + ' ' + u['name']}", "", url, "",
         "**System Prompt / Persona Note for Tutor Agent:**",
         f"You are an AI tutor for the university course **{d['title'] or u['code']}**. Use the following syllabus, "
         "schedule, assessment details and learning outcomes to guide student inquiries, align your explanations with "
         "the appropriate week's content, and recommend the correct readings. Give hints before answers.", "",
         "> [!info] Generated from the official unit outline (re-run `fetch_unit_outline.py` to refresh). "
         "Canvas and the lecturer's announcements override it.", "",
         "## 1. Course Details",
         f"* **Course Code & Title:** {d['title']}", f"* **Semester & Location:** {d['session']}"]
    for k in ("Credit points", "Prerequisites", "Corequisites", "Assumed knowledge"):
        if d["details"].get(k):
            L.append(f"* **{k}:** {d['details'][k]}")
    if d["staff"]:
        L.append(f"* **Teaching staff:** {'; '.join(d['staff'])}")
    if d["study_commitment"]:
        L.append(f"* **Study Commitment:** {d['study_commitment']}")
    L += ["", "## 2. Course Description", d["description"], "", "## 3. Learning Outcomes (LO)"]
    L += [f"* **{lo}:** {txt}" for lo, txt in d["learning_outcomes"]]
    L += ["", "## 4. Assessment", "| Type | Assessment | Weight | Due | Length | AI use | LOs |",
          "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"]
    L += [f"| {a.get('type', '')} | {a.get('description', '')} | {a.get('weight', '')} | {a.get('due', '')} | "
          f"{a.get('length', '')} | {a.get('ai', '')} | {a.get('outcomes', '')} |" for a in d["assessments"]]
    if ex:
        L += ["", f"> [!important] Final exam: **{ex.get('weight')}**, {ex.get('length')}, {ex.get('description')}. "
                  f"Your exam: {u['exam']['start_dt']:%a %d %b %H:%M}, {u['exam']['venue']}, seat {u['exam'].get('seat')}; "
                  f"conditions: {u['exam']['conditions']}."]
    if hs:
        L += ["", "> [!warning] Hurdle", *[f"> {h}" for h in hs]]
    if d["assessment_notes"]:
        L += ["", "**Assessment notes (from the outline):**", *[f"- {n}" for n in d["assessment_notes"]]]
    L += ["", "## 5. Required Readings & Resources", readings_md(d["readings"]), "",
          "## 6. Weekly Schedule & Learning Activities", "",
          "| Week | Topic / Activity Description | Activity Type | Associated LOs |", "| :--- | :--- | :--- | :--- |"]
    for w in d["schedule"]:
        wk = w["week"].replace("Week ", "")
        wk = "Multi" if "multiple weeks" in w["week"].lower() else wk
        L.append(f"| **{wk}** | {w['topic']} | {w['activity']} | {w['outcomes'].replace(' ', ', ')} |")
    return "\n".join(L) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--if-missing", action="store_true", help="only units without an outline yet")
    ap.add_argument("--offline", action="store_true", help="re-render from the cached HTML")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = STATE / "marks.json"
    marks = json.loads(mpath.read_text("utf-8"))["units"] if mpath.exists() else {}
    for u in work_units(args.unit, args.allow_sat):
        dst = unit_path(u, "outline")
        js = OUT / f"{u['code']}.json"
        if args.if_missing and dst.exists() and js.exists():
            continue
        url = outline_url(u)
        raw = OUT / f"{u['code']}.html"
        try:
            html = raw.read_text("utf-8") if args.offline and raw.exists() else fetch(url)
        except Exception as e:
            print(f"  FAIL {u['code']}: {url}: {e}")
            continue
        raw.write_text(html, "utf-8")
        d = parse(html)
        if not d["schedule"] and not d["assessments"]:
            print(f"  FAIL {u['code']}: {url} didn't look like a unit outline (check the session code / outline_url)")
            continue
        d["url"] = url
        d["hurdles"] = hurdles(d)
        js.write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render(u, d, url, marks), "utf-8")
        ex = final_exam(d)
        line = f"  ok {u['code']}: {len(d['learning_outcomes'])} LOs, {len(d['schedule'])} schedule rows, " \
               f"{len(d['assessments'])} assessments → {dst.name}"
        print(line)
        if ex:
            w = float(re.sub(r"[^\d.]", "", ex.get("weight", "")) or 0) / 100
            have = marks.get(u["code"], {}).get("exam_weight")
            flag = "" if have is None or abs(have - w) < 0.005 else f"   WARN results.xlsx has {have:.0%}: check it"
            print(f"      final exam {ex.get('weight')} ({ex.get('length')}){flag}")
        for h in d["hurdles"]:
            print(f"      WARN hurdle: {h[:140]}")


if __name__ == "__main__":
    main()
