"""Work back from a target EIHWAM to the exam mark each unit needs.

Reads results.xlsx read-only (from a temp copy; the file is often open in Excel).
Writes state/marks.json (used by plan_study.py) and <vault>/00_dashboard/marks.md.

Usage:
    python scripts/marks.py [--target 80]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

from common import STATE, TZ, VAULT, CONFIG, copy_locked, expand, load_units, load_yaml

EXAM_WORD = "exam"


def norm(s) -> str:
    return str(s or "").strip().lower()


def find_header(ws, first: str) -> tuple[int, list[str]]:
    """Locate the header row (tables don't always start at A1)."""
    for r in range(1, 11):
        header = [norm(c.value) for c in ws[r]]
        if first in header:
            return r, header
    raise ValueError(f"no '{first}' header in the first 10 rows of sheet {ws.title!r}")


def read_unit_sheet(ws, assumptions: dict) -> dict:
    """Parse a unit sheet: task | is Hurdle | weight | mark | out of | ..."""
    hrow, header = find_header(ws, "task")
    col = {name: header.index(name) for name in ("task", "weight", "mark", "out of")}
    assume = {norm(k): float(v) for k, v in (assumptions or {}).items()}
    tasks, exam_w = [], 0.0
    for row in ws.iter_rows(min_row=hrow + 1, values_only=True):
        task = row[col["task"]]
        if not isinstance(task, str) or not task.strip():
            continue
        w = float(row[col["weight"]] or 0)
        name = norm(task)
        if EXAM_WORD in name:
            exam_w += w
            tasks.append({"task": task.strip(), "weight": w, "kind": "exam"})
            continue
        mark, out = row[col["mark"]], row[col["out of"]]
        if name in assume:
            frac, status = assume[name], "assumed"
        elif isinstance(mark, (int, float)) and isinstance(out, (int, float)) and out:
            frac, status = mark / out, "recorded"
        else:
            frac, status = None, "missing"
        tasks.append({"task": task.strip(), "weight": w, "kind": "coursework",
                      "fraction": frac, "status": status})
    banked = sum(t["weight"] * t["fraction"] for t in tasks
                 if t["kind"] == "coursework" and t["fraction"] is not None)
    cw_weight = sum(t["weight"] for t in tasks if t["kind"] == "coursework")
    return {"tasks": tasks, "exam_weight": exam_w, "banked": banked * 100,
            "coursework_weight": cw_weight * 100,
            "missing": [t["task"] for t in tasks if t.get("status") == "missing"],
            "assumed": [t["task"] for t in tasks if t.get("status") == "assumed"]}


def completed_eihwam(ws, level_weight: dict) -> tuple[float, float]:
    hrow, header = find_header(ws, "unit")
    i_cp, i_lv, i_mark = header.index("cp"), header.index("course lv"), header.index("mark")
    num = den = 0.0
    for row in ws.iter_rows(min_row=hrow + 1, values_only=True):
        cp, lv, mark = row[i_cp], row[i_lv], row[i_mark]
        if isinstance(mark, (int, float)) and cp and lv:
            w = level_weight[int(lv)] * cp
            num += w * mark
            den += w
    return num, den


def exam_needed(u: dict, unit_mark: float) -> float:
    return (unit_mark - u["banked"]) / u["exam_weight"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, help="override target EIHWAM")
    args = ap.parse_args()

    cfg = load_yaml(CONFIG / "targets.yaml")
    target = args.target or float(cfg["target_eihwam"])
    lw = {int(k): v for k, v in cfg["level_weight"].items()}
    wb = openpyxl.load_workbook(copy_locked(Path(expand(cfg["results_xlsx"]))), data_only=True)

    num, den = completed_eihwam(wb[cfg["results_total_sheet"]], lw)
    units = load_units()
    for u in units:
        u.update(read_unit_sheet(wb[u["results_sheet"]], u.get("assumptions")))
        u["w_cp"] = u["eihwam_weight"] * u["cp"]
    s2_den = sum(u["w_cp"] for u in units)
    tot_den = den + s2_den
    for u in units:
        # EIHWAM points gained per exam percentage point
        u["leverage"] = u["exam_weight"] * u["w_cp"] / tot_den

    # (A) Target reached at the end of this semester: uniform S2 unit mark needed
    m_after = (target * tot_den - num) / s2_den
    # (B) Target at graduation: final-year average needed for a given S2 exam average
    fy = cfg["final_year"]
    fy_den = fy["thesis_cp"] * fy["thesis_weight"] + fy["other_cp"] * fy["other_weight"]
    scenarios = []
    for e in (60, 65, 70, 75, 80, 85, 90):
        s2_num = sum(u["w_cp"] * min(100, u["banked"] + u["exam_weight"] * e) for u in units)
        after = (num + s2_num) / tot_den
        need_fy = (target * (tot_den + fy_den) - num - s2_num) / fy_den
        scenarios.append({"exam_avg": e, "eihwam_after_s2": after, "final_year_avg_needed": need_fy})

    out = {
        "generated": datetime.now(TZ).isoformat(timespec="seconds"),
        "target": target, "completed_eihwam": num / den, "completed_den": den,
        "s2_avg_needed_for_target_now": m_after, "scenarios": scenarios,
        "units": {u["code"]: {
            "banked": u["banked"], "exam_weight": u["exam_weight"], "leverage": u["leverage"],
            "exam_needed": {str(m): exam_needed(u, m) for m in (75, 80, 85, 90)},
            "exam_needed_target_now": exam_needed(u, m_after),
            "assumed": u["assumed"], "missing": u["missing"],
            "confidence": u.get("confidence", 3),
        } for u in units},
    }
    STATE.mkdir(exist_ok=True)
    (STATE / "marks.json").write_text(json.dumps(out, indent=2), "utf-8")

    md = render(out, units, target)
    dst = VAULT / "00_dashboard" / "marks.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(md, "utf-8")
    print(md)
    print(f"\n[written] {dst}\n[written] {STATE / 'marks.json'}")


def render(out: dict, units: list[dict], target: float) -> str:
    L = ["---", "source: generated", "generator: pipeline_academic/scripts/marks.py",
         f"updated: {out['generated'][:10]}", "---", "",
         f"# Marks: working back from EIHWAM {target:g}", "",
         "> [!info] Generated file. Re-run `python scripts/marks.py` after any new mark lands; don't edit here.",
         "",
         f"Completed EIHWAM (1000-level W=0): **{out['completed_eihwam']:.2f}** "
         f"(Σ W·cp = {out['completed_den']:g})", "",
         f"## A. Reach {target:g} at the end of this semester",
         f"Needs a weighted S2 unit average of **{out['s2_avg_needed_for_target_now']:.1f}**.", "",
         "| Unit | Exam | Exam weight | Coursework banked | Exam % for unit 75 | 80 | 85 | 90 | "
         f"Exam % for A ({out['s2_avg_needed_for_target_now']:.0f}) | EIHWAM per exam % |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for u in units:
        d = out["units"][u["code"]]
        flag = " †" if d["assumed"] or d["missing"] else ""
        cells = " | ".join(f"{d['exam_needed'][str(m)]:.0f}" for m in (75, 80, 85, 90))
        L.append(f"| {u['code']} | {u['exam']['start_dt']:%a %d %b %H:%M} | {d['exam_weight']*100:.0f}% | "
                 f"{d['banked']:.1f} / {u['coursework_weight']:.0f}{flag} | {cells} | "
                 f"**{d['exam_needed_target_now']:.0f}** | {d['leverage']:.3f} |")
    L += ["", "† includes assumed marks (from the unit's config `assumptions`), listed below.", ""]
    for u in units:
        d = out["units"][u["code"]]
        if d["assumed"] or d["missing"]:
            L.append(f"- **{u['code']}**: assumed {', '.join(d['assumed']) or '-'}; "
                     f"missing (counted as 0) {', '.join(d['missing']) or '-'}")
    L += ["", f"## B. Reach {target:g} at graduation",
          "Uses the `final_year` assumption in `config/targets.yaml` (verify against the handbook).", "",
          "| If S2 exams average | EIHWAM after S2 | Final-year average needed |", "|---|---|---|"]
    for s in out["scenarios"]:
        L.append(f"| {s['exam_avg']}% | {s['eihwam_after_s2']:.2f} | {s['final_year_avg_needed']:.1f} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
