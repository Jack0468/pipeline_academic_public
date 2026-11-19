"""Turn exam dates + required marks into concrete study blocks and an importable calendar.

Inputs : config/study_plan.yaml, config/units/*.yaml, state/marks.json (run marks.py first)
Outputs: out/calendar/study_plan.ics (blocks + exams), out/calendar/exams.ics (exams only),
         <vault>/00_dashboard/study_plan.md

Allocation: every free block goes to the unit with the largest share of its remaining quota
still to place before its exam ("remaining quota / remaining slots before exam"). Units interleave
instead of being crammed one at a time. Blocks within `lock_hours_before_exam` go only to that
exam. Quota share ∝ EIHWAM leverage × exam mark needed × (6 - confidence).

Usage:
    python scripts/plan_study.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import progress
from common import CALENDAR_DIR, CONFIG, EXAMS_ICS, STATE, STUDY_ICS, TZ, VAULT, find_sources, lecture_labels, load_units, load_yaml, unit_plan

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=TZ)


def exam_window(u):
    s = u["exam"]["start_dt"]
    return s, s + timedelta(minutes=u["exam"]["reading_min"] + u["exam"]["writing_min"])


def gcal_busy() -> list[tuple[datetime, datetime]]:
    """Busy intervals pulled from Google Calendar by `gcal_sync.py busy` (state/gcal_busy.json), if any."""
    p = STATE / "gcal_busy.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text("utf-8"))
    if data.get("fetched"):
        age_h = (datetime.now(TZ) - datetime.fromisoformat(data["fetched"])).total_seconds() / 3600
        if age_h > 24:
            print(f"  ! busy times are {age_h / 24:.1f} days old (Google login expired?): events added since then "
                  "are not avoided. Log in again: python scripts\\gcal_sync.py auth --discover --events", file=sys.stderr)
    max_h = float(load_yaml(CONFIG / "study_plan.yaml").get("max_busy_hours", 12))
    out = []
    for b in data.get("busy", []):
        s, e = datetime.fromisoformat(b["start"]).astimezone(TZ), datetime.fromisoformat(b["end"]).astimezone(TZ)
        if (e - s).total_seconds() / 3600 > max_h:           # a marker (or merged free/busy block), not a commitment
            print(f"  ! ignoring {(e - s).total_seconds() / 3600:.0f} h busy block {s:%a %d %b %H:%M} -> {e:%a %d %b %H:%M} "
                  f"({b.get('calendar')}). If real events hide inside it, log in with --events (docs/GCAL_SETUP.md)",
                  file=sys.stderr)
            continue
        out.append((s, e))
    return out


def lecture_label(p: Path) -> str:
    return p.stem.replace("___", " ").replace("_", " ").strip()


REVIEW_TITLE = "Spaced review (all units)"


def ongoing(now: datetime) -> tuple[list[dict], list[dict]]:
    """Blocks of the previous plan that are under way right now (start < now < end). They are kept exactly as
    they were, so re-planning mid-block doesn't delete (or retitle) the block you are sitting in."""
    p = STUDY_ICS
    if not p.exists():
        return [], []
    from gcal_sync import parse_ics
    t = lambda v: datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(TZ)
    blocks, reviews = [], []
    for e in parse_ics(p):
        if "DTSTART" not in e or "DTEND" not in e or e.get("SUMMARY", "").startswith("EXAM"):
            continue
        s, end = t(e["DTSTART"]), t(e["DTEND"])
        if not s < now < end:
            continue
        if e.get("SUMMARY") == REVIEW_TITLE:
            reviews.append({"start": s, "end": end})
        else:
            code = re.search(r"\b[A-Z]{4}\d{4}\b", e.get("SUMMARY", ""))
            blocks.append({"start": s, "end": end, "title": e.get("SUMMARY", ""), "desc": e.get("DESCRIPTION", ""),
                           "unit": code.group(0) if code else None, "phase": "in progress", "moved": False})
    return blocks, reviews


