"""peer.py: finding the share, question parsing and IDs, templates, submissions, class results, the commands."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import peer
import pytest

# ---------------------------------------------------------------------------------------------------------------
# Question IDs and parsing


def test_question_ids_are_pinned(mock_md):
    """Classmates' submissions refer to these IDs, so they must never change (REFACTOR_PLAN, R0 and R2)."""
    paper, solutions = mock_md
    assert peer.mock_uid("TEST1001", "2026-S2", 1, "12345") == "39c172"
    rows, warnings = peer.template_rows("TEST1001", "2026-S2", 1, paper, solutions)
    assert warnings == []
    assert {r["question_id"]: r["version"] for r in rows} == {
        "39c172-Q1": "97f468", "39c172-Q2": "231210", "39c172-Q3": "bf6e84", "39c172-Q4": "00bb0e"}


def test_parts_marks_and_minutes(mock_md):
    paper, solutions = mock_md
    rows, _ = peer.template_rows("TEST1001", "2026-S2", 1, paper, solutions)
    got = [(peer.label(r), r["marks_available"], r["minutes_allocated"], r["multiple_choice"]) for r in rows]
    assert got == [
        ("Q1(a)", "2", "2.4", ""), ("Q1(b)(i)", "3", "3.6", ""), ("Q1(b)(ii)", "5", "6", ""),
        ("Q2", "1", "2", "yes"),
        ("Q3", "6", "7", ""),
        ("Q4(a)", "1", "2", ""), ("Q4(b)(i)", "1", "2", ""), ("Q4(b)(ii)", "2", "4", ""), ("Q4(c)", "3", "6", "")]


def test_version_ignores_spacing_but_not_edits():
    block = "## Question 1 (2 marks)\n\nFind $x$.\n\n---\n"
    assert peer.text_version(block) == peer.text_version("## Question 1  (2 marks)\nFind  $x$.\n")
    assert peer.text_version(block) != peer.text_version("## Question 1 (2 marks)\nFind $y$.\n")


def test_part_marks_that_do_not_add_up_fall_back_to_the_question():
    block = "## Question 1 (10 marks, 10 min)\n(a) One. [2 marks]\n(b) Two. [3 marks]\n"
    parts, warning = peer.parse_question(1, block)
    assert [(p.part, p.marks) for p in parts] == [("", 10.0)]
    assert "add up to 5" in warning


def test_letter_i_after_h_is_a_letter_not_a_roman_numeral():
    block = "## Question 1 (9 marks)\n" + "".join(f"({c}) x [1 mark]\n" for c in "abcdefghi")
    parts, _ = peer.parse_question(1, block)
    assert [p.part for p in parts] == list("abcdefghi")


def test_parent_marks_replaced_by_its_sub_parts():
    block = "## Question 1 (8 marks)\n(a) Intro. [8 marks]\n(i) One. [4 marks]\n(ii) Two. [4 marks]\n"
    parts, _ = peer.parse_question(1, block)
    assert [p.part for p in parts] == ["a.i", "a.ii"]


def test_seed_falls_back_to_generated_date():
    assert peer.seed_of("", "---\ngenerated: 2026-11-10\n---\n") == "2026-11-10"
    assert peer.seed_of("---\nseed: 7\n---\n", "---\ngenerated: 2026-11-10\n---\n") == "7"


# ---------------------------------------------------------------------------------------------------------------
# Finding the shared folder


def make_share(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / peer.MARKER).write_text("x", "utf-8")
    return path


def test_find_share_in_the_usual_place(tmp_path):
    root = tmp_path / "OneDrive - Uni"
    share = make_share(root / "SHARED" / peer.SHARE_NAME)
    assert peer.find_share(roots=[root]) == share


def test_find_share_through_a_shortcut_with_another_name(tmp_path):
    root = tmp_path / "OneDrive - Uni"
    (root / "Documents" / "big").mkdir(parents=True)
    share = make_share(root / "Shared stuff" / "course notes copy")
    assert peer.find_share(roots=[root]) == share


def test_find_share_stops_at_the_depth_limit(tmp_path):
    root = tmp_path / "od"
    make_share(root / "a" / "b" / "c" / "d")
    assert peer.find_share(roots=[root]) is None


def test_explicit_location_wins_and_a_wrong_one_is_not_replaced(tmp_path, monkeypatch):
    root = tmp_path / "od"
    real = make_share(root / peer.SHARE_NAME)
    other = make_share(tmp_path / "elsewhere")
    assert peer.find_share(str(other), roots=[root]) == other
    assert peer.find_share(str(tmp_path / "nothing here"), roots=[root]) is None
    monkeypatch.setenv("SHARE_PATH", str(other))
    assert peer.find_share(roots=[root]) == other
    monkeypatch.delenv("SHARE_PATH")
    assert peer.find_share(here=other, roots=[root]) == other
    assert peer.find_share(cached=str(tmp_path / "gone"), roots=[root]) == real


