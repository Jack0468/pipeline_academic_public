"""share_vault.py's peer parts: templates and class results per mock, the sync never touching _submissions/, and
peer.py itself passing the personal-data scan it is published under."""
from __future__ import annotations

from pathlib import Path

import peer
import pytest
import share_vault as sv


@pytest.fixture
def share(tmp_path) -> Path:
    s = tmp_path / "share"
    (s / "TEST1001" / "2026-S2" / "mock_exams").mkdir(parents=True)
    (s / sv.MARKER).write_text("x", "utf-8")
    return s


def test_sync_removes_stale_files_but_never_submissions(share):
    inbox = share / peer.INBOX
    mine = inbox / "TEST1001" / "2026-S2" / "mock_exam_1" / "tok12345.csv"
    mine.parent.mkdir(parents=True)
    mine.write_text("classmate's file", "utf-8")
    (inbox / "TEST1001" / "2026-S2" / "errata").mkdir()                     # an empty folder of theirs
    old = share / "TEST1001" / "2026-S2" / "mock_exams" / "mock_exam_9.md"
    old.write_text("withdrawn mock", "utf-8")
    plan = [(Path("README.md"), "readme\n"), (Path("TEST1001/2026-S2/README.md"), "unit\n")]

    def in_scope(rel: Path) -> bool:
        return True
    stale = sv.stale_files(share, {d for d, _ in plan}, in_scope)
    assert stale == [old]
    sv.sync(share, inbox, plan, stale, in_scope)
    assert not old.exists()
    assert mine.read_text("utf-8") == "classmate's file"
    assert (inbox / "TEST1001" / "2026-S2" / "errata").is_dir()
    assert (share / "README.md").read_text("utf-8") == "readme\n"


def test_sync_creates_the_submissions_folder(tmp_path):
    share = tmp_path / "new share"
    sv.sync(share, share / peer.INBOX, [(Path("README.md"), "x\n")], [], lambda rel: True)
    assert (share / peer.INBOX).is_dir() and (share / sv.MARKER).exists()


def test_mock_results_template_then_class_results(share, mock_md, tmp_path):
    paper, solutions = mock_md
    inbox = share / peer.INBOX
    uid, files, notes = sv.mock_results("TEST1001", "2026-S2", 1, paper, solutions, inbox, "2026-11-21")
    assert uid == "39c172" and list(files) == ["mock_exam_1_results_template.csv"] and notes == []
    template = peer.read_csv(files["mock_exam_1_results_template.csv"])
    folder = inbox / "TEST1001" / "2026-S2" / "mock_exam_1"
    folder.mkdir(parents=True)
    for i in range(5):
        rows = [{**t, "marks_awarded": "1", "token": f"tok{i}abc", "sat": "2026-11"} for t in template]
        (folder / f"tok{i}abc.csv").write_text(peer.to_csv(rows), "utf-8")
    (folder / "bad.csv").write_text("question_id,part,marks_awarded\nnope,,1\n", "utf-8")
    _, files, notes = sv.mock_results("TEST1001", "2026-S2", 1, paper, solutions, inbox, "2026-11-21")
    assert "mock_exam_1_class_results.md" in files and "mock_exam_1_class_results.json" in files
    assert "From 5 attempt(s)" in files["mock_exam_1_class_results.md"]
    assert any("REJECTED submission bad.csv" in n for n in notes)
    for text in files.values():
        assert not sv.personal_hits(text)


def test_exported_mock_carries_its_uid(mock_md):
    paper, _ = mock_md
    out = sv.clean(sv.clean_mock(sv.with_front(paper, "mock_uid", "39c172")), set())
    assert out.startswith("---\n") and "mock_uid: 39c172" in out.split("---")[1]


def test_shared_notes_from_the_unit_config(tmp_path):
    unit_dir = tmp_path / "units" / "2026_sc2_test1001"
    (unit_dir / "sources").mkdir(parents=True)
    (unit_dir / "sources" / "handwritten_crash course.md").write_text(
        "---\nunit: TEST1001\nsource: handwritten\ntype: transcription\noriginal: \"file:///x.pdf\"\n---\n"
        "# Crash course\n\nPole placement matters. [scan](file:///c/x.pdf)\n", "utf-8")
    u = {"code": "TEST1001", "share": {"notes": [
        {"file": "sources/handwritten_crash course.md", "as": "crash_course_notes.md", "title": "Crash course notes"},
        {"file": "../../outside.md", "as": "x.md"},
        {"file": "sources/missing.md", "as": "y.md"},
        {"file": "sources/handwritten_crash course.md", "as": "../escape.md"}]}}
    shared, problems = sv.shared_notes(u, unit_dir, set())
    assert [n for n, _ in shared] == ["crash_course_notes.md"]
    text = shared[0][1]
    front = text.split("---")[1]
    assert "type: shared notes" in front and "transcription" not in front and "original" not in front
    assert "> [!info] Crash course notes. Shared from a classmate's own notes" in text
    assert "file:///" not in text and "Pole placement matters. scan" in text
    assert len(problems) == 3 and not sv.personal_hits(text)


def test_no_shared_notes_without_config(tmp_path):
    assert sv.shared_notes({"code": "TEST1001"}, tmp_path, set()) == ([], [])


def test_peer_tool_passes_the_personal_data_scan():
    """peer.py is copied into the share, so it must never contain anything the scan blocks."""
    assert sv.personal_hits(sv.PEER_SOURCE.read_text("utf-8")) == []


def test_top_readme_has_the_consent_note():
    text = sv.TOP_README.format(units="- unit", date="2026-11-21", example="TEST1001 1",
                                example_words="TEST1001 mock exam 1")
    assert "python peer.py record TEST1001 1" in text and "not anonymous" in text and not sv.personal_hits(text)


def test_readme_example_prefers_an_upcoming_unit():
    from datetime import datetime, timedelta

    from common import TZ
    past = {"code": "OLD1001", "exam": {"start_dt": datetime.now(TZ) - timedelta(days=3)}}
    soon = {"code": "NEW1002", "exam": {"start_dt": datetime.now(TZ) + timedelta(days=3)}}
    assert sv.readme_example([past, soon], [(past, 3), (soon, 1)]) == ("NEW1002", 1)
    assert sv.readme_example([past], [(past, 3)]) == ("OLD1001", 3)
    assert sv.readme_example([], []) == ("ABCD1234", 1)
