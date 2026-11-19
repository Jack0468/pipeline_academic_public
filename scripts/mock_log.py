"""Generation log for every mock exam: which questions the coordinator sent back, why, and what replaced them.

gen_practice_exam.py calls write() after it saves a mock. Per mock, in state/mock_logs/<unit id>/:
    mock_exam_<n>.json   everything: run details, every model call (what, how long), every question sent back
                         (round, reason, the question and solution before and after, difficulty proxies),
                         the provenance of each final question, and what the solution check changed
    mock_exam_<n>.md     the same as a readable report
and across all mocks: state/mock_logs/index.md and index.csv (one row per mock).

The logs live in state/, not the vault: the reasons and solutions in them can give answers away. Read a
mock's log after sitting it.

Purpose: test whether replacement questions are harder than the ones they replace (the owner's hypothesis,
2026-11-16). The profile numbers (words, parts, givens, marking steps, equations) are proxies; the real
measure is your marks per question on that mock, which grade_practice.py now stores in
state/practice_check_data/ for later joining (question number = the part id's leading number).
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from common import STATE, TZ

LOGS = STATE / "mock_logs"
METRICS = ("stem_words", "parts", "subparts", "givens", "mark_steps", "display_equations", "solution_words")


def summary(res: dict) -> dict:
    calls = res.get("calls", [])
    abandoned = [h for h in res.get("history", []) if h.get("abandoned")]
    hist = [h for h in res.get("history", []) if not h.get("abandoned")]   # from the run that finished each section
    replaced = [h for h in hist if h["status"] == "replaced"]
    deltas = {}
    for m in METRICS:
        pairs = [(h["before"]["profile"] or {}).get(m) for h in replaced], [(h["after"]["profile"] or {}).get(m)
                                                                           for h in replaced]
        diffs = [b - a for a, b in zip(*pairs) if a is not None and b is not None]
        deltas[m] = round(sum(diffs) / len(diffs), 2) if diffs else None
    return dict(
        questions_sent_back=len({(h["section"], h["question"]) for h in hist}),
        sent_back_records=len(hist),
        replaced=len(replaced),
        unresolved=sum(h["status"] == "unresolved" for h in hist),
        rounds_used=max((h["round"] for h in hist), default=0),
        abandoned_records=len(abandoned),           # from a section a failed run never finished
        failed_calls=sum(not c["ok"] for c in calls),
        model_calls=len(calls),
        model_minutes=round(sum(c["seconds"] for c in calls) / 60, 1),
        mean_change_when_replaced=deltas,          # after minus before, averaged over replaced questions
        reasons=sorted({h["reason"].split(":")[0].split(" (")[0] for h in hist}),
    )


def _fmt(p: dict | None, m: str) -> str:
    return "-" if not p or p.get(m) is None else f"{p[m]:g}"


def render(data: dict) -> str:
    s = data["summary"]
    L = [f"# {data['unit']} mock exam {data['mock']}: generation log", "",
         "> [!warning] Contains answers (reasons, solutions). Read it after sitting the mock.", "",
         f"- Generated {data['generated']} with {data['model']}; seed {data['seed']}; research {data['research']}",
         f"- {s['model_calls']} model calls ({s['failed_calls']} failed), {s['model_minutes']} min of model time; "
         f"{s['questions_sent_back']} question(s) sent back ({s['sent_back_records']} times), {s['replaced']} replaced, "
         f"{s['unresolved']} unresolved; rounds used: {s['rounds_used']}",
         f"- Solution check: {data['verify']['status']}; changed {len(data['verify']['changed'])} solution(s)"
         + (f" ({', '.join(f'Q{k}' for k in data['verify']['changed'])})" if data['verify']['changed'] else "")
         + (f"; correction notes on {', '.join(f'Q{k}' for k in data['verify']['corrected'])}"
            if data['verify']['corrected'] else ""), ""]
    if data["history"]:
        L += ["## Sent back", "",
              "Profile columns are before -> after (stem words, givens, marking steps, display equations).", "",
              "| Round | Q | Section | Reason | Outcome | Stem words | Givens | Steps | Equations |",
              "|---|---|---|---|---|---|---|---|---|"]
        for h in data["history"]:
            b, a = (h["before"] or {}).get("profile"), (h["after"] or {}).get("profile")
            cell = lambda m: f"{_fmt(b, m)} -> {_fmt(a, m)}" if a else _fmt(b, m)
            status = h["status"] + (" (abandoned: the run failed and the section was rewritten)" if h.get("abandoned") else "")
            L.append(f"| {h['round']} | {h['question']} | {h['section'] or 'paper'} | {h['reason'].replace('|', '/')} | "
                     f"{status} | {cell('stem_words')} | {cell('givens')} | {cell('mark_steps')} | "
                     f"{cell('display_equations')} |")
        d = s["mean_change_when_replaced"]
        L += ["", "Mean change when replaced (after minus before): "
              + ", ".join(f"{m.replace('_', ' ')} {v:+g}" for m, v in d.items() if v is not None) if s["replaced"]
              else "", "", "## Before and after", ""]
        for h in data["history"]:
            L += [f"### Round {h['round']}, Question {h['question']}: {h['status']}", "", f"Reason: {h['reason']}", "",
                  "**Before**", "", (h["before"] or {}).get("question") or "(missing)", ""]
            if h.get("after"):
                L += ["**After**", "", h["after"]["question"], ""]
    L += ["## Every question in the paper", "", "| Q | Section | Origin | Times sent back | Solution changed by check |",
          "|---|---|---|---|---|"]
    for k, p in sorted(data["provenance"].items(), key=lambda kv: int(kv[0])):
        L.append(f"| {k} | {p['section'] or 'paper'} | {p['status']} | {p['times_sent_back']} | "
                 f"{'yes' if int(k) in data['verify']['changed'] else ''} |")
    L += ["", "## Model calls", "", "| What | Section | Round | Seconds | OK |", "|---|---|---|---|---|"]
    L += [f"| {c['what']} | {c['section'] or 'paper'} | {c['round']} | {c['seconds']:g} | {'yes' if c['ok'] else 'no'} |"
          for c in data["calls"]]
    return "\n".join(L) + "\n"


def write_index() -> Path:
    rows = []
    for p in sorted(LOGS.glob("*/mock_exam_*.json")):
        d = json.loads(p.read_text("utf-8"))
        s = d["summary"]
        rows.append(dict(unit=d["unit"], mock=d["mock"], generated=d["generated"], model=d["model"],
                         sent_back=s["questions_sent_back"], replaced=s["replaced"], unresolved=s["unresolved"],
                         rounds=s["rounds_used"], calls=s["model_calls"], model_minutes=s["model_minutes"],
                         solutions_changed_by_check=len(d["verify"]["changed"]),
                         **{f"d_{m}": s["mean_change_when_replaced"].get(m) for m in METRICS},
                         log=str(p.with_suffix(".md").relative_to(LOGS))))
    rows.sort(key=lambda r: (r["unit"], r["mock"]))
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "index.csv", "w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    L = ["# Mock generation logs", "", "One row per mock. d_* = mean change (after minus before) over replaced "
         "questions. Join with your marks per question (state/practice_check_data/) to test whether "
         "replacements are harder.", "",
         "| Unit | Mock | Generated | Sent back | Replaced | Unresolved | Rounds | Calls | Model min | Check changed | "
         "d stem words | d steps | Log |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['unit']} | {r['mock']} | {r['generated'][:16]} | {r['sent_back']} | {r['replaced']} | "
                 f"{r['unresolved']} | {r['rounds']} | {r['calls']} | {r['model_minutes']} | "
                 f"{r['solutions_changed_by_check']} | {r['d_stem_words'] if r['d_stem_words'] is not None else '-'} | "
                 f"{r['d_mark_steps'] if r['d_mark_steps'] is not None else '-'} | {r['log']} |")
    out = LOGS / "index.md"
    out.write_text("\n".join(L) + "\n", "utf-8")
    return out


def write(u: dict, n: int, res: dict, meta: dict, verify: dict, issues: list[str]) -> Path:
    """Save one mock's generation log (JSON + Markdown) and refresh the index. Returns the Markdown path."""
    d = LOGS / u["id"]
    d.mkdir(parents=True, exist_ok=True)
    data = dict(unit=u["code"], unit_id=u["id"], mock=n, generated=datetime.now(TZ).isoformat(timespec="seconds"),
                **meta, sections=[dict(id=x["sec"].get("id", ""), kind=x["sec"]["kind"], count=x["sec"].get("count"),
                                       marks=x["sec"]["marks"]) for x in res["sections"]],
                key=res.get("key", ""), open_issues=issues, verify=verify, summary=summary(res),
                calls=res.get("calls", []), history=res.get("history", []),
                provenance={str(k): v for k, v in res.get("provenance", {}).items()})
    (d / f"mock_exam_{n}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), "utf-8")
    md = d / f"mock_exam_{n}.md"
    md.write_text(render(data), "utf-8")
    write_index()
    return md
