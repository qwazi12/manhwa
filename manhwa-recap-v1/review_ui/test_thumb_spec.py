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


# ------------------------------------------------------------------ A2
def _sharpness(img, box):
    """Mean absolute Laplacian in a region — high for sharp art, ~0 for blur."""
    import numpy as np
    from PIL import ImageFilter
    g = img.convert("L").crop(box)
    a = np.asarray(g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], 1, 128)),
                   dtype=np.float32)
    return float(np.abs(a - 128).mean())


def _portrait_project(tmp_path, face=(300, 250, 460, 430)):
    """A project whose real series cover is portrait 720x1080, sharp texture
    edge to edge, with a skin-coloured 'face' block."""
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(7)
    pdir = tmp_path / "proj"
    (pdir / "pages").mkdir(parents=True)
    (pdir / "crops").mkdir()
    im = Image.new("RGB", (720, 1080), (30, 60, 110))
    d = ImageDraw.Draw(im)
    for y in range(0, 1080, 6):
        for x in range(0, 720, 6):
            v = rnd.randint(0, 255)
            d.rectangle([x, y, x + 5, y + 5], fill=(v, 255 - v, (v * 3) % 256))
    d.rectangle(face, fill=(232, 180, 150))
    im.save(pdir / "pages" / "_cover.png")
    return pdir, [face[0] / 720, face[1] / 1080, face[2] / 720, face[3] / 1080]


def test_no_pillarbox_when_cover_is_portrait(tmp_path):
    from PIL import Image, ImageFilter
    pdir, fbox = _portrait_project(tmp_path)
    out = os.path.join(ART, "a2_portrait_cover.png")
    concept = {"composition": "cover-badge", "anchor_image": "pages/_cover.png",
               "chapter": "358", "palette": {"accent": [200, 30, 30]},
               "cover_face_box": fbox}
    ts.render_concept(str(pdir), concept, {}, out)
    im = Image.open(out)
    assert im.size == (1280, 720)
    centre = _sharpness(im, (440, 120, 840, 600))
    left = _sharpness(im, (0, 120, 192, 600))
    right = _sharpness(im, (1088, 120, 1280, 600))
    # negative control: the metric must catch a blurred side bar
    blurred = im.filter(ImageFilter.GaussianBlur(20))
    assert _sharpness(blurred, (0, 120, 192, 600)) < 0.25 * centre
    assert left > 0.6 * centre and right > 0.6 * centre, (left, centre, right)
    assert not concept.get("_backdrop")
    # the face is anchored into the frame: its colour is present in the output
    px = im.convert("RGB").load()
    skin = sum(1 for y in range(0, 720, 4) for x in range(0, 1280, 4) if px[x, y] == (232, 180, 150))
    assert skin > 500, skin


# ------------------------------------------------------------------ A5
def test_cover_logo_band_and_corner_mark_are_removed(tmp_path):
    """The cover's own title logo (a wide block at the bottom) is trimmed off and
    a corner watermark is inpainted: neither colour survives in the render."""
    from PIL import Image, ImageDraw
    pdir, fbox = _portrait_project(tmp_path)
    cov = Image.open(pdir / "pages" / "_cover.png").convert("RGB")
    d = ImageDraw.Draw(cov)
    LOGO, MARK = (255, 255, 40), (20, 20, 200)   # never in the noise (its r+g is 255)
    d.rectangle([100, 800, 620, 1040], fill=LOGO)          # title logo, bottom 26%
    d.rectangle([560, 10, 710, 50], fill=MARK)             # site badge, top-right
    cov.save(pdir / "pages" / "_cover.png")
    blocks = [[100 / 720, 800 / 1080, 620 / 720, 1040 / 1080], [560 / 720, 10 / 1080, 710 / 720, 50 / 1080]]
    out = os.path.join(ART, "a5_logo_trim.png")
    concept = {"composition": "cover-badge", "anchor_image": "pages/_cover.png", "chapter": "358",
               "palette": {"accent": [200, 30, 30]}, "cover_text_blocks": blocks}
    ts.render_concept(str(pdir), concept, {}, out)
    px = Image.open(out).convert("RGB").load()
    near = lambda p, c: sum(abs(a - b) for a, b in zip(p, c)) < 60
    logo = sum(1 for y in range(0, 720, 3) for x in range(0, 1280, 3) if near(px[x, y], LOGO))
    mark = sum(1 for y in range(0, 720, 3) for x in range(0, 1280, 3) if near(px[x, y], MARK))
    assert logo == 0 and mark == 0, (logo, mark)
    assert concept["_cover_trim"]["bottom_pct"] >= 26 and concept["_cover_trim"]["marks_inpainted"] == 1
    # negative control: with the trim switched off (style-pack override 0/0) the
    # logo colour IS found, so the check above is measuring something real
    concept2 = dict(concept, cover_face_box=[0.2, 0.75, 0.8, 0.95])
    ts.render_concept(str(pdir), concept2, {"cover_trim": {"top_pct": 0, "bottom_pct": 0}},
                      os.path.join(ART, "a5_logo_untrimmed_control.png"))
    px2 = Image.open(os.path.join(ART, "a5_logo_untrimmed_control.png")).convert("RGB").load()
    assert sum(1 for y in range(0, 720, 3) for x in range(0, 1280, 3) if near(px2[x, y], LOGO)) > 500
