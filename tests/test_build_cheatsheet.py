"""build_cheatsheet.py: the page budget and the print preparation (no AI, no LaTeX)."""
from __future__ import annotations

import build_cheatsheet as bc
import pytest


def unit(sheet: str) -> dict:
    return {"code": "ABCD1003", "exam": {"notes_sheet": sheet}}


def test_sides_from_the_notes_sheet_rule():
    assert bc.sides(unit("1 x A4 double-sided")) == 2
    assert bc.sides(unit("2 x A4")) == 4
    assert bc.sides(unit("1 x A4 single-sided")) == 1


def test_open_book_unit_is_refused():
    with pytest.raises(SystemExit):
        bc.sides(unit(""))


def test_prepare_flattens_tables_and_display_maths():
    md = ("---\nunit: ABCD1003\n---\n# ABCD1003 notes sheet\n"
          "| What | Formula |\n|---|---|\n| Overshoot | $M_p = e^{-\\pi\\zeta}$ |\n"
          "$$t_s = 4/\\sigma$$ (2%)\n**Use:** specs\n[[note|Label]]\n")
    out = bc.prepare(md)
    assert "unit:" not in out                                  # front matter gone
    assert "- What · Formula" in out and "- Overshoot · $M_p = e^{-\\pi\\zeta}$" in out
    assert "|---" not in out
    assert "$t_s = 4/\\sigma$ (2%)" in out and "$$" not in out
    assert "Label" in out and "[[" not in out
