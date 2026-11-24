"""Every unit you have taken, cross-checked across your results sheet, Canvas, OneDrive and this pipeline.

    python scripts/course_inventory.py [--no-canvas]

Sources (all read-only):
  results.xlsx   the totals sheet from config/targets.yaml (`results_xlsx`, `results_total_sheet`): year, session,
                 unit, cp, level. Marks and grades are not read.
  Canvas         every course you are or were enrolled in (active, completed, pending), with its term
  OneDrive       unit folders under .UNI-COURSEWORK and .UNI-COURSEWORK/.old, named <year>_sc<n>_<CODE>
  pipeline       config/units/*.yaml, the vault's units/ folders, and state/concept_index/ (back-scraped units)

Writes (private, never in this repo and never exported by share_vault.py):
  paths.COURSE_INVENTORY  (default: <vault>/_pipeline/course_inventory.md)  one row per unit, then the mismatches
  STATE/course_inventory.json                                              the same data for other scripts

Prints counts only, so nothing about you lands in a terminal log.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from paths import CONFIG, COURSE_INVENTORY, ONEDRIVE_COURSEWORK, STATE, VAULT

CODE = re.compile(r"\b([A-Z]{4}\d{4})\b")
FOLDER = re.compile(r"^(\d{4})_s[c]?(\d)_([a-z]{4}\d{4})$", re.I)
_SEM = r"(?<![a-z])(?:semester|sem|s)\s*([12])(?!\d)"      # 'Semester 2', 'S2', 'S2C'; not 'Analysis 1'
TERM = re.compile(rf"{_SEM}\D*?(20\d\d)|(20\d\d)\D*?{_SEM}", re.I)
INTENSIVE = re.compile(r"(summer|winter)\D*?(20\d\d)|(20\d\d)\D*?(summer|winter)", re.I)
INVENTORY_JSON = STATE / "course_inventory.json"


def unit_id(year: int | str, session: str, code: str) -> str:
    return f"{year}_{session}_{code.lower()}"


def norm_session(s) -> str | None:
    """'S2', 'Semester 2', 2, 'sc2' -> 'sc2'; 'Summer' -> 'summer'. None if it can't be read."""
    t = str(s or "").strip().lower()
    if m := re.search(r"(summer|winter)", t):
        return m.group(1)
    if m := re.fullmatch(r"(?:sc|s|sem(?:ester)?)?\s*([12])(?:\D.*)?", t):
        return f"sc{m.group(1)}"
    return None


def parse_folder(name: str) -> tuple[str, str, str] | None:
    """'2025_SC2_ABCD2104' -> ('2025', 'sc2', 'ABCD2104')."""
    m = FOLDER.match(name)
    return (m.group(1), f"sc{m.group(2)}", m.group(3).upper()) if m else None


def parse_term(text: str, start_at: str | None = None) -> tuple[str | None, str | None]:
    """Year and session from a Canvas term name ('Semester 2 2025', '2025 S2', 'Summer 2026'), else from start_at."""
    if m := TERM.search(text or ""):
        sem, year = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        return year, f"sc{sem}"
    if m := INTENSIVE.search(text or ""):
        kind, year = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        return year, kind.lower()
    if start_at and re.match(r"20\d\d-\d\d", start_at):
        y, mo = int(start_at[:4]), int(start_at[5:7])
        return str(y), "sc1" if 2 <= mo <= 6 else "sc2" if 7 <= mo <= 11 else None
    return None, None


def parse_canvas_course(c: dict) -> dict | None:
    """One Canvas course -> {code, year, session, canvas_id, canvas_name}; None if no unit code is visible."""
    text = " ".join(str(c.get(k) or "") for k in ("course_code", "name", "sis_course_id"))
    m = CODE.search(text.upper())
    if not m:
        return None
    term = (c.get("term") or {}).get("name", "")
    year, session = parse_term(f"{term} {c.get('sis_course_id') or ''} {c.get('name') or ''}", c.get("start_at"))
    return {"code": m.group(1), "year": year, "session": session, "canvas_id": c.get("id"),
            "canvas_name": c.get("name") or c.get("course_code"),
            "restricted": bool(c.get("access_restricted_by_date"))}


# ---------- readers ----------

