# AI batch. On the free tier this does ~20 requests/day/model (resets ~19:00 Sydney); with billing
# enabled (Tier 1) it runs everything in one go.
# Unit by unit in exam order: recall material, handwriting transcription, then checks of your
# handwritten recall attempts (summary_recall/recall_L<n>.pdf).
# Stops at the daily cap; finished work is cached, so just run it again tomorrow.
# OneNote: only the changed pages of attempt sections are exported here (export_onenote.py --attempts-only, no AI),
# so a recall page written tonight is marked tonight. Whole sections are NOT exported here: re-exporting an edited
# section re-transcribes all of it (BLUEPRINT N: page-level transcription). Run scripts\export_onenote.py yourself
# when you want new lecture or tutorial ink picked up.
$py = if ($env:PIPELINE_PYTHON) { $env:PIPELINE_PYTHON } else { "$env:USERPROFILE\.conda\envs\pipeline_academic\python.exe" }
$env:PYTHONUNBUFFERED = "1"     # progress lines reach the log as they happen
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force "state\logs" | Out-Null
$log = "state\logs\nightly_$(Get-Date -Format yyyy-MM-dd).log"
& $py scripts\export_onenote.py --attempts-only | Tee-Object -FilePath $log -Append   # changed attempt pages only
$units = & $py -c "import sys; sys.path.insert(0, 'scripts'); from common import load_units, exam_over; print(' '.join(u['code'] for u in load_units() if not exam_over(u)))"   # sat units are frozen
foreach ($u in $units.Split(" ")) {
    foreach ($script in "gen_recall.py", "lecture_examples.py", "extract_handwriting.py", "grade_recall.py", "grade_practice.py", "ingest_intel.py") {   # mock exams: on request only (gen_practice_exam.py)
        $cmd = $script.Split(" ")
        $out = & $py "scripts\$($cmd[0])" @($cmd | Select-Object -Skip 1) --unit $u 2>&1 | Tee-Object -FilePath $log -Append
        $out
        if ($out -match "STOPPED") { "Daily cap reached; resuming tomorrow." | Tee-Object -FilePath $log -Append; exit 0 }
    }
}
& $py scripts\pace_recall.py | Tee-Object -FilePath $log -Append   #  times on new recall files
& $py scripts\link_slides.py | Tee-Object -FilePath $log -Append   # make new recall files' "p. N" refs clickable
& $py scripts\link_tutorials.py | Tee-Object -FilePath $log -Append   # new transcriptions -> pages per tutorial