def build_slots(cfg, units, now: datetime, under_way: list[dict] = ()):
    """Free blocks from *now* (rolling re-plan: missed blocks simply drop off) to the last exam.

    - `under_way`: blocks already under way (see ongoing()); new blocks stay clear of them, and they count
      towards that day's number of blocks.

    - Busy events (config `busy:` + Google Calendar) are padded by `busy_buffer_min` on both sides.
    - Study happens only between wake + morning_buffer and bed - wind_down (`sleep:`). After a late
      event (ending past bedtime) the next day starts no earlier than its end + min_sleep_hours.
    - Preferred block times come from `blocks:`. With `reflow: true`, a block knocked out by an event
      is moved to another free gap the same day (keeping `break_min` between blocks), so the day
      still gets its usual number of blocks where possible.
    """
    last = max(exam_window(u)[0] for u in units)
    d = max(date.fromisoformat(str(cfg["start_date"])), now.date())
    raw = [(datetime.fromisoformat(str(b["start"])).replace(tzinfo=TZ),
            datetime.fromisoformat(str(b["end"])).replace(tzinfo=TZ)) for b in cfg.get("busy") or []]
    raw += gcal_busy()
    pad = timedelta(minutes=cfg.get("busy_buffer_min", 30))
    bb, ba = timedelta(minutes=cfg["exam_buffer_before_min"]), timedelta(minutes=cfg["exam_buffer_after_min"])
    brk = timedelta(minutes=cfg.get("break_min", 30))
    blocked = [(s - pad, e + pad) for s, e in raw] + [(s - bb, e + ba) for s, e in map(exam_window, units)]
    blocked += [(k["start"] - brk, k["end"] + brk) for k in under_way]
    overlaps = lambda s, e: any(s < be and e > bs for bs, be in blocked)

    sl = cfg.get("sleep") or {}
    wake_t, bed_t = hm(sl.get("wake", "07:30")), hm(sl.get("bed", "23:30"))
    min_sleep = timedelta(hours=sl.get("min_sleep_hours", 8))
    morning, wind = timedelta(minutes=sl.get("morning_buffer_min", 60)), timedelta(minutes=sl.get("wind_down_min", 60))
    reflow_len = timedelta(minutes=cfg.get("reflow_block_min", 90))
    late_events = [e for s, e in raw] + [e for s, e in map(exam_window, units)]

    def bed(day):                        # bedtime belonging to `day` (after midnight → next date)
        return at(day + timedelta(days=1) if bed_t <= wake_t else day, bed_t)

    slots, reviews = [], []
    while d <= last.date():
        wake = at(d, wake_t)
        for e in late_events:            # late night yesterday → sleep in today
            if bed(d - timedelta(days=1)) < e < wake + timedelta(hours=4):
                wake = max(wake, e + min_sleep)
        day_start, day_end = wake + morning, bed(d) - wind
        keep = cfg.get("day_overrides", {}).get(WEEKDAYS[d.weekday()])
        prefs = [(at(d, hm(a)), at(d, hm(b))) for i, (a, b) in enumerate(cfg["blocks"]) if keep is None or i in keep]
        target = min(len(prefs), cfg.get("max_blocks_per_day", len(prefs)))
        target -= sum(1 for k in under_way if k["start"].date() == d and k.get("title"))    # blocks under way today

        def fits(s, e, chosen):
            return (s >= now and s >= day_start and e <= day_end and e <= last and not overlaps(s, e)
                    and all(e + brk <= cs or s >= ce + brk for cs, ce in chosen))

        def pack(chosen):                # greedily fill free gaps with reflow-length blocks
            t = max(day_start, now)
            if t.second or t.microsecond:                        # events can end at odd seconds
                t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
            t = t + timedelta(minutes=(-t.minute) % 15)          # quarter-hour starts
            while t + reflow_len <= day_end and len(chosen) < target:
                if fits(t, t + reflow_len, chosen):
                    chosen.append((t, t + reflow_len))
                    t += reflow_len + brk
                else:
                    t += timedelta(minutes=15)
            return chosen

        chosen = []
        for s, e in prefs:               # A: preferred times, then reflow into gaps
            if len(chosen) < target and fits(s, e, chosen):
                chosen.append((s, e))
        if cfg.get("reflow", True) and len(chosen) < target:
            chosen = pack(chosen)
            alt = pack([])               # B: busy day: pack from the start of the day instead
            if len(alt) > len(chosen):
                chosen = alt
        free = lambda s, e: fits(s, e, chosen)
        slots += [{"start": s, "end": e, "moved": (s, e) not in prefs} for s, e in sorted(chosen)]
        if cfg.get("review_block"):
            s, e = at(d, hm(cfg["review_block"][0])), at(d, hm(cfg["review_block"][1]))
            if free(s, e):
                reviews.append({"start": s, "end": e})
        d += timedelta(days=1)
    return slots, reviews


