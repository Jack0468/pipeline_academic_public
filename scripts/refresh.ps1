# Zero-token refresh: recompute marks, regenerate the study plan + calendar, add any new vault files.
# Run after a new mark lands, after editing config (confidence, busy shifts), after adding sources,
# or after adding shifts to your Google Calendar.
# If Google Calendar is linked (.secrets\gcal_token.json, see docs\GCAL_SETUP.md), it also pulls your
# busy times first and pushes the new plan to the "Exam study (pipeline)" calendar at the end.
$ErrorActionPreference = "Stop"
$py = if ($env:PIPELINE_PYTHON) { $env:PIPELINE_PYTHON } else { "$env:USERPROFILE\.conda\envs\pipeline_academic\python.exe" }
Set-Location (Split-Path $PSScriptRoot -Parent)
$gcal = Test-Path ".secrets\gcal_token.json"
$gcalOk = $gcal
if ($gcal) {
    & $py scripts\gcal_sync.py busy
    if ($LASTEXITCODE) {                            # usually an expired login (testing-mode tokens last 7 days)
        Write-Host "  WARN Google Calendar busy times not refreshed: planning with the last saved ones; calendar push skipped."
        Write-Host "       Log in again (30 s): python scripts\gcal_sync.py auth --discover --events  (and --account <label> for the extra account)"
        $gcalOk = $false
    }
}
& $py scripts\fetch_unit_outline.py --if-missing   # official outlines (sydney.edu.au), once per unit
if (Select-String -Path ".env" -Pattern "^CANVAS_TOKEN=." -Quiet) {
    & $py scripts\canvas_sync.py                    # marks, marker feedback, announcements, new slides
    if ($LASTEXITCODE) { Write-Host "  (Canvas sync failed; continuing with the last synced data)" }
}
& $py scripts\svg_notes_to_pdf.py | Where-Object { $_ -notmatch "\(cached" }   # note-app SVGs → annotated PDFs (sources.annotated_svg)
& $py scripts\marks.py | Out-Null; if ($LASTEXITCODE) { exit $LASTEXITCODE }
& $py scripts\plan_study.py | Select-Object -Last 3
& $py scripts\scaffold_vault.py
& $py scripts\pace_recall.py | Out-Null             # time allocation per exam-style question
& $py scripts\link_slides.py | Out-Null             # slide copies in the vault + clickable "p. N" links
& $py scripts\link_tutorials.py | Out-Null          # tutorial sheets in the vault + your working per tutorial
# Handbook: only on request, per subject (build_handbook.py --unit X --pdf), never from here (rule since 2026-11-18)
& $py scripts\check_no_emoji.py | Select-Object -Last 1   # rule: no emojis anywhere (fix: check_no_emoji.py --fix)
& $py scripts\check_vault.py --brief                       # units on the folder standard (docs/VAULT_STANDARD.md); read-only
if ($gcalOk) { & $py scripts\gcal_sync.py push }
elseif ($gcal) { Write-Host "`n(Google Calendar not updated: log in again, then re-run refresh.ps1)" }
else { Write-Host "`n(Google Calendar not linked: see docs\GCAL_SETUP.md)" }
Write-Host "`nAI steps (only process new/changed files):"
Write-Host "  powershell -File scripts\nightly_ai.ps1"
