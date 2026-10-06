"""Spec 07 (docs/audit/07_THUMBNAIL_RECIPE.md) — real pytest tests.

Every test RENDERS a real 1280x720 PNG and asserts on its pixels, not on the
code's own numbers (owner, 2026-10-06: earlier assertions passed while the
rendered output was wrong). Artifacts are kept in THUMB_ARTIFACTS (default: a
temp dir) so they can be looked at.
Run: python3 -m pytest manhwa-recap-v1/review_ui/test_thumb_spec.py -q
"""
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
import thumbnail_studio as ts  # noqa: E402

ART = os.environ.get("THUMB_ARTIFACTS") or tempfile.mkdtemp(prefix="thumbspec_")
os.makedirs(ART, exist_ok=True)
YELLOW = (255, 212, 0)


def _canvas(color=(40, 44, 60)):
    from PIL import Image
    return Image.new("RGBA", (ts.W, ts.H), color + (255,))


def _yellow_rows(img, x0=0, x1=None):
    """Rows that contain the hook's exact fill colour."""
    px = img.convert("RGB").load()
    x1 = x1 or img.width
    rows = []
    for y in range(img.height):
        for x in range(x0, x1, 2):
            if px[x, y] == YELLOW:
                rows.append(y)
                break
    return rows


def _render_hook(text, name, zone=None):
    img = _canvas()
    zx0, zy0, zx1, zy1 = zone or ts.ZONES["text"]
    lay = ts.hook_layout(text, zx1 - zx0, zy1 - zy0)
    assert lay is not None
    ts.draw_hook(img, lay, zx0, zy1)
    out = os.path.join(ART, name)
    img.convert("RGB").save(out)
    from PIL import Image
    return lay, Image.open(out)


def test_cap_height_scales_with_word_count():
    assert [ts.cap_height_for(n) for n in (1, 2, 3, 4)] == [180, 132, 108, 90]
    assert ts.cap_height_for(9) >= ts.CAP_FLOOR
    # rendered: a single-line capitals-only hook's yellow height IS its cap height
    measured = {}
    for text in ("GENIUS", "HE WON", "VILLAIN NUMBER ONE", "THE FATED VILLAIN WINS"):
        lay, im = _render_hook(text, f"a1_cap_{len(text.split())}w.png", zone=(28, 40, 1252, 692))
        rows = _yellow_rows(im)
        if len(lay["lines"]) == 1:
            measured[len(text.split())] = max(rows) - min(rows) + 1
    for words, h in measured.items():
        assert abs(h - ts.cap_height_for(words)) <= 4, (words, h)
    assert measured[1] > measured[2] > measured[3] > measured[4]


def test_text_never_shrinks_below_floor():
    # a 4-word hook into the spec text zone, then into a zone too narrow for all 4
    for zone, name in ((ts.ZONES["text"], "a1_floor_zone.png"), ((28, 470, 380, 692), "a1_floor_narrow.png")):
        lay, im = _render_hook("THE FATED VILLAIN RETURNS", name, zone=zone)
        assert lay["cap"] >= ts.CAP_FLOOR
        rows = _yellow_rows(im)
        # the first line's yellow extent (capitals) is at least the floor
        first = [r for r in rows if r <= min(rows) + lay["cap"] + 2]
        assert max(first) - min(first) + 1 >= ts.CAP_FLOOR - 3
    assert lay["dropped"] >= 1, "too wide at the floor must DROP words, not shrink"
    assert ts.stroke_for(90) == 9 and ts.stroke_for(180) == 14


def test_font_is_condensed_heavy_sans():
    assert ts.font_available(), ts.font_path()
    assert "Anton" in os.path.basename(ts.font_path())
