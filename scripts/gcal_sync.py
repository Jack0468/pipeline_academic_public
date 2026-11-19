"""Two-way Google Calendar link for the study plan.

    python scripts/gcal_sync.py auth [--discover]                 # main account (owns the study calendar)
    python scripts/gcal_sync.py auth --account gmail --discover   # extra account: its calendars count as busy
    python scripts/gcal_sync.py busy            # your busy times → state/gcal_busy.json → plan_study.py avoids them
    python scripts/gcal_sync.py push [--dry-run]  # out/calendar/study_plan.ics → "Exam study (pipeline)" calendar
    python scripts/gcal_sync.py status

Permissions are deliberately narrow:
  calendar.app.created  create one secondary calendar and manage only the events in it
  calendar.freebusy     see when you're busy (not what the events are) on GCAL_BUSY_CALENDARS
It can't read, change or delete anything in your other calendars.

push is idempotent: event IDs are derived from the planner's stable UIDs, changed blocks are updated
in place, and future blocks that disappeared from the plan are deleted. Blocks that have already ended
are left alone (a record of what was planned). Nothing is duplicated on re-runs.
Setup (OAuth client, once): docs/GCAL_SETUP.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import REPO, STATE, STUDY_ICS, TZ, load_units

SCOPES = ["https://www.googleapis.com/auth/calendar.app.created",
          "https://www.googleapis.com/auth/calendar.freebusy"]
# Optional (auth --discover): list the calendars shared with this account, so busy times come from
# all of them (other accounts' calendars, work, uni) without naming each one in GCAL_BUSY_CALENDARS.
LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
# Optional (auth --events): read individual events (read-only). Free/busy merges overlapping events,
# so one long "marker" event (e.g. a week-long "study period") hides real events inside it. With
# this scope each event is judged on its own: markers are skipped and real events kept, with titles.
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
LONG_H = float(os.environ.get("GCAL_LONG_EVENT_HOURS", "12"))     # longer than this = marker, not busy
SECRETS = REPO / ".secrets"
CLIENT = SECRETS / "google_oauth_client.json"
TOKEN = SECRETS / "gcal_token.json"
CAL_STATE = STATE / "gcal.json"
ICS = STUDY_ICS
CAL_NAME = os.environ.get("GCAL_CALENDAR_NAME", "Exam study (pipeline)")
BUSY_CALS = [c.strip() for c in os.environ.get("GCAL_BUSY_CALENDARS", "primary").split(",") if c.strip()]
UNIT_COLOURS = ["9", "10", "6", "3", "5", "7"]     # Google event colour IDs, assigned in exam order
EXAM_COLOUR = "11"                                  # tomato red


_scopes: list[str] = []
SKIP = ("#holiday@", "#contacts@", "#weather@", "addressbook#")


def token_path(account: str | None = None) -> Path:
    """The main account (owns the study calendar) uses gcal_token.json; extra accounts gcal_token_<label>.json."""
    return TOKEN if not account else SECRETS / f"gcal_token_{account}.json"


def extra_accounts() -> list[str]:
    return sorted(p.stem.removeprefix("gcal_token_") for p in SECRETS.glob("gcal_token_*.json"))


def service(interactive: bool = False, discover: bool = False, account: str | None = None, events: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    tok = token_path(account)
    want = ((SCOPES if not account else [SCOPES[1]]) + ([LIST_SCOPE] if discover else [])
            + ([EVENTS_SCOPE] if events else []))
    creds = Credentials.from_authorized_user_file(str(tok)) if tok.exists() else None   # keeps granted scopes
    if interactive and creds and not set(want) <= set(creds.scopes or []):
        creds = None                                     # asked for a new scope: log in again
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not interactive:
            sys.exit(f"Not logged in ({account or 'main'} account): run  python scripts/gcal_sync.py auth"
                     + (f" --account {account}" if account else "") + "  (see docs/GCAL_SETUP.md)")
        if not CLIENT.exists():
            sys.exit(f"Missing OAuth client file {CLIENT} (see docs/GCAL_SETUP.md)")
        from google_auth_oauthlib.flow import InstalledAppFlow
        creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT), want).run_local_server(
            port=0, prompt="select_account consent")     # always show the account picker
    SECRETS.mkdir(exist_ok=True)
    tok.write_text(creds.to_json(), "utf-8")
    _scopes[:] = list(creds.scopes or [])
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def calendar_list(svc) -> list[dict]:
    """Every calendar this account shows (its own + ones shared with it). Needs auth --discover."""
    if LIST_SCOPE not in _scopes:
        return []
    out, token = [], None
    while True:
        page = svc.calendarList().list(pageToken=token, showHidden=True).execute()
        out += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            break
    return out


def busy_calendars(svc, extra: list[str]) -> dict[str, str]:
    """{calendar id: display name} to treat as busy for one account: `extra` + primary + (with
    --discover) every calendar in its list, including ones hidden in the UI. Our study calendar,
    holidays, birthdays and weather are skipped. Exclude one with GCAL_IGNORE_CALENDARS."""
    own = load_state().get("calendar_id")
    ignore = {c.strip() for c in os.environ.get("GCAL_IGNORE_CALENDARS", "").split(",") if c.strip()}
    ids = {c: c for c in extra + ["primary"]}
    for c in calendar_list(svc):
        ids[c["id"]] = c.get("summaryOverride") or c.get("summary") or c["id"]
        if c.get("primary"):
            ids.pop("primary", None)
            ids[c["id"]] = f"{c.get('summary', c['id'])} (main)"
    return {c: n for c, n in ids.items()
            if c != own and c not in ignore and n not in ignore and not any(s in c for s in SKIP)}


def load_state() -> dict:
    return json.loads(CAL_STATE.read_text("utf-8")) if CAL_STATE.exists() else {}


def save_state(s: dict) -> None:
    CAL_STATE.parent.mkdir(exist_ok=True)
    CAL_STATE.write_text(json.dumps(s, indent=2), "utf-8")


def ensure_calendar(svc, state: dict) -> str:
    cid = state.get("calendar_id")
    if cid:
        try:
            svc.calendars().get(calendarId=cid).execute()
            return cid
        except Exception:
            print("  stored calendar not found; creating a new one")
    cal = svc.calendars().insert(body={"summary": CAL_NAME, "timeZone": "Australia/Sydney",
                                       "description": "Generated by pipeline_academic (plan_study.py). "
                                                      "Edits here are overwritten on the next push."}).execute()
    state["calendar_id"] = cal["id"]
    state["events"] = {}
    save_state(state)
    print(f"  created calendar '{CAL_NAME}'")
    return cal["id"]


def parse_ics(path: Path) -> list[dict]:
    raw = path.read_text("utf-8").replace("\r\n", "\n")
    lines = re.sub(r"\n[ \t]", "", raw).split("\n")              # unfold continuation lines
    unesc = lambda v: v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    events, ev, in_alarm = [], None, False
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            ev, in_alarm = {"alarms": []}, False
        elif ln == "END:VEVENT":
            events.append(ev)
            ev = None
        elif ev is not None:
            if ln == "BEGIN:VALARM":
                in_alarm = True
            elif ln == "END:VALARM":
                in_alarm = False
            elif ":" in ln:
                k, v = ln.split(":", 1)
                k = k.split(";")[0]
                if in_alarm:
                    m = re.match(r"-PT(\d+)M", v) if k == "TRIGGER" else None
                    if m:
                        ev["alarms"].append(int(m.group(1)))
                elif k in ("UID", "DTSTART", "DTEND", "SUMMARY", "DESCRIPTION", "LOCATION"):
                    ev[k] = unesc(v)
    return events


def ics_time(v: str) -> str:
    return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()


def desired_events() -> dict[str, dict]:
    colours = {u["code"]: UNIT_COLOURS[i % len(UNIT_COLOURS)] for i, u in enumerate(load_units())}
    out = {}
    for e in parse_ics(ICS):
        code = re.search(r"\b[A-Z]{4}\d{4}\b", e.get("SUMMARY", ""))
        colour = EXAM_COLOUR if e.get("SUMMARY", "").startswith("EXAM") else colours.get(code.group(0) if code else "", "8")
        eid = hashlib.sha1(e["UID"].encode()).hexdigest()           # base32hex-safe, stable per block
        body = {
            "id": eid, "summary": e.get("SUMMARY", ""), "description": e.get("DESCRIPTION", ""),
            "location": e.get("LOCATION", ""), "colorId": colour,
            "start": {"dateTime": ics_time(e["DTSTART"]), "timeZone": "Australia/Sydney"},
            "end": {"dateTime": ics_time(e["DTEND"]), "timeZone": "Australia/Sydney"},
            "reminders": {"useDefault": False,
                          "overrides": [{"method": "popup", "minutes": m} for m in e["alarms"][:5]]},
            "extendedProperties": {"private": {"pipeline_uid": e["UID"]}},
        }
        out[eid] = body
    return out


def cmd_push(dry: bool) -> None:
    if not ICS.exists():
        sys.exit(f"{ICS} not found: run plan_study.py (or refresh.ps1) first")
    want = desired_events()
    digest = {eid: hashlib.sha1(json.dumps(b, sort_keys=True).encode()).hexdigest() for eid, b in want.items()}
    svc = service()
    state = load_state()
    cid = ensure_calendar(svc, state)
    have, token = set(), None
    since = datetime.now(TZ).isoformat(timespec="seconds")   # only events not yet over: past blocks stay as history
    while True:
        page = svc.events().list(calendarId=cid, pageToken=token, maxResults=2500, showDeleted=False,
                                 timeMin=since).execute()
        have |= {it["id"] for it in page.get("items", [])}
        token = page.get("nextPageToken")
        if not token:
            break
    known = state.setdefault("events", {})
    add = [e for e in want if e not in have]
    upd = [e for e in want if e in have and known.get(e) != digest[e]]
    rem = [e for e in have if e not in want]
    print(f"  {len(want)} planned: {len(add)} to add, {len(upd)} to update, {len(rem)} to delete, "
          f"{len(want) - len(add) - len(upd)} unchanged")
    if dry:
        return
    for eid in add:
        try:
            svc.events().insert(calendarId=cid, body=want[eid]).execute()
        except Exception as ex:                                     # id used before (deleted event): revive it
            if "requiredAccessLevel" in str(ex) or "writer access" in str(ex):
                sys.exit("  FAIL The main login can't write to the study calendar: it was created by another Google "
                         "account. Run  gcal_sync.py auth --discover  and pick the account that owns "
                         f"'{CAL_NAME}' (log other accounts in with --account <label>). Nothing was changed.")
            if "409" not in str(ex) and "already exists" not in str(ex).lower():
                raise
            svc.events().update(calendarId=cid, eventId=eid, body={**want[eid], "status": "confirmed"}).execute()
        known[eid] = digest[eid]
    for eid in upd:
        svc.events().update(calendarId=cid, eventId=eid, body=want[eid]).execute()
        known[eid] = digest[eid]
    for eid in rem:
        svc.events().delete(calendarId=cid, eventId=eid).execute()
        known.pop(eid, None)
    state["last_push"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_state(state)
    print(f"  pushed to '{CAL_NAME}'")


def list_events(svc, cid: str, start: datetime, end: datetime):
    """Busy events in one calendar, judged one by one. Returns (busy, skipped); raises if the
    account can only see free/busy for this calendar."""
    busy, skipped, token = [], [], None
    while True:
        page = svc.events().list(calendarId=cid, timeMin=start.isoformat(), timeMax=end.isoformat(),
                                 singleEvents=True, orderBy="startTime", maxResults=2500, pageToken=token,
                                 fields="nextPageToken,items(summary,start,end,status,transparency,eventType,attendees(self,responseStatus))"
                                 ).execute()
        for ev in page.get("items", []):
            title = ev.get("summary", "(busy)")
            if ev.get("status") == "cancelled" or ev.get("eventType") == "workingLocation":
                continue
            if any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees", [])):
                continue
            if "dateTime" not in ev.get("start", {}):
                skipped.append((title, ev["start"].get("date"), "all-day"))
                continue
            s, e = ev["start"]["dateTime"], ev["end"]["dateTime"]
            hours = (datetime.fromisoformat(e) - datetime.fromisoformat(s)).total_seconds() / 3600
            if ev.get("transparency") == "transparent":
                skipped.append((title, s, "shown as free"))
            elif hours > LONG_H:
                skipped.append((title, s, f"{hours:.0f} h long: treated as a marker"))
            else:
                busy.append({"start": s, "end": e, "title": title})
        token = page.get("nextPageToken")
        if not token:
            break
    return busy, skipped


def account_email(svc) -> str:
    for c in calendar_list(svc):
        if c.get("primary"):
            return c["id"]
    return "(email unknown: log in with --discover)"


def cmd_busy() -> None:
    units = load_units()
    start = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    end = max(u["exam"]["start_dt"] for u in units) + timedelta(days=1)
    busy, errors, report, seen = [], [], [], set()
    accts = ([None] if TOKEN.exists() else []) + extra_accounts()
    if not TOKEN.exists():
        print("  ! main account not logged in (it owns the study calendar): run  gcal_sync.py auth --discover")
    skipped_all = []

    def add(b, cal, acct):
        key = (b["start"], b["end"], b.get("title"))
        if key not in seen:                                         # same event via two accounts: count once
            seen.add(key)
            busy.append({**b, "calendar": cal, "account": acct or "main"})

    for acct in accts:
        svc = service(account=acct)
        cals = busy_calendars(svc, BUSY_CALS if acct is None else [])
        ids = list(cals)
        if EVENTS_SCOPE in _scopes:                                 # event by event where we can read events
            fallback = []
            for cid in ids:
                try:
                    evs, skipped = list_events(svc, cid, start, end)
                except Exception:
                    fallback.append(cid)                            # free/busy-only access: merged blocks
                    continue
                for b in evs:
                    add(b, cals[cid], acct)
                skipped_all += [(cals[cid], *s) for s in skipped]
                report.append((acct or "main", cals[cid] + " [events]", len(evs)))
            ids = fallback
        for i in range(0, len(ids), 15):    # >~20 calendars per call fails with tooManyCalendarsRequested
            res = svc.freebusy().query(body={"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                                             "timeZone": "Australia/Sydney", "calendarExpansionMax": 50,
                                             "items": [{"id": c} for c in ids[i:i + 15]]}).execute()
            for cal, info in res.get("calendars", {}).items():
                errors += [f"{acct or 'main'} / {cals.get(cal, cal)}: {e.get('reason')}" for e in info.get("errors", [])]
                n = 0
                for b in info.get("busy", []):
                    n += 1
                    add({"start": b["start"], "end": b["end"]}, cals.get(cal, cal), acct)
                report.append((acct or "main", cals.get(cal, cal) + " [free/busy]", n))
        hint = " --account " + acct if acct else ""
        if LIST_SCOPE not in _scopes:
            report.append((acct or "main", f"[only listed calendars: run auth{hint} --discover --events]", 0))
        elif EVENTS_SCOPE not in _scopes:
            report.append((acct or "main", f"[merged free/busy only: run auth{hint} --discover --events]", 0))
    busy.sort(key=lambda b: b["start"])
    out = STATE / "gcal_busy.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"fetched": datetime.now(TZ).isoformat(timespec="seconds"),
                               "calendars": [f"{a} / {c}" for a, c, _ in report], "busy": busy}, indent=2), "utf-8")
    print(f"  {len(busy)} busy interval(s) until {end:%Y-%m-%d} → {out.name}")
    for a, c, n in report:
        print(f"    {a:<10} {c:<45} {n:>3}")
    for i, b in enumerate(busy):
        s = datetime.fromisoformat(b["start"]).astimezone(TZ)
        e = datetime.fromisoformat(b["end"]).astimezone(TZ)
        hours = (e - s).total_seconds() / 3600
        if i < 40 or hours > LONG_H:
            end = f"{e:%H:%M}" if e.date() == s.date() else f"{e:%a %d %b %H:%M}"
            warn = (f"   WARN {hours:.0f} h merged block: may hide events inside; log in with --events"
                    if hours > LONG_H else "")
            print(f"      {s:%a %d %b %H:%M}–{end}  {b.get('title', '')}  ({b['calendar']}){warn}")
    if len(busy) > 40:
        print(f"      … {len(busy) - 40} more")
    if skipped_all:
        print("  not counted as busy:")
        for cal, title, when, why in skipped_all:
            print(f"      {str(when)[:16]}  {title}  ({cal}): {why}")
    for e in errors:
        print(f"  ! {e}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["auth", "busy", "push", "status"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--discover", action="store_true",
                    help="with auth: also grant read-only access to your calendar list (all its calendars count as busy)")
    ap.add_argument("--account", help="with auth: an extra Google account to read busy times from (a short label, "
                                      "e.g. gmail). It needs no write access; the study calendar stays on the main account")
    ap.add_argument("--events", action="store_true",
                    help="with auth: read individual events (read-only), so long marker events don't hide real ones")
    a = ap.parse_args()
    if a.command == "auth":
        svc = service(interactive=True, discover=a.discover, account=a.account, events=a.events)
        print(f"  logged in as {account_email(svc)} ({a.account or 'main'}); token {token_path(a.account).name}; "
              f"calendar-list discovery: {'on' if LIST_SCOPE in _scopes else 'off'}; "
              f"event-level reading: {'on' if EVENTS_SCOPE in _scopes else 'off'}")
        for cid, name in busy_calendars(svc, BUSY_CALS if not a.account else []).items():
            print(f"    busy source: {name}")
    elif a.command == "busy":
        cmd_busy()
    elif a.command == "push":
        cmd_push(a.dry_run)
    else:
        s = load_state()
        print(f"  client file: {'yes' if CLIENT.exists() else 'MISSING'}; main token: {'yes' if TOKEN.exists() else 'no'}; "
              f"extra accounts: {', '.join(extra_accounts()) or 'none'}")
        for acct in ([None] if TOKEN.exists() else []) + extra_accounts():
            svc = service(account=acct)
            cals = busy_calendars(svc, BUSY_CALS if acct is None else [])
            print(f"  {acct or 'main'}: {account_email(svc)}; discovery {'on' if LIST_SCOPE in _scopes else 'off'}; "
                  f"busy sources: {', '.join(cals.values())}")
        print(f"  calendar: {s.get('calendar_id', '(not created yet)')}; events tracked: {len(s.get('events', {}))}; "
              f"last push: {s.get('last_push', 'never')}")
        if ICS.exists():
            print(f"  plan: {len(desired_events())} events in {ICS.name}")


if __name__ == "__main__":
    main()
