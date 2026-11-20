"""Where every file lives. The one module that knows the repo's output folders and the vault's unit layout.

Rule: a script never spells out a location for something it generates. It asks for a key here (through
common.unit_path for vault notes), so moving a file type means changing one table. The standard behind the tables is
docs/VAULT_STANDARD.md; the routing of every generated file is in docs/ARCHITECTURE.md.

Machine-specific locations come from the environment (.env), so another student changes settings, not code:
    VAULT_PATH, ONEDRIVE_COURSEWORK    their vault and coursework folder
    PIPELINE_STATE, PIPELINE_OUT       caches and exports (default: state/ and out/ in the repo)
    PIPELINE_CONFIG                    unit configs (default: config/); tests point this at a copy
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
PROMPTS = REPO / "prompts"

load_dotenv(REPO / ".env")
# Fall back to the shared key file in my_ai_skills (never printed).
load_dotenv(REPO.parent / "my_ai_skills" / "my_ai_skills" / ".env")

CONFIG = Path(os.environ.get("PIPELINE_CONFIG", str(REPO / "config")))
STATE = Path(os.environ.get("PIPELINE_STATE", str(REPO / "state")))      # caches and machine state: safe to rebuild
OUT = Path(os.environ.get("PIPELINE_OUT", str(REPO / "out")))            # exports for other programs
def _default_coursework() -> Path:
    """.UNI-COURSEWORK in the work or school OneDrive (OneDriveCommercial), else in the personal one."""
    root = os.environ.get("OneDriveCommercial") or os.environ.get("OneDrive") or str(Path.home() / "OneDrive")
    return Path(root) / ".UNI-COURSEWORK"


ONEDRIVE_COURSEWORK = Path(os.environ.get("ONEDRIVE_COURSEWORK") or _default_coursework())
VAULT = Path(os.environ.get("VAULT_PATH", str(REPO.parent / "academic_vault")))

CALENDAR_DIR = OUT / "calendar"
STUDY_ICS = CALENDAR_DIR / "study_plan.ics"
EXAMS_ICS = CALENDAR_DIR / "exams.ics"
LOG_DIR = STATE / "logs"
BACKUP_DIR = STATE / "vault_backups"
MIGRATION_DIR = STATE / "vault_migrations"

# Unit folder layouts. A unit's config picks one with `vault_layout:`; without it a unit stays on layout 1, so units
# that have not been migrated (and sat units, which are frozen) keep working exactly as they are.
_LAYOUT_1 = {
    "hub": "_unit.md",
    "plan": "exam_plan.md",
    "notes_sheet": "notes_sheet.md",
    "outline": "_unit_outline.md",
    "canvas": "canvas.md",
    "intel": "exam_intel.md",
    "handbook": "handbook.md",
    "handbook_pdf": "handbook.pdf",
    "handbook_complete_pdf": "handbook_with_worked_examples.pdf",
    "worked_examples": "worked_examples.md",
    "worked_examples_pdf": "{code}_worked_examples.pdf",
    "worked_examples_types": "worked_examples_types.md",
    "recall": "recall",
    "recall_checks": "recall_checks",
    "practice": "practice",
    "practice_checks": "practice_checks",
    "error_log": "practice/error_log.md",
    "stuck_log": "practice/stuck_log.md",
    "past_papers": "practice/past_papers.md",
    "sources": "sources",
    "slides": "slides",
    "tutorials": "tutorials",
    "archive": "_archive",
}
_LAYOUT_2 = {
    **_LAYOUT_1,
    "outline": "reference/unit_outline.md",
    "canvas": "reference/canvas.md",
    "intel": "reference/exam_intel.md",
    "handbook": "exam_room/handbook.md",
    "handbook_pdf": "exam_room/handbook.pdf",
    "handbook_complete_pdf": "exam_room/handbook_with_worked_examples.pdf",
    "worked_examples": "exam_room/worked_examples.md",
    "worked_examples_pdf": "exam_room/worked_examples.pdf",
    "worked_examples_types": "exam_room/worked_examples_types.md",
}
LAYOUTS = {1: _LAYOUT_1, 2: _LAYOUT_2}
CURRENT_LAYOUT = 2

# What may sit in a layout 2 unit folder (check_vault.py enforces it; EXAM_<year>/ is added by reconstruct_exam.py).
ROOT_NOTES = {"_unit.md", "exam_plan.md", "notes_sheet.md"}
UNIT_FOLDERS = {"recall", "recall_checks", "practice", "practice_checks", "sources", "slides", "tutorials",
                "reference", "exam_room", "_archive"}
PDF_FOLDERS = {"exam_room", "practice", "slides", "tutorials", "_archive"}


def layout(u: dict) -> int:
    return int(u.get("vault_layout") or 1)


def unit_rel(u: dict, key: str) -> str:
    """A note or folder's path inside the unit folder, in the unit's layout."""
    return LAYOUTS[layout(u)][key].replace("{code}", str(u.get("code", "")))


def note_type(rel: str) -> str | None:
    """The `type` property a note gets from where it lives in a layout 2 unit (None: it must set its own)."""
    exact = {"_unit.md": "unit hub", "exam_plan.md": "exam plan", "notes_sheet.md": "notes sheet",
             "reference/unit_outline.md": "unit outline", "reference/canvas.md": "canvas record",
             "reference/exam_intel.md": "exam intel", "exam_room/handbook.md": "handbook",
             "exam_room/worked_examples.md": "worked examples",
             "exam_room/worked_examples_types.md": "worked-example types",
             "practice/error_log.md": "error log", "practice/stuck_log.md": "stuck log",
             "practice/past_papers.md": "practice log", "practice/tutorials.md": "tutorial index"}
    if rel in exact:
        return exact[rel]
    folder, _, name = rel.partition("/")
    if folder == "sources":
        return "transcription" if name.startswith("handwritten_") else "reference notes"
    return {"recall": "recall", "recall_checks": "recall check", "practice_checks": "practice check"}.get(folder)