def from_results() -> list[dict]:
    """Rows of the results totals sheet: unit, year, session, cp, level (never marks)."""
    import openpyxl
    from common import copy_locked, expand, load_yaml
    cfg = load_yaml(CONFIG / "targets.yaml")
    wb = openpyxl.load_workbook(copy_locked(Path(expand(cfg["results_xlsx"]))), data_only=True, read_only=True)
    ws = wb[cfg["results_total_sheet"]]
    rows = list(ws.iter_rows(values_only=True))
    heads = [i for i, row in enumerate(rows[:10]) if "unit" in [str(v or "").strip().lower() for v in row]]
    if not heads:
        raise SystemExit(f"no 'unit' header in the first 10 rows of {cfg['results_total_sheet']!r}")
    header = [str(v or "").strip().lower() for v in rows[heads[0]]]
    col = {k: header.index(k) for k in ("unit", "year", "session", "cp", "course lv") if k in header}
    return [r for row in rows[heads[0] + 1:] if (r := results_row(row, col))]


def results_row(row: tuple, col: dict[str, int]) -> dict | None:
    m = CODE.search(str(row[col["unit"]] or "").upper())
    if not m:
        return None
    def get(k):
        return row[col[k]] if k in col else None
    y = get("year")
    year = str(int(y)) if isinstance(y, (int, float)) else (str(y) if y else None)
    return {"code": m.group(1), "year": year, "session": norm_session(get("session")), "cp": get("cp"),
            "level": get("course lv")}


def from_canvas() -> list[dict]:
    from canvas_sync import get
    seen, out = set(), []
    for state in ("active", "completed", "invited_or_pending"):
        for c in get("courses", enrollment_state=state, per_page=100, **{"include[]": "term"}):
            if c.get("id") in seen:
                continue
            seen.add(c.get("id"))
            if p := parse_canvas_course(c):
                out.append(p)
    return out


def from_onedrive(root: Path = ONEDRIVE_COURSEWORK) -> tuple[list[dict], list[str]]:
    found, other = [], []
    for base, where in ((root, "current"), (root / ".old", "old")):
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            if d.name.startswith(".") and base == root:
                continue
            if p := parse_folder(d.name):
                files = sum(1 for f in d.rglob("*") if f.is_file())
                found.append({"code": p[2], "year": p[0], "session": p[1], "folder": str(d), "where": where,
                              "files": files, "canvas_mirror": (d / "canvas").is_dir()})
            else:
                other.append(f"{where}/{d.name}")
    return found, other


def from_pipeline() -> dict[str, set[str]]:
    have = defaultdict(set)
    for f in (CONFIG / "units").glob("*.yaml"):
        have[f.stem.lower()].add("config")
    for d in (VAULT / "units").glob("*"):
        if d.is_dir():
            have[d.name.lower()].add("vault")
    for d in (STATE / "concept_index").glob("*"):
        if d.is_dir() and not d.name.startswith("_"):
            have[d.name.lower()].add("indexed")
    return have


# ---------- merge ----------

def merge(results: list[dict], canvas: list[dict], onedrive: list[dict], pipeline: dict[str, set[str]]) -> list[dict]:
    """One record per unit id. A source row without a year or session joins the unit with the same code if there
    is exactly one; otherwise it stays on its own under '?'."""
    units: dict[str, dict] = {}

    def slot(row: dict) -> dict:
        if row.get("year") and row.get("session"):
            uid = unit_id(row["year"], row["session"], row["code"])
        else:
            same = [k for k, v in units.items() if v["code"] == row["code"]]
            uid = same[0] if len(same) == 1 else unit_id(row.get("year") or "?", row.get("session") or "?", row["code"])
        return units.setdefault(uid, {"id": uid, "code": row["code"], "results": None, "canvas": [],
                                      "onedrive": [], "pipeline": []})

    for r in results:
        slot(r)["results"] = {"cp": r.get("cp"), "level": r.get("level")}
    for d in onedrive:
        slot(d)["onedrive"].append({k: d[k] for k in ("folder", "where", "files", "canvas_mirror")})
    for c in canvas:
        slot(c)["canvas"].append({k: c[k] for k in ("canvas_id", "canvas_name", "restricted")})
    for uid, have in pipeline.items():
        if uid in units:
            units[uid]["pipeline"] = sorted(have)
        elif m := re.match(r"^(\d{4})_(sc\d|summer|winter)_([a-z]{4}\d{4})$", uid):
            slot({"code": m.group(3).upper(), "year": m.group(1), "session": m.group(2)})["pipeline"] = sorted(have)
    return sorted(units.values(), key=lambda u: u["id"])


