"""link_tutorials.py: matching tutorial sheets and handwritten pages to tutorial numbers."""
from __future__ import annotations

from pathlib import Path

import link_tutorials as lt


def test_tutorial_number_from_sheet_names():
    names = {"ABCD1001_Tutorial_2.pdf": 2, "ABCD1001___Tutorial_11-5.pdf": 11, "ABCD1001___Tutorial_6-1.pdf": 6,
             "tut3_questions.pdf": 3, "ABCD1001_tutorial.pdf": None}
    for name, n in names.items():
        m = lt.TUT_FILE.search(Path(name).stem)
        assert (int(m.group(1)) if m else None) == n, name


def test_tutorial_number_from_page_headings():
    assert lt.tutorial_of("Week 3 Tutorial step response empirical transfer function modeling") == 3
    assert lt.tutorial_of("Tutorial 9 question 2 inverted pendulum reachability check") == 9
    assert lt.tutorial_of("Tutorial 6: Nyquist diagram and Bode plot stability margins") == 6
    assert lt.tutorial_of("Tutorial gain inequality calculation for stability bound") is None
    assert lt.tutorial_of("Observer gain vector definition and setup") is None


def test_page_headings_are_found():
    md = "### p. 2 · Week 2 Tutorial cruise control\ntext\n### p. 10 · Continuation of the derivation\n"
    assert [(int(p), t) for p, t in lt.PAGE_HEADING.findall(md)] == [
        (2, "Week 2 Tutorial cruise control"), (10, "Continuation of the derivation")]
