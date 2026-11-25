"""unit_path on a layout-1 unit finds a file already kept where the standard layout puts it."""
from __future__ import annotations

import common


def test_layout1_unit_uses_the_standard_place_when_only_that_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "vault_unit_dir", lambda u: tmp_path)
    u = {"id": "2026_sc2_abcd1003", "code": "ABCD1003"}                 # no vault_layout: layout 1
    assert common.unit_path(u, "intel") == tmp_path / "exam_intel.md"     # neither exists: the layout-1 place
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "exam_intel.md").write_text("x", "utf-8")
    assert common.unit_path(u, "intel") == tmp_path / "reference" / "exam_intel.md"
    (tmp_path / "exam_intel.md").write_text("y", "utf-8")
    assert common.unit_path(u, "intel") == tmp_path / "exam_intel.md"     # the layout-1 file wins when it exists