def issues(u: dict) -> list[str]:
    out = []
    taken = u["results"] is not None
    if taken and not u["onedrive"]:
        out.append("in results, no OneDrive folder")
    if taken and not u["canvas"]:
        out.append("in results, not found on Canvas")
    if u["onedrive"] and not taken:
        out.append("OneDrive folder, not in results")
    if u["canvas"] and not taken:
        out.append("on Canvas, not in results")
    if len(u["onedrive"]) > 1:
        out.append("more than one OneDrive folder")
    if u["canvas"] and u["onedrive"] and not any(d["canvas_mirror"] for d in u["onedrive"]):
        out.append("no Canvas mirror yet")
    if u["canvas"] and all(c["restricted"] for c in u["canvas"]):
        out.append("Canvas access closed")
    if "?" in u["id"]:
        out.append("year or session unknown")
    return out


def render(units: list[dict], other: list[str], sources: dict[str, str]) -> str:
    tick = lambda b: "yes" if b else "-"  # noqa: E731
    lines = ["---", "type: course inventory", "source: course_inventory.py", f"generated: {date.today().isoformat()}",
             "share: never", "---", "", "# Courses taken", "",
             "> [!info] Generated by `scripts/course_inventory.py` from your results sheet, Canvas, OneDrive and the "
             "pipeline's own files. Re-run it to update; edit the sources, not this page. Marks are never read.", "",
             "Sources: " + "; ".join(f"{k}: {v}" for k, v in sources.items()), "",
             "| Unit | Results | Canvas | OneDrive | Canvas mirror | Pipeline | Check |",
             "|---|---|---|---|---|---|---|"]
    for u in units:
        od = ", ".join(f"[{d['where']}](<{Path(d['folder']).as_uri()}>) ({d['files']} files)" for d in u["onedrive"])
        cv = ", ".join(str(c["canvas_id"]) + (" (closed)" if c["restricted"] else "") for c in u["canvas"])
        lines.append(f"| {u['id']} | {tick(u['results'])} | {cv or '-'} | {od or '-'} | "
                     f"{tick(any(d['canvas_mirror'] for d in u['onedrive']))} | {', '.join(u['pipeline']) or '-'} | "
                     f"{'; '.join(issues(u)) or 'ok'} |")
    if other:
        lines += ["", "## OneDrive folders that are not unit folders", "", *[f"- {o}" for o in other]]
    by = defaultdict(list)
    for u in units:
        for i in issues(u):
            by[i].append(u["id"])
    if by:
        lines += ["", "## To check", ""]
        lines += [f"- **{k}** ({len(v)}): {', '.join(v)}" for k, v in sorted(by.items())]
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-canvas", action="store_true", help="skip Canvas (offline)")
    a = ap.parse_args()
    sources = {}
    try:
        results = from_results()
        sources["results"] = f"{len(results)} rows"
    except Exception as e:      # a missing sheet or config shouldn't stop the other checks
        results, sources["results"] = [], f"not read ({type(e).__name__})"
    canvas = []
    if a.no_canvas:
        sources["Canvas"] = "skipped"
    else:
        try:
            canvas = from_canvas()
            sources["Canvas"] = f"{len(canvas)} courses"
        except SystemExit as e:
            sources["Canvas"] = f"not read ({e})"
        except Exception as e:
            sources["Canvas"] = f"not read ({type(e).__name__})"
    onedrive, other = from_onedrive()
    sources["OneDrive"] = f"{len(onedrive)} unit folders"
    units = merge(results, canvas, onedrive, from_pipeline())
    COURSE_INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    COURSE_INVENTORY.write_text(render(units, other, sources), "utf-8")
    INVENTORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_JSON.write_text(json.dumps({"generated": date.today().isoformat(), "sources": sources, "units": units,
                                          "other_folders": other}, indent=1), "utf-8")
    n_issue = defaultdict(int)
    for u in units:
        for i in issues(u):
            n_issue[i] += 1
    print(f"{len(units)} units | " + " | ".join(f"{k}: {v}" for k, v in sources.items()))
    for k, v in sorted(n_issue.items()):
        print(f"  {v:3}  {k}")
    print(f"Written (private): {COURSE_INVENTORY}")


if __name__ == "__main__":
    main()
