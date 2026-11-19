"""check_personal.py: the pre-commit personal-data check."""
from __future__ import annotations

import check_personal as cp

# Built in pieces, so this file doesn't trip the very check it tests.
FAKE_SID = "5" + "12345678"
FAKE_MAIL = "someone@" + "gmail.com"


def test_generic_patterns_catch_ids_emails_and_user_folders(tmp_path):
    pats = cp.patterns(tmp_path)                       # no share.yaml: generic only
    texts = {"a.md": f"SID {FAKE_SID}\nmail {FAKE_MAIL}\npath C:\\Users\\someone\\x\nok line",
             "b.md": "placeholder C:\\Users\\<you>\\OneDrive is fine"}
    assert cp.hits(texts, pats) == ["a.md:1: pattern #0", "a.md:2: pattern #1", "a.md:3: pattern #2"]


def test_own_patterns_come_from_share_yaml(tmp_path):
    yaml_text = "personal_patterns: ['(?i)\\bzed\\b']\npublish_block_patterns: ['WXYZ\\d{4}']\n"
    (tmp_path / "share.yaml").write_text(yaml_text, "utf-8")
    pats = cp.patterns(tmp_path)
    assert len(pats) == len(cp.GENERIC) + 2
    found = cp.hits({"c.py": "# by Zed\nunit WXYZ1234\n"}, pats)
    assert found == [f"c.py:1: pattern #{len(cp.GENERIC)}", f"c.py:2: pattern #{len(cp.GENERIC) + 1}"]


def test_hits_never_contain_the_matching_text():
    assert FAKE_SID not in "".join(cp.hits({"a.md": FAKE_SID}, cp.GENERIC))


def test_the_repo_itself_is_clean():
    assert cp.hits(cp.files(staged=False), cp.GENERIC) == []