# ---------------------------------------------------------------------------------------------------------------
# Templates, submissions, validation


@pytest.fixture
def share(tmp_path, mock_md) -> tuple[Path, list[dict]]:
    """A share with TEST1001 2026-S2 mock 1's results template."""
    s = make_share(tmp_path / "share")
    rows, _ = peer.template_rows("TEST1001", "2026-S2", 1, *mock_md)
    d = peer.mock_dir(s, "TEST1001", "2026-S2")
    d.mkdir(parents=True)
    (d / "mock_exam_1_results_template.csv").write_text(peer.to_csv(rows), "utf-8")
    return s, peer.read_csv(peer.to_csv(rows))


def answers(**marks: str) -> list[str]:
    """Input lines for ask_marks: sat, marked by, then per part (and option for Q2) and minutes per question."""
    return ["2026-11", "self",
            marks.get("q1a", "2"), marks.get("q1bi", "3"), marks.get("q1bii", "5"), "20",
            "B", "1",
            marks.get("q3", "4"), "",
            "1", "1", "2", "3", "15"]


def scripted(lines: list[str]):
    it = iter(lines)
    return lambda prompt: next(it)


def test_record_writes_the_inbox_file_and_a_local_copy(share):
    s, template = share
    rows = peer.ask_marks(template, scripted(answers()), datetime(2026, 11, 21))
    local = peer.local_copy("TEST1001", "2026-S2", 1)
    copy, note = peer.save_submission(rows, template, "tok12345", peer.inbox_root(s), local)
    sent = s / peer.INBOX / "TEST1001" / "2026-S2" / "mock_exam_1" / "tok12345.csv"
    assert sent.exists() and copy.exists() and note.startswith("sent")
    rows2 = peer.read_csv(sent.read_text("utf-8"))
    assert rows2[0]["minutes_used"] == "20" and rows2[3]["option_chosen"] == "B" and rows2[0]["sat"] == "2026-11"
    _, note = peer.save_submission(rows, template, "tok12345", peer.inbox_root(s), local)
    assert note.startswith("replaced")
    assert len(list(sent.parent.glob("*.csv"))) == 1


def test_ask_marks_retries_an_invalid_entry(share):
    _, template = share
    lines = answers()
    lines[2:3] = ["7", "2"]                       # 7 is more than Q1(a)'s 2 marks
    rows = peer.ask_marks(template, scripted(lines), datetime(2026, 11, 21))
    assert rows[0]["marks_awarded"] == "2"


def test_validate_rejects_bad_files(share):
    _, template = share
    good = [{**t, "marks_awarded": "1", "token": "abcd1234", "sat": "2026-11", "marked_by": "self"} for t in template]
    sub, errs = peer.validate(good, template)
    assert sub and not errs
    cases = {
        "between 0 and": [{**good[0], "marks_awarded": "9"}],
        "unknown question": [{**good[0], "question_id": "zzz-Q1"}],
        "token": [{**good[0], "token": "a b"}],
        "year and month": [{**good[0], "sat": "21/11/2026"}],
        "one letter": [{**good[3], "option_chosen": "BC"}],
    }
    for expected, rows in cases.items():
        sub, errs = peer.validate(rows, template)
        assert sub is None and any(expected in e for e in errs), expected


def test_rows_for_an_older_question_version_are_not_counted(share):
    _, template = share
    rows = [{**template[0], "version": "oldold", "marks_awarded": "1", "token": "abcd1234"}]
    sub, errs = peer.validate(rows, template)
    assert not errs and sub.old_version == 1 and not sub.answers


def test_collect_uses_the_file_name_as_token_and_reports_rejects(share):
    s, template = share
    folder = s / peer.INBOX / "TEST1001" / "2026-S2" / "mock_exam_1"
    folder.mkdir(parents=True)
    by_hand = [{**t, "marks_awarded": "1"} for t in template]
    (folder / "handfilled.csv").write_text("﻿" + peer.to_csv(by_hand).replace(",", ";"), "utf-8")
    (folder / "broken.csv").write_text(peer.to_csv([{**template[0], "marks_awarded": "-1"}]), "utf-8")
    (folder / "notes.txt").write_text("ignored", "utf-8")
    subs, rejected = peer.collect(folder, template)
    assert [x.token for x in subs] == ["handfilled"]
    assert len(rejected) == 1 and rejected[0].startswith("broken.csv")


# ---------------------------------------------------------------------------------------------------------------
# Class results


