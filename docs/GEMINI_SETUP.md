# AI calls: backends, quotas, where everything lives

**In use now:** the Antigravity CLI (`GEMINI_BACKEND=agy`) on the Google AI Pro subscription.
All the recall material (54 lectures) and handwriting transcriptions were produced this way. The
Gemini API key (free tier, about 20 calls/day) is the fallback. The billing steps below only
matter if the CLI stops working. For calling agy from other projects or agents (including the
second-marker recipe), see the `antigravity_cli` skill in `my_ai_skills`.

## Backends (`GEMINI_BACKEND` in `.env`)
| | `agy` (current default) | `api` |
|---|---|---|
| What | Antigravity CLI (`%LOCALAPPDATA%\agy\bin\agy.exe`), logged in with your Google AI Pro account through the Antigravity app | Gemini API with `GEMINI_API_KEY` |
| Quota | Subscription: ~1,500 requests/day (Gemini CLI's documented AI Pro figure; check Antigravity's own limits) | Free tier: ~20 requests/day in practice |
| Model | `AGY_MODEL` (`gemini-3.8-flash-medium`; `agy models` lists them) | `GEMINI_MODEL` + `GEMINI_FALLBACK_MODELS` |
| Speed | ~2.5 min per 20-page handwriting chunk | ~1 min |
| How | `gemini._agy`: copies PDFs + context into a temp folder, then `agy -p … --output-format json`; the agent is told not to touch files | `google-genai` SDK |

Tested 2026-11-06 (agy 1.5.1): print mode works from a non-TTY subprocess (the reported hang
didn't occur). On the same 20-page chunk, its transcription was as accurate as the API's and more
literal. Install: `irm https://antigravity.google/cli/install.ps1 | iex` (checksum-verified binary;
adds itself to the user PATH). Login is shared with the Antigravity desktop app.
Switch back with `GEMINI_BACKEND=api` if the CLI breaks after one of its automatic self-updates.

## Where the pieces are
| What | Where |
|---|---|
| API key | `GEMINI_API_KEY` in `my_ai_skills\my_ai_skills\.env` (shared with the `gemini_review`/`gemini_coder` skills). A `GEMINI_API_KEY` in `pipeline_academic\.env` would override it |
| Model | `GEMINI_MODEL` in `pipeline_academic\.env` (now `gemini-3.8-flash`); code default in `scripts\gemini.py` |
| Key loading | `scripts\common.py` (`load_dotenv`: repo `.env` first, then `my_ai_skills\.env`) |
| Every API call | `scripts\gemini.py`: `pdf_parts` (inline ≤18 MB, else Files API), `call` (retries, daily-cap stop, optional Google Search) |
| Callers | `gen_recall.py` (1 call/lecture), `extract_handwriting.py` (1 call/20 PDF pages), `gen_practice_exam.py` (2 calls/mock: write + check) |
| Prompts | `prompts\recall_questions.md`, `handwriting_annotations.md`, `practice_exam.md`, `practice_exam_verify.md` |
| Models this key can call | `python scripts\gemini.py` (listing is free and uses no quota) |
| Usage and limits | https://aistudio.google.com/usage (per model, per day) |
| Which project the key belongs to, and its tier | https://aistudio.google.com/apikey → the key's project → https://aistudio.google.com/projects |

## What the limit actually is (checked 2026-11-06)
The 429 error said: quota `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value **20**, `retryDelay`
**26288 s** (7.3 h). That is a **per-day** cap per model, resetting at midnight Pacific (~19:00 Sydney).
It is not a per-minute limit; a per-minute limit's retry delay is under 60 s.
Because the cap is per model, each model (3.6, 3.7, 3.8 Flash …) has its own 20/day.

The Google AI Pro (student) subscription covers the Gemini app, NotebookLM and Antigravity. It does
**not** raise API quota. Only billing does.

## Overload (503) and fallback models
On 2026-11-06 at ~13:30, `gemini-3.8-flash` and `gemini-3.7-flash` both returned `503 UNAVAILABLE`
("high demand"), while `gemini-3.5-flash` answered in 4 s. `gemini.call` now tries `GEMINI_MODEL`, then
`GEMINI_FALLBACK_MODELS` (default `gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash`). It moves on
after 2 overloaded attempts or a daily cap, and each output's frontmatter `model:` records the model
that actually wrote it.

Known agy behaviour: if the agent calls a tool (e.g. to do arithmetic), print mode ends with an
empty answer. The prompts forbid tool use, and `gemini._agy` retries once. Details are in LOGBOOK
2026-11-06.

## Lift the API cap (fallback only, ≈5 min)
1. https://aistudio.google.com/apikey → note the project of the key that's in `my_ai_skills\.env`.
2. Projects page → that project → **Set up billing** (card or existing Cloud billing account).
   It moves to **Tier 1** immediately; limits rise to production levels.
3. **Set a budget alert** first: https://console.cloud.google.com/billing → Budgets & alerts →
   e.g. AUD 10/month with email alerts at 50/90/100%.
4. Optional, possibly free: Google AI Pro now includes a **US$10/month Google Cloud credit**
   through the Google Developer Program. Open https://developers.google.com/program and look for
   the AI Pro benefit; apply it to the same billing account. Student plans aren't confirmed
   eligible, so check whether the benefit shows up.
5. Run `powershell -File scripts\nightly_ai.ps1`. With Tier 1 it finishes everything in one go.

**Expected cost** (gemini-3.8-flash, US$0.75 / 1M input, US$3.75 / 1M output until 2026-12-31):
all remaining recall + handwriting is ≈1 M input + ≈0.5 M output tokens, about **US$2–3**. A
mock exam (all slides + practice attached, plus the check pass) is ≈0.3 M input, about **US$0.30**.
`--research` adds Google Search grounding, which is billed per search query after a free allowance.

## Without billing
`nightly_ai.ps1` stops cleanly at the cap and resumes the next day. To get more per day for free,
set `GEMINI_MODEL` to another Flash model the key lists (each has its own 20/day), e.g.
`gemini-3.7-flash`. Quality differences between adjacent Flash versions are small for this work.