def uncovered(u, prog) -> list[Path]:
    """Lecture PDFs not yet ticked in progress.md, in lecture order."""
    lec = find_sources(u, "lectures")
    labels = lecture_labels(lec)
    done = prog.get(u["code"], {}).get("covered", set())
    return [p for p in lec if labels[p] not in done]


def allocate(slots, units, marks, cfg, prog):
    lock = timedelta(hours=cfg["lock_hours_before_exam"])
    weight = {}
    for u in units:
        m = marks["units"][u["code"]]
        need = max(m["exam_needed_target_now"], 50) / 100
        n_lec = max(len(find_sources(u, "lectures")), 1)
        backlog = 0.5 + 0.5 * len(uncovered(u, prog)) / n_lec      # 1.0 = nothing covered, 0.5 = all covered
        weight[u["code"]] = m["leverage"] * need * (6 - int(m["confidence"])) * backlog
    tot = sum(weight.values())
    quota = {c: len(slots) * w / tot for c, w in weight.items()}
    start = {u["code"]: exam_window(u)[0] for u in units}

    # 1. locked slots: the run-up to each exam belongs to that exam
    for s in slots:
        upcoming = [u for u in units if s["end"] <= start[u["code"]] and start[u["code"]] - s["start"] <= lock]
        if upcoming:
            s["unit"], s["locked"] = upcoming[0]["code"], True
    used = Counter(s["unit"] for s in slots if s.get("unit"))

    # 2. free slots: largest (remaining quota / remaining free slots before its exam)
    prev = None
    for i, s in enumerate(slots):
        if s.get("unit"):
            prev = s["unit"]
            continue
        cands = [u["code"] for u in units if s["end"] <= start[u["code"]]]

        def score(c):
            avail = sum(1 for t in slots[i:] if not t.get("unit") and t["end"] <= start[c])
            return ((quota[c] - used[c]) / max(avail, 1), c != prev, -start[c].timestamp())

        c = max(cands, key=score)
        s["unit"], used[c], prev = c, used[c] + 1, c
    return quota, weight


