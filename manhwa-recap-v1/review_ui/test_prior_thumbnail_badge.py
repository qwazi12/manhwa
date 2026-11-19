"""Owner review, 2026-11-19: the merges FV 361-369 and Bountiful 85-92 carried
the posted thumbnail with the new "CH a-b" badge drawn at the standard size over
a bigger posted badge, so "367" / "91" still showed beside it; and every
re-badge changed colour because the colour sample sat on the old "CH" text.
The re-badge now takes the posted badge's own colour and covers all of it.
No network. Run: python3 -m pytest test_prior_thumbnail_badge.py -q"""
import os
import sys

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
import server  # noqa: E402
import thumbnail_studio as ts  # noqa: E402


def _posted(cap, colour=(240, 100, 49), label="CH 361-367"):
    im = Image.new("RGBA", (ts.W, ts.H), (30, 60, 140, 255))
    box = ts.draw_badge(im, label, colour, cap=cap, keep_colour=True)
    return im, box


@pytest.mark.parametrize("cap", [38, 62, 76])
def test_the_new_badge_covers_the_posted_one_in_its_colour(cap):
    im, old = _posted(cap)
    colour, right, bottom = server._posted_badge(im)
    assert max(abs(a - b) for a, b in zip(colour, (240, 100, 49))) <= 2
    new = ts.draw_badge(im, "CH 361-369", colour, cap=server._covering_cap("CH 361-369", right, bottom),
                        keep_colour=True)
    assert new[2] >= old[2] and new[3] >= old[3]          # nothing of the old badge left outside


def test_a_thumbnail_without_a_flat_badge_is_left_to_the_old_path():
    im = Image.effect_noise((ts.W, ts.H), 80).convert("RGBA")
    assert server._posted_badge(im) is None


def test_flat_art_bigger_than_a_badge_is_not_taken_for_one():
    im = Image.new("RGBA", (ts.W, ts.H), (0, 0, 0, 255))
    assert server._posted_badge(im) is None


def test_a_curly_apostrophe_stays_in_the_thumbnail_hook():
    """ORV 309-311 (2026-11-19): "Omniscient Reader’s Viewpoint" became "READER S"."""
    assert "READER S" not in ts.hook_from_title("Omniscient Reader’s Viewpoint")
    assert "READER'S" in ts.hook_from_title("Omniscient Reader’s Viewpoint")


def test_a_short_merge_gets_one_timestamp_per_chapter():
    """ORV 309-311 (2026-11-19): chapters grouped by 5 left a 3-chapter merge with none."""
    import description_blocks as db
    import range_compile as rc
    marks = [(309, 0.0), (310, 483.6), (311, 975.4)]
    assert db.block3(rc.arcs(marks, every=1)).splitlines() == ["0:00 Chapter 309", "8:03 Chapter 310",
                                                              "16:15 Chapter 311"]
