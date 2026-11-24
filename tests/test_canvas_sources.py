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


def test_renumber_is_simultaneous_and_targeted():
    m = {"L5": "L4", "L6": "L5", "L7b": "L6", "L7a": "L7"}
    progress = ("- [ ] L5 · [[ABCD1001_L5|recall]]\n- [x] L6 · [[ABCD1001_L6|recall]]\n"
                "- [ ] L7a · [[ABCD1001_L7a|recall]]\n- [ ] L7b · [[ABCD1001_L7b|recall]]\n"
                "- [ ] L15 · [[ABCD1001_L15|recall]]\n"
                "- [ ] L5 · [[ABCD1002_L5|recall]]\n")             # another unit's row in the same progress.md
    assert ad.renumber_text(progress, "ABCD1001", m) == (
        "- [ ] L4 · [[ABCD1001_L4|recall]]\n- [x] L5 · [[ABCD1001_L5|recall]]\n"
        "- [ ] L7 · [[ABCD1001_L7|recall]]\n- [ ] L6 · [[ABCD1001_L6|recall]]\n- [ ] L15 · [[ABCD1001_L15|recall]]\n"
        "- [ ] L5 · [[ABCD1002_L5|recall]]\n")
    hub = "| L5 | [pdf](x) | [[units/a/recall/ABCD1001_L5\\|ABCD1001_L5]] |\n| L6 | y |"
    want = "| L4 | [pdf](x) | [[units/a/recall/ABCD1001_L4\\|ABCD1001_L4]] |\n| L5 | y |"
    assert ad.renumber_text(hub, "ABCD1001", m) == want
    recall = "---\nlecture: L6\n---\n# ABCD1001 L6: FM demodulation\nsee (L5 p.3) and L6 p.12; L6 in a sentence"
    assert ad.renumber_text(recall, "ABCD1001", m) == (
        "---\nlecture: L5\n---\n# ABCD1001 L5: FM demodulation\nsee (L4 p.3) and L5 p.12; L6 in a sentence")


def test_superseding_needs_the_same_kind_and_the_same_text():
    from pathlib import Path
    questions = "Week 7 Tutorial 1. Consider the model P(s) = (s-2)/((s+1)(s+4)). (a) Show that the zero constrains S."
    pool = {Path("T7-5.pdf"): ("solutions", questions),               # same name pattern, but the solutions
            Path("T7-6.pdf"): ("sheet", questions + " (b) Discuss.")}
    assert ad.best_match(("sheet", questions + " (b) Discuss"), pool)[0] == Path("T7-6.pdf")
    assert ad.best_match(("sheet", ""), pool) == (None, 0.0)          # a scan (your own notes): never a copy
    other = ad.best_match(("sheet", "Mixers: Gilbert cell multiplier, additive mixing via nonlinearity"), pool)
    assert other[1] < ad.SAME_TEXT                                     # same number, different document: stays


def test_log_edit_appends_to_front_matter_lists():
    assert 'edits:\n  - "x"' in ad.log_edit("---\nedits: []\n---\n", "x")
    out = ad.log_edit('---\nedits:\n  - "a"\nother: 1\n---\n', "b")
    assert '  - "a"\n  - "b"\nother: 1' in out
    assert ad.log_edit("---\nunit: X\n---\n", "b") == "---\nunit: X\n---\n"   # no edits list: unchanged


def test_transcription_found_by_original_and_header_ignored(tmp_path, monkeypatch):
    import common
    src = tmp_path / "sources"
    src.mkdir()
    monkeypatch.setattr(common, "unit_path", lambda u, key: src)
    pdf = tmp_path / "Scan from 2026-11-12.pdf"
    renamed = src / "handwritten_crash course.md"
    renamed.write_text('---\noriginal: "file:///C:/x/Scan%20from%202026-11-12.pdf"\n---\n', "utf-8")
    assert common.transcription_for({}, pdf) == renamed                  # a renamed transcription stays the one
    assert common.transcription_for({}, tmp_path / "other.pdf") == src / "handwritten_other.md"
    a = "---\ngenerated: 2026-11-17\n---\n# X\n## Your open questions (1)\n- [ ] why?\n## Page by page\ntext\n"
    b = a.replace("2026-11-17", "2026-11-20").replace("# X", "# X (new template)")
    assert common.transcription_content(a) == common.transcription_content(b)     # header changes are not edits
    assert common.transcription_content(a) != common.transcription_content(a.replace("- [ ]", "- [x]"))


def test_prune_cache_keeps_current_copies_only(tmp_path):
    keep, stale = tmp_path / "L1_new.pdf", tmp_path / "L1_old.pdf"
    other = tmp_path / "notes.md"
    for p in (keep, stale, other):
        p.write_text("x")
    assert prune_cache(tmp_path, {keep}) == [stale]
    assert keep.exists() and not stale.exists() and other.exists()
    assert prune_cache(tmp_path / "missing", set()) == []


def test_name_key_drops_only_canvas_repeat_suffixes():
    from pathlib import Path

    from common import name_key
    assert name_key(Path("ELEC_Tutorial_6-4.pdf")) == name_key(Path("ELEC_Tutorial_6.pdf")) == "elec_tutorial_6"
    assert name_key(Path("ELEC___Lecture_9-7-1.pdf")) == "elec_lecture_9"
    assert name_key(Path("ELEC_Tutorial_6-4.pdf")) != name_key(Path("ELEC_Tutorial_7-1.pdf"))
    assert name_key(Path("Tutorial_3_Solutions.pdf")) == "tutorial_3_solutions"


def test_canvas_token_is_not_sent_to_another_host_on_redirect():
    """File downloads can redirect to a signed S3 URL, which rejects a second auth mechanism (HTTP 400)."""
    import urllib.request

    from canvas_sync import _NoAuthOffSite
    h = _NoAuthOffSite()
    req = urllib.request.Request("https://canvas.example.edu/files/1", headers={"Authorization": "Bearer x"})
    off = h.redirect_request(req, None, 302, "", {}, "https://bucket.s3.example.com/f?X-Amz-Signature=1")
    same = h.redirect_request(req, None, 302, "", {}, "https://canvas.example.edu/files/1/download")
    assert not off.has_header("Authorization")
    assert same.has_header("Authorization")