def label_phases(slots, units, cfg, prog):
    """Coverage blocks only for lectures not yet ticked; triage notes when they don't fit."""
    ph = cfg["phases"]
    lpb = cfg.get("lectures_per_block", 2)
    by_unit = defaultdict(list)
    for s in slots:
        by_unit[s["unit"]].append(s)
    lectures = {u["code"]: uncovered(u, prog) for u in units}
    info = {u["code"]: u for u in units}
    triage = {}
    for code, ss in by_unit.items():
        n = len(ss)
        lec = lectures[code]
        n_sim = max(1, round(n * ph["simulate"])) if n > 1 else 0
        cap = max(1, round(n * ph.get("coverage_max", 0.6)))       # leave room for practice + mocks
        n_cov = min(-(-len(lec) // lpb), cap, max(n - n_sim, 0)) if lec else 0
        if lec and len(lec) > n_cov * lpb:
            labels = lecture_labels(find_sources(info[code], "lectures"))
            triage[code] = (len(lec), n_cov, [labels[p] for p in lec])
        for j, s in enumerate(ss):
            u = info[code]
            sheet = u["exam"].get("notes_sheet", "")
            if s.get("locked") and j == n - 1:
                s["phase"], s["title"] = "final", f"{code} final review"
                s["desc"] = (f"Light review only: error_log.md, your {sheet}, logistics "
                             f"(venue {u['exam']['venue']}, seat {u['exam'].get('seat')}, calculator).")
            elif j < n_cov and lec:
                a, b = j * len(lec) // n_cov, max((j + 1) * len(lec) // n_cov, j * len(lec) // n_cov + 1)
                labels = lecture_labels(find_sources(u, "lectures"))     # full list keeps L6a/L6b stable
                names = [labels[p] for p in lec[a:b]]
                s["phase"], s["title"] = "coverage", f"{code} cover: {', '.join(names)}"
                s["lectures"] = names
                s["desc"] = ("1) Blurt: closed-book, answer the recall prompts from memory on paper (numbered like the prompts); scan to summary_recall/recall_<L>.pdf so grade_recall.py checks it. "
                             "2) Open the slides, fix gaps in a different colour. "
                             "3) Answer the recall questions in units/{id}/recall/. "
                             "4) 1-2 related tutorial problems using the hint ladder. "
                             "5) Log misses in practice/error_log.md. "
                             "6) Tick the lectures in 00_dashboard/progress.md.").replace("{id}", u["id"])
            elif j >= n - n_sim:
                s["phase"], s["title"] = "simulate", f"{code} exam simulation"
                s["desc"] = (f"Full mock exam (practice/mock_exam_<n>) or 2 h mixed set under exam conditions ({u['exam']['conditions']}; "
                             f"only {', '.join(u['exam']['materials'])}). Mark it, log errors, "
                             f"then update your {sheet}.")
            else:
                s["phase"], s["title"] = "practice", f"{code} mixed timed problems"
                s["desc"] = ("Interleaved problems across topics (tutorials, quizzes, mock exams). "
                             "~1.2 min per mark. Every miss goes in error_log.md; add one item to your "
                             f"{sheet} draft. Then +1 practice_blocks_done in progress.md.")
    return triage


# ---------- output ----------

def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line: str) -> str:
    out, b = [], line.encode("utf-8")
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:      # don't split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def vevent(uid, start, end, summary, desc="", location="", alarms=()):
    L = ["BEGIN:VEVENT", f"UID:{uid}@pipeline-academic", f"DTSTAMP:{utc(datetime.now(TZ))}",
         f"DTSTART:{utc(start)}", f"DTEND:{utc(end)}", f"SUMMARY:{ics_escape(summary)}"]
    if desc:
        L.append(f"DESCRIPTION:{ics_escape(desc)}")
    if location:
        L.append(f"LOCATION:{ics_escape(location)}")
    for minutes in alarms:
        L += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{ics_escape(summary)}",
              f"TRIGGER:-PT{minutes}M", "END:VALARM"]
    L.append("END:VEVENT")
    return L


def calendar(events):
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//pipeline_academic//study plan//EN",
         "CALSCALE:GREGORIAN", "X-WR-CALNAME:Exam study 2026 S2"]
    for e in events:
        L += e
    L.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(l) for l in L) + "\r\n"


def uid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def exam_events(units):
    ev = []
    for u in units:
        s, e = exam_window(u)
        x = u["exam"]
        desc = (f"Seat {x.get('seat')}. {x['conditions']}. Materials: {'; '.join(x['materials'])}. "
                f"Reading {x['reading_min']} min + writing {x['writing_min']} min. Map: {x.get('map', '')}")
        ev.append(vevent(uid("exam", u["code"]), s, e, f"EXAM {u['code']} {u['name']}", desc,
                         x["venue"], alarms=(24 * 60, 120)))
    return ev


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_yaml(CONFIG / "study_plan.yaml")
    mpath = STATE / "marks.json"
    if not mpath.exists():
        sys.exit("state/marks.json missing: run scripts/marks.py first")
    marks = json.loads(mpath.read_text("utf-8"))
    units = load_units()

    now = datetime.now(TZ)
    prog = progress.read(units)
    on_blocks, on_reviews = ongoing(now)
    slots, reviews = build_slots(cfg, units, now, on_blocks + on_reviews)
    quota, weight = allocate(slots, units, marks, cfg, prog)
    triage = label_phases(slots, units, cfg, prog)
    slots = sorted(slots + on_blocks, key=lambda s: s["start"])        # same UID, title and text: unchanged in Calendar
    reviews = sorted(reviews + on_reviews, key=lambda r: r["start"])

    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    ev = exam_events(units)
    EXAMS_ICS.write_text(calendar(ev), "utf-8", newline="")
    for s in slots:
        ev.append(vevent(uid("blk", s["start"]), s["start"], s["end"], s["title"], s["desc"]))
    for r in reviews:
        ev.append(vevent(uid("rev", r["start"]), r["start"], r["end"], REVIEW_TITLE,
                         "Redo the Re-test items in 00_dashboard/recall_checks + recall questions you missed in the last 3 days + re-attempt error_log items "
                         "without looking. 45 min max."))
    STUDY_ICS.write_text(calendar(ev), "utf-8", newline="")

    md = render(slots, reviews, units, quota, weight, marks, prog, triage, now)
    dst = VAULT / "00_dashboard" / "study_plan.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(md, "utf-8")
    print(md)
    print(f"[written] {STUDY_ICS}\n[written] {EXAMS_ICS}\n[written] {dst}")