def submission(template: list[dict], token: str, frac: dict[str, float],
               minutes: float | None = None) -> peer.Submission:
    """A submission scoring `frac` of each question's marks (by question number)."""
    sub = peer.Submission(token, "2026-11", "self")
    for t in template:
        f = frac.get(t["question"])
        if f is not None:
            first = t is next(x for x in template if x["question"] == t["question"])
            sub.answers[(t["question_id"], t["part"])] = {
                "awarded": f * float(t["marks_available"]), "option": "B" if t["multiple_choice"] else "",
                "minutes": minutes if first else None}
    return sub


def test_nothing_but_counts_below_five_attempts(share):
    _, template = share
    subs = [submission(template, f"t{i}xxx", {"1": 0.5, "3": 1.0}) for i in range(4)]
    agg = peer.aggregate(template, subs)
    assert agg["participants"] == 4 and agg["mean"] is None
    assert all(q["facility"] is None and q["discrimination"] is None for q in agg["questions"])
    assert agg["questions"][0]["n"] == 4


def test_facility_discrimination_and_flags(share):
    _, template = share
    # Q1 tracks the rest of the paper; Q3 goes the other way (the strongest score 0), and takes far too long.
    levels = [0.2, 0.4, 0.6, 0.8, 1.0, 0.9]
    subs = [submission(template, f"tok{i}aaa", {"1": lv, "3": 1 - lv, "4": lv}, minutes=30) for i, lv in
            enumerate(levels)]
    agg = peer.aggregate(template, subs, errata={4: 2})
    q1, q2, q3, q4 = agg["questions"]
    assert q1["n"] == 6 and q1["facility"] == pytest.approx(sum(levels) / 6)
    assert q1["discrimination"] > 0.5
    assert q3["discrimination"] < 0 and any("stronger students" in f for f in q3["flags"])
    assert any("much longer" in f for f in q3["flags"])           # 30 min against 7
    assert q2["n"] == 0 and q2["facility"] is None
    assert any("2 error report" in f for f in q4["flags"])
    assert agg["mean"] is not None


def test_published_results_hold_no_tokens(share):
    _, template = share
    subs = [submission(template, f"secret{i}", {"1": 0.5}) for i in range(6)]
    agg = peer.aggregate(template, subs)
    md, js = peer.class_results_md(agg, "2026-11-21"), json.dumps(agg)
    assert "secret" not in md and "secret" not in js
    assert "| Q1 | 10 | 6 | 50% |" in md


def test_compare_counts_questions_above_the_class_mean(share):
    _, template = share
    agg = peer.aggregate(template, [submission(template, f"tok{i}aaa", {"1": 0.5}) for i in range(5)])
    mine = [{**t, "marks_awarded": t["marks_available"]} for t in template if t["question"] == "1"]
    lines = peer.compare(agg, mine)
    assert lines[-1] == "You are above the class mean on 1 of 1 question(s) with results."


# ---------------------------------------------------------------------------------------------------------------
# Error reports and the command line


def test_report_and_errata_counts(tmp_path):
    inbox = tmp_path / "inbox"
    p = peer.write_report(inbox, "TEST1001", "2026-S2", 1, "q3b", "The answer should be 2.", "tok12345",
                          datetime(2026, 11, 21, 9, 30))
    assert p.name == "tok12345_20261121-093000.md"
    assert "question: Q3B" in p.read_text("utf-8")
    assert peer.errata_counts(p.parent, 1) == {3: 1}
    with pytest.raises(peer.PeerError):
        peer.write_report(inbox, "TEST1001", "2026-S2", 1, "three", "x", "tok12345", datetime(2026, 11, 21))


def test_cli_where_record_results(share, monkeypatch, capsys):
    s, template = share
    assert peer.main(["where", "--share", str(s)]) == 0
    assert str(s) in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", scripted(answers()))
    assert peer.main(["record", "test1001", "1", "--share", str(s)]) == 0
    out = capsys.readouterr().out
    assert "sent:" in out and "on the 9 part(s) attempted" in out
    assert peer.main(["results", "TEST1001", "1", "--share", str(s)]) == 1     # no class results exported yet
    assert "no class results" in capsys.readouterr().err


def test_cli_record_from_a_filled_template(share, tmp_path, capsys):
    s, template = share
    filled = tmp_path / "mine.csv"
    filled.write_text(peer.to_csv([{**t, "marks_awarded": "1", "sat": "2026-11"} for t in template]), "utf-8")
    assert peer.main(["record", "TEST1001", "1", "--share", str(s), "--from", str(filled)]) == 0
    assert "9/24 on the 9 part(s) attempted" in capsys.readouterr().out


def test_cli_explains_a_missing_share(tmp_path, capsys):
    assert peer.main(["where", "--share", str(tmp_path / "nope")]) == 1
    assert "is not the shared folder" in capsys.readouterr().err


def test_unknown_unit_lists_what_the_share_has(share, capsys):
    s, _ = share
    assert peer.main(["record", "ZZZZ9999", "1", "--share", str(s)]) == 1
    assert "it has: TEST1001" in capsys.readouterr().err
