"""course_inventory.py: parsing folder names and Canvas terms, merging the sources, and the checks. No network."""
from __future__ import annotations

import course_inventory as ci


def test_folder_names_in_both_cases():
    assert ci.parse_folder("2025_SC2_ABCD2104") == ("2025", "sc2", "ABCD2104")
    assert ci.parse_folder("2026_sc1_abcd1002") == ("2026", "sc1", "ABCD1002")
    assert ci.parse_folder("Welcome lecture") is None
    assert ci.parse_folder("2023_FEIT_PEP_OLM_EC") is None


def test_sessions_and_terms():
    assert ci.norm_session("S2") == "sc2"
    assert ci.norm_session(1) == "sc1"
    assert ci.norm_session("Semester 2") == "sc2"
    assert ci.norm_session("Summer") == "summer"
    assert ci.norm_session("") is None
    assert ci.parse_term("Semester 2 2025") == ("2025", "sc2")
    assert ci.parse_term("2026 S1") == ("2026", "sc1")
    assert ci.parse_term("ABCD2104_S2C_ND_CC_2025") == ("2025", "sc2")
    assert ci.parse_term("Summer School 2026") == ("2026", "summer")
    assert ci.parse_term("Data Analysis 1", "2025-08-01T00:00:00Z") == ("2025", "sc2")   # 'Analysis 1' is not S1
    assert ci.parse_term("", None) == (None, None)


def test_canvas_course_needs_a_unit_code():
    c = {"id": 7, "name": "ABCD2104 Circuits", "course_code": "ABCD2104", "term": {"name": "Semester 2 2025"}}
    assert ci.parse_canvas_course(c) == {"code": "ABCD2104", "year": "2025", "session": "sc2", "canvas_id": 7,
                                         "canvas_name": "ABCD2104 Circuits", "restricted": False}
    assert ci.parse_canvas_course({"id": 8, "name": "Faculty welcome hub"}) is None


def test_merge_and_checks():
    results = [{"code": "ABCD2104", "year": "2025", "session": "sc2", "cp": 6, "level": 2},
               {"code": "WXYZ1001", "year": "2024", "session": "sc1", "cp": 6, "level": 1}]
    canvas = [{"code": "ABCD2104", "year": "2025", "session": "sc2", "canvas_id": 7, "canvas_name": "x",
               "restricted": False},
              {"code": "QRST3001", "year": None, "session": None, "canvas_id": 9, "canvas_name": "y",
               "restricted": True}]
    onedrive = [{"code": "ABCD2104", "year": "2025", "session": "sc2", "folder": "C:/od/.old/2025_SC2_ABCD2104",
                 "where": "old", "files": 3, "canvas_mirror": False}]
    units = {u["id"]: u for u in ci.merge(results, canvas, onedrive, {"2025_sc2_abcd2104": {"indexed"}})}
    assert set(units) == {"2025_sc2_abcd2104", "2024_sc1_wxyz1001", "?_?_qrst3001"}
    assert units["2025_sc2_abcd2104"]["pipeline"] == ["indexed"]
    assert ci.issues(units["2025_sc2_abcd2104"]) == ["no Canvas mirror yet"]
    assert ci.issues(units["2024_sc1_wxyz1001"]) == ["in results, no OneDrive folder",
                                                     "in results, not found on Canvas"]
    assert "on Canvas, not in results" in ci.issues(units["?_?_qrst3001"])
    assert "Canvas access closed" in ci.issues(units["?_?_qrst3001"])


def test_undated_canvas_row_joins_the_one_unit_with_its_code():
    results = [{"code": "ABCD2104", "year": "2025", "session": "sc2", "cp": 6, "level": 2}]
    canvas = [{"code": "ABCD2104", "year": None, "session": None, "canvas_id": 7, "canvas_name": "x",
               "restricted": False}]
    units = ci.merge(results, canvas, [], {})
    assert len(units) == 1 and units[0]["canvas"][0]["canvas_id"] == 7


def test_render_never_shows_marks():
    units = ci.merge([{"code": "ABCD2104", "year": "2025", "session": "sc2", "cp": 6, "level": 2}], [], [], {})
    md = ci.render(units, [], {"results": "1 rows"})
    assert "share: never" in md and "| 2025_sc2_abcd2104 | yes |" in md


def test_past_courses_skip_configured_closed_and_undated_units(tmp_path):
    from canvas_sync import past_courses
    cw = tmp_path / "cw"
    inv = {"units": [
        {"id": "2025_sc2_abcd2104", "code": "ABCD2104", "pipeline": ["indexed"],
         "canvas": [{"canvas_id": 7, "restricted": False}],
         "onedrive": [{"folder": str(cw / "x" / "2025_sc2_abcd2104"), "where": "current"},
                      {"folder": str(cw / ".old" / "2025_SC2_ABCD2104"), "where": "old"}]},
        {"id": "2024_sc1_wxyz1001", "code": "WXYZ1001", "pipeline": [],
         "canvas": [{"canvas_id": 8, "restricted": False}], "onedrive": []},
        {"id": "2026_sc2_abcd3405", "code": "ABCD3405", "pipeline": ["config", "vault"],
         "canvas": [{"canvas_id": 9, "restricted": False}], "onedrive": []},
        {"id": "2023_sc1_qrst1000", "code": "QRST1000", "pipeline": [],
         "canvas": [{"canvas_id": 10, "restricted": True}], "onedrive": []},
        {"id": "?_?_qrst3001", "code": "QRST3001", "pipeline": [],
         "canvas": [{"canvas_id": 11, "restricted": False}], "onedrive": []}]}
    got = {u["id"]: u for u in past_courses(inv, cw)}
    assert set(got) == {"2025_sc2_abcd2104", "2024_sc1_wxyz1001"}
    assert got["2025_sc2_abcd2104"]["onedrive_folder"] == ".old/2025_SC2_ABCD2104"        # the .old folder first
    assert got["2024_sc1_wxyz1001"]["onedrive_folder"] == ".old/2024_SC1_WXYZ1001"
    assert got["2024_sc1_wxyz1001"]["new_folder"] and got["2024_sc1_wxyz1001"]["sources"] == {}
    assert set(past_courses(inv, cw, only=["WXYZ1001"])[0]) >= {"canvas_id", "code"}