def unit_plans(units, now) -> list[str]:
    """Each upcoming unit's exam plan: a link plus a copy of its Summary. The plans themselves (units/<id>/exam_plan.md)
    are written by you and Claude and never touched by scripts; this file is regenerated, so nothing is lost here."""
    L = ["## Unit plans",
         "_Copied on every refresh from each unit's `exam_plan.md` (its Summary section). Edit the unit plan, not this "
         "file. Where a unit plan gives a block-by-block order, it wins over the block titles below and in the "
         "calendar; the times stay._", ""]
    for u in units:
        if exam_window(u)[1] < now:
            continue
        p, summary = unit_plan(u)
        link = f"[[units/{u['id']}/exam_plan|{u['code']} exam plan]]"
        if not p.exists():
            L += [f"### {u['code']}", "No plan yet: run `scripts\\scaffold_vault.py` for a starter, then fill it in.", ""]
            continue
        L += [f"### {u['code']}: {link}", summary or "_(the plan has no Summary section)_", ""]
    return L


def render(slots, reviews, units, quota, weight, marks, prog, triage, now):
    count = Counter(s["unit"] for s in slots)
    L = ["---", "source: generated", "generator: pipeline_academic/scripts/plan_study.py",
         f"updated: {now:%Y-%m-%d %H:%M}", "---", "", "# Study plan from now to the end of exams", "",
         f"> [!info] Re-planned from **{now:%a %d %b %H:%M}**. Missed blocks drop off and the remaining work is "
         "re-spread. Tick progress in [[00_dashboard/progress|Progress check-in]], then run "
         "`scripts\\refresh.ps1` (also updates Google Calendar).", "",
         "| Unit | Exam | Exam % needed | Confidence | Lectures covered | Practice / mocks done | Blocks left | Share |",
         "|---|---|---|---|---|---|---|---|"]
    tot = sum(weight.values())
    for u in units:
        m = marks["units"][u["code"]]
        p = prog.get(u["code"], {})
        n_lec = len(find_sources(u, "lectures"))
        L.append(f"| {u['code']} | {u['exam']['start_dt']:%a %d %b %H:%M} | {m['exam_needed_target_now']:.0f} | "
                 f"{m['confidence']} | {n_lec - len(uncovered(u, prog))}/{n_lec} | "
                 f"{p.get('practice', 0)} / {p.get('mocks', 0)} | {count[u['code']]} | {weight[u['code']] / tot:.0%} |")
    L += ["", f"{len(slots)} focus blocks + {len(reviews)} evening spaced-review blocks left.", ""]
    if triage:
        L += ["> [!warning] Not enough time to cover every lecture properly",
              "> Coverage is compressed for these units. Skim the listed lectures in exam-scope order, and lean "
              "on recall questions + practice instead of full re-reads:"]
        for code, (n_left, n_cov, names) in triage.items():
            L.append(f"> - **{code}**: {n_left} lectures left, {n_cov} coverage block(s) "
                     f"(~{-(-n_left // max(n_cov, 1))} per block): {', '.join(names)}")
        L.append("")
    L += unit_plans(units, now)
    exams = {exam_window(u)[0].date(): u for u in units}
    by_day = defaultdict(list)
    for s in slots:
        by_day[s["start"].date()].append(s)
    for d in sorted(set(by_day) | set(exams)):
        head = f"## {d:%Y-%m-%d} ({WEEKDAYS[d.weekday()]})"
        if d in exams:
            u = exams[d]
            head += f": **EXAM {u['code']} {u['exam']['start_dt']:%H:%M}**, {u['exam']['venue']}, seat {u['exam'].get('seat')}"
        L.append(head)
        for s in by_day.get(d, []):
            lock = (" (locked)" if s.get("locked") else "") + (" (moved: busy)" if s.get("moved") else "")
            L.append(f"- {s['start']:%H:%M}-{s['end']:%H:%M} **{s['title']}**{lock} · _{s['phase']}_")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
