# Google Calendar link (`scripts/gcal_sync.py`)

## What it does
- **busy:** reads your commitments from every calendar on every Google account you log in, and
  writes them to `state/gcal_busy.json`. `plan_study.py` schedules around them, keeping 30 min clear
  on each side.
- **push:** writes the plan (focus blocks, evening reviews, exams with seat, venue, map and 24 h /
  2 h reminders) into its own calendar, **"Exam study (pipeline)"**. Each unit gets a colour, and
  exams are red. Event IDs come from the planner's stable IDs, so a re-push updates blocks in
  place and deletes dropped ones. There are never duplicates.
- `scripts\refresh.ps1` does busy → plan → push whenever a main-account token exists.

## Accounts
| Login | Token | Role |
|---|---|---|
| main | `.secrets/gcal_token.json` | owns "Exam study (pipeline)"; its calendars count as busy |
| `--account <label>` (any number) | `.secrets/gcal_token_<label>.json` | read-only; its calendars count as busy |

`.env`: `GCAL_BUSY_CALENDARS=primary,<other calendar ids>`, `GCAL_IGNORE_CALENDARS=<names or ids to skip>`.

## Logging in
Each login opens the browser. Pick the account named, then click "Google hasn't verified this
app → Continue" (it's your own app in testing mode). Run these in the `pipeline_academic` env
(`conda activate pipeline_academic` first). Base Python has no Google libraries and fails with
`No module named 'google_auth_oauthlib'`.
```
conda activate pipeline_academic
python scripts\gcal_sync.py auth --discover --events                     # pick the main account
python scripts\gcal_sync.py auth --account gmail --discover --events     # an extra account, labelled "gmail"
python scripts\gcal_sync.py status                                       # check every account + sources
powershell -File scripts\refresh.ps1
```
- **`--discover`:** read the account's calendar list, so *every* calendar on it counts, including
  ones shared from other accounts.
- **`--events`:** read events one by one (read-only) instead of merged free/busy blocks. See below.
- **The main login must be the account that owns the study calendar.** A different account gets
  "can't write to the study calendar", and nothing is changed. `auth` warns if you pick the wrong
  one. To fix a mix-up, rerun both `auth` lines and pick the right account each time. `auth` always
  shows the picker and replaces that login's token.
- **Testing-mode tokens expire after 7 days.** When refresh says "Not logged in or login
  expired", rerun the two `auth` lines (30 s). `auth` notices the dead token and opens the browser.
  To stop the weekly expiry, publish the app (**Google Auth Platform → Audience → Publish app**).
  It stays unverified, so you still click through the warning, but tokens no longer expire after 7 days.

## Which events count as busy
With `--events`, each event is judged on its own:
- **Counted:** timed events, listed by `gcal_sync.py busy` with their titles.
- **Skipped** (listed under "not counted as busy", with the reason):
  - events longer than `GCAL_LONG_EVENT_HOURS` (12 h), which are treated as markers like a
    "study period";
  - all-day events;
  - events set to "Show as: Free";
  - cancelled or declined events, and working-location entries.

**Why `--events` matters:** free/busy merges overlapping events. A week-long marker event once
swallowed a crash-course lecture inside it. Event-level reading keeps the lecture and skips the
marker.

**What still uses free/busy:** calendars an account can only see as free/busy fall back to merged
blocks. As a safety net, the planner ignores any merged block longer than `max_busy_hours` (12)
in `config/study_plan.yaml` and prints a warning.

To leave a calendar out entirely, add it to `GCAL_IGNORE_CALENDARS=<name or id>,…` in `.env`.
Holidays, birthdays, weather and the study calendar itself are always skipped.

## Permissions
| Scope | Account | Allows |
|---|---|---|
| `calendar.app.created` | main | create *one* calendar and manage only the events in it |
| `calendar.freebusy` | all | see free/busy times |
| `calendar.calendarlist.readonly` (`--discover`) | all | see which calendars the account has |
| `calendar.events.readonly` (`--events`) | all | read event times and titles (nothing is written; titles stay in `state/`) |

Nothing can change or delete events in your own calendars.

## First-time setup (done 2026-11-09; for reference)
1. https://console.cloud.google.com/ → a project → **APIs & Services → Library →** "Google
   Calendar API" → **Enable**.
2. **Google Auth Platform**: app name `pipeline_academic`, audience **External**. Add every
   account you'll log in with under **Audience → Test users**; otherwise Google says "Access blocked".
3. **Clients → Create client → Desktop app → Download JSON**. Save it as
   `.secrets\google_oauth_client.json` (gitignored).
4. Run the login commands above.

## Day to day and gotchas
- Put commitments in any of your calendars as usual, then run `refresh.ps1`. Clashing blocks move
  and the study calendar updates.
- Don't edit events in "Exam study (pipeline)": the next push overwrites them.
- `gcal_sync.py busy` on its own shows exactly what the planner will avoid. It's the first thing
  to check if a block lands on top of something.
- To start the study calendar over: delete it in Google Calendar, delete `state\gcal.json`, then
  `gcal_sync.py push`.
