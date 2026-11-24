"""lecture_examples.py: parsing the model's reply, without the answer and within the PDF's pages."""
from __future__ import annotations

import lecture_examples as le

REPLY = """### p.8 · Link power budget
**Type:** worked example
**Question:** 4 connectors at 1 dB each, a 10 dB margin, $P_R = -10$ dBm. Find the minimum launched power.
**Answer on the slides:** pp. 8-9

### p.40 · Beyond the end
**Type:** exercise
**Question:** something
**Answer on the slides:** none
"""


def test_parse_keeps_fields_and_drops_pages_past_the_end():
    items, bad = le.parse(REPLY, 20)
    assert [i["page"] for i in items] == [8]
    assert items[0]["type"] == "worked example" and items[0]["answer"] == "pp. 8-9"
    assert "$P_R = -10$ dBm" in items[0]["question"]
    assert bad and "past page 20" in bad[0]


def test_parse_none_and_garbage():
    assert le.parse("NONE", 10) == ([], [])
    items, bad = le.parse("Here are the examples I found!", 10)
    assert items == [] and bad
