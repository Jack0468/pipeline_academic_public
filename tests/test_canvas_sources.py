"""The Canvas mirror as the source of lectures and tutorials: solutions detection, relabelling, the vault cache."""
from __future__ import annotations

import adopt_sources as ad
from common import prune_cache, solutions_start


def test_solutions_start_finds_the_heading_page():
    sheet = ["Week 2 Tutorial\n1. Inversion and high gain feedback", "(e) Assume now a disturbance"]
    assert solutions_start(sheet) is None                                   # a question sheet
    assert solutions_start(sheet + ["Week 2 Solutions\n1. (a) The inverse"]) == 3
    assert solutions_start(["Week 4 Tutorial Solutions\n1. Internal Stability"]) == 1
    assert solutions_start(["questions", "Solutions\n1. To achieve zero steady state"]) == 2
    assert solutions_start(["Week 3 Tutorial", "more questions"], "ABCD1001_Tutorial_3_Solutions.pdf") == 1


def test_solutions_heading_ignores_the_word_in_a_sentence():
    assert solutions_start(["Determine the solution T(t) to the differential equation (7)."]) is None
    assert solutions_start(["The homogeneous solution is"]) is None


def test_relabel_only_touches_slide_references():
    text = "(L6a p.20), L6a p.36 and (L6b pp. 14-16); not L6ab p.1, XL6a p.2 or 'L6a is a label'"
    out = ad.relabel(ad.relabel(text, "L6a", "L6"), "L6b", "L6")
    assert out == "(L6 p.20), L6 p.36 and (L6 pp. 14-16); not L6ab p.1, XL6a p.2 or 'L6a is a label'"


def test_log_edit_appends_to_front_matter_lists():
    assert 'edits:\n  - "x"' in ad.log_edit("---\nedits: []\n---\n", "x")
    out = ad.log_edit('---\nedits:\n  - "a"\nother: 1\n---\n', "b")
    assert '  - "a"\n  - "b"\nother: 1' in out
    assert ad.log_edit("---\nunit: X\n---\n", "b") == "---\nunit: X\n---\n"   # no edits list: unchanged


def test_prune_cache_keeps_current_copies_only(tmp_path):
    keep, stale = tmp_path / "L1_new.pdf", tmp_path / "L1_old.pdf"
    other = tmp_path / "notes.md"
    for p in (keep, stale, other):
        p.write_text("x")
    assert prune_cache(tmp_path, {keep}) == [stale]
    assert keep.exists() and not stale.exists() and other.exists()
    assert prune_cache(tmp_path / "missing", set()) == []
