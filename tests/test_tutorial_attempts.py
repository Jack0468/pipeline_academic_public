"""tutorial_attempts.py: questions on a sheet, labels in your notes, and a checklist that keeps your ticks."""
from __future__ import annotations

import tutorial_attempts as ta


def test_sheet_questions_in_order_only():
    text = "Week 5 Tutorial\n1. A plant has a model (1) given by\n2. Consider a linear plant\n2. stray\n3. Consider\n"
    assert list(ta.sheet_questions(text)) == [1, 2, 3]
    assert ta.sheet_questions("no numbered questions here") == {}


def test_labels_from_headings_and_line_starts_only():
    assert ta.labels("Tutorial 4 Question 3: pole placement", "", 3) == [3]
    assert ta.labels("Figure 5: Nyquist plot of L(s) in Problem 4", "", 4) == [4]
    assert ta.labels("notes", "> 1.) $P(s)$\n> ② Rational controller", 3) == [1, 2]
    assert ta.labels("notes", "> [sketch: star] Q2 is close to exam style question", 4) == []   # a mention, not working
    assert ta.labels("Tutorial 9 question 7", "", 3) == []                                          # not on the sheet


def checklist(tmp_path, monkeypatch, ev):
    import tutorial_attempts
    monkeypatch.setattr(tutorial_attempts, "VAULT", tmp_path)
    monkeypatch.setattr(tutorial_attempts, "unit_path",
                        lambda u, key: tmp_path / ("practice/tutorial_attempts.md" if key == "tutorial_attempts"
                                                   else "practice"))
    return ta.update_checklist({"code": "ABCD1001"}, ev)


def test_checklist_keeps_your_ticks_and_notes(tmp_path, monkeypatch):
    ev = {(2, 1): "your working p. 2", (2, 2): "no working found", (5, 1): "no working found"}
    ticks, status = checklist(tmp_path, monkeypatch, ev)
    assert status == "created" and ticks == {(2, 1): True, (2, 2): False, (5, 1): False}
    path = tmp_path / "practice/tutorial_attempts.md"
    md = path.read_text("utf-8")
    md = md.replace("- [x] Q1 · your working p. 2", "- [ ] Q1 · your working p. 2\n  only read it, redo")   # you untick
    md = md.replace("- [ ] Q1 · no working found", "- [x] Q1 · did it on paper")                          # you tick
    path.write_text(md, "utf-8")
    ev2 = {(2, 1): "your working p. 2, p. 3", (2, 2): "your working p. 4", (5, 1): "no working found",
           (5, 2): "no working found", (3, 1): "your working p. 9"}
    ticks, status = checklist(tmp_path, monkeypatch, ev2)
    md = path.read_text("utf-8")
    assert ticks[(2, 1)] is False                      # your untick stays, evidence refreshed
    assert "- [ ] Q1 · your working p. 2, p. 3\n  only read it, redo" in md
    assert ticks[(2, 2)] is True                       # new working found: ticked
    assert "- [x] Q1 · did it on paper" in md and ticks[(5, 1)] is True     # your own text and tick kept
    assert md.index("## Tutorial 3") < md.index("## Tutorial 5")            # a new tutorial goes in number order
    assert "- [ ] Q2 · no working found" in md.split("## Tutorial 5")[1]
    assert checklist(tmp_path, monkeypatch, ev2)[1] == "unchanged"
