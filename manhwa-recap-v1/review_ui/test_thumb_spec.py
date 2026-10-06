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


# ------------------------------------------------------------------ A3 / A4
SKIN = (232, 180, 150)


def _panel(tmp_path, name, face_px, size=(1600, 1000), bubble_px=None):
    """A synthetic chapter panel: soft noise (never pure black/yellow/white), a
    flat skin-coloured face block and optionally a white speech bubble."""
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(3)
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(0, size[1], 10):
        for x in range(0, size[0], 10):
            v = rnd.randint(60, 140)
            d.rectangle([x, y, x + 9, y + 9], fill=(v, v + 10, v + 30))
    d.rectangle(face_px, fill=SKIN)
    if bubble_px:
        d.ellipse(bubble_px, fill=(255, 255, 255))
    p = tmp_path / name
    im.save(p)
    W_, H_ = size
    nb = lambda b: [b[0] / W_, b[1] / H_, b[2] / W_, b[3] / H_]
    return str(p), nb(face_px), (nb(bubble_px) if bubble_px else None)


def _bbox_of(img, colour, tol=0):
    px = img.convert("RGB").load()
    xs, ys = [], []
    for y in range(0, img.height, 2):
        for x in range(0, img.width, 2):
            if sum(abs(a - b) for a, b in zip(px[x, y], colour)) <= tol:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _hero(path, face, **kw):
    c = {"composition": "panel-hero", "focal_file": path, "face_box": face, "face_is_mc": True,
         "overlay_text": "HE WON", "chapter": "358", "palette": {"accent": [200, 30, 30]}}
    c.update(kw)
    return c


def test_text_and_face_zones_do_not_intersect(tmp_path):
    from PIL import Image
    # the face sits on the LEFT of the panel, right where the hook goes
    path, face, _ = _panel(tmp_path, "left_face.png", (180, 380, 520, 760))
    out = os.path.join(ART, "a4_left_face_mirrored.png")
    c = _hero(path, face)
    ts.render_concept(str(tmp_path), c, {}, out)
    im = Image.open(out)
    skin = _bbox_of(im, SKIN)
    text = _bbox_of(im.crop((0, 400, 1280, 720)), YELLOW)
    assert skin and text
    text = (text[0], text[1] + 400, text[2], text[3] + 400)
    # measured on the pixels: the hook's yellow and the face never overlap
    assert not ts._intersects(skin, text), (skin, text)
    px = im.convert("RGB").load()
    inside = sum(1 for y in range(skin[1], skin[3]) for x in range(skin[0], skin[2]) if px[x, y] == YELLOW)
    assert inside == 0
    assert c["_mirrored"] is True                      # it had to mirror to get there
    assert (skin[0] + skin[2]) / 2 > 640               # the face moved to the right
    # and the zones themselves never overlap
    assert not ts._intersects(ts.ZONES["text"], ts.ZONES["face"])


def test_arrow_tip_stops_short_of_the_face(tmp_path):
    import numpy as np
    from PIL import Image
    path, face, _ = _panel(tmp_path, "right_face.png", (1080, 260, 1300, 520))
    with_arrow = os.path.join(ART, "a3_arrow.png")
    c = _hero(path, face)
    ts.render_concept(str(tmp_path), c, {}, with_arrow)
    no_arrow = os.path.join(tmp_path, "no_arrow.png")
    ts.render_concept(str(tmp_path), _hero(path, face, face_is_mc=False), {}, no_arrow)
    a = np.asarray(Image.open(with_arrow).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(no_arrow).convert("RGB"), dtype=np.int16)
    ys, xs = np.nonzero(np.abs(a - b).sum(axis=2) > 30)      # the arrow's own pixels
    assert len(xs) > 2000, "no arrow drawn"
    skin = _bbox_of(Image.open(with_arrow), SKIN)
    fx0, fy0, fx1, fy1 = skin

    def dist(x, y):
        dx = np.maximum(np.maximum(fx0 - x, 0), x - fx1)
        dy = np.maximum(np.maximum(fy0 - y, 0), y - fy1)
        return np.sqrt(dx * dx + dy * dy)
    d_all = dist(xs, ys)
    yel = (np.abs(a[ys, xs] - np.array(YELLOW)).sum(axis=1) < 40)
    d_tip = d_all[yel].min()
    assert d_all.min() >= 10, d_all.min()               # outline included: never touches
    assert 17 <= d_tip <= 31, d_tip                     # the yellow tip: 18-30 px short
    # one arrow, 200-340 px long, from the text block toward the face
    (sx, sy), (ex, ey) = c["_arrow"]
    assert 200 <= ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5 <= 341
    assert ex > sx and ey < sy


def test_bubble_under_the_hook_is_inpainted(tmp_path):
    from PIL import Image
    # a white speech bubble exactly where the hook goes (bottom-left)
    path, face, bub = _panel(tmp_path, "bubble.png", (1080, 260, 1300, 520), bubble_px=(120, 600, 700, 820))
    out = os.path.join(ART, "a4_bubble_inpainted.png")
    c = _hero(path, face, bubbles=[bub])
    ts.render_concept(str(tmp_path), c, {}, out)
    ctrl = os.path.join(ART, "a4_bubble_control.png")
    ts.render_concept(str(tmp_path), _hero(path, face), {}, ctrl)
    white = lambda p: _bbox_of(Image.open(p).crop((0, 440, 700, 720)), (255, 255, 255), tol=12)
    assert c["_bubbles_inpainted"] == 1
    assert white(ctrl) is not None                      # control: the bubble is there
    assert white(out) is None                           # inpainted: no bubble pixels left


# ------------------------------------------------------------------ A6
def _face_project(tmp_path):
    """Five panels + face_boxes.json: a good MC close-up, a near-duplicate of
    it (re-encoded, shifted 3 px), a different good panel, a near-black panel
    and a panel whose only face is tiny."""
    import json as _j
    from PIL import Image
    pdir = tmp_path / "fp"
    (pdir / "crops").mkdir(parents=True)
    a, fa, _ = _panel(tmp_path, "a.png", (1050, 220, 1380, 620))
    Image.open(a).save(pdir / "crops" / "p1.png")
    im = Image.open(a).convert("RGB")
    dup = Image.new("RGB", im.size)
    dup.paste(im.crop((3, 0, im.width, im.height)), (0, 0))
    dup.save(pdir / "crops" / "p2.jpg", quality=70)
    Image.open(pdir / "crops" / "p2.jpg").save(pdir / "crops" / "p2.png")
    import random
    rnd = random.Random(11)
    other = Image.new("RGB", (1600, 1000), (190, 120, 60))
    from PIL import ImageDraw
    d = ImageDraw.Draw(other)
    for _ in range(400):
        x, y = rnd.randint(0, 1590), rnd.randint(0, 990)
        d.rectangle([x, y, x + 9, y + 9], fill=(rnd.randint(150, 230), 90, 40))
    d.rectangle((1000, 200, 1360, 640), fill=(120, 200, 230))
    other.save(pdir / "crops" / "p3.png")
    Image.new("RGB", (1600, 1000), (6, 6, 8)).save(pdir / "crops" / "p4.png")
    t, ft, _ = _panel(tmp_path, "t.png", (800, 400, 840, 440))
    Image.open(t).save(pdir / "crops" / "p5.png")
    descs = [{"panel_id": "p%d" % i, "file": "p%d.png" % i, "width": 1600, "height": 1000,
              "ok": True, "visual_description": "", "ocr_text": ""} for i in range(1, 6)]
    (pdir / "descriptions.json").write_text(_j.dumps(descs))
    import face_boxes as fbm
    rec = lambda f, box, mc=True: {"key": fbm._key(str(pdir / "crops" / f)), "analysed": True,
                                   "faces": [{"box": box, "name": "", "is_mc": mc}] if box else [],
                                   "bubbles": [], "bubble_coverage": 0.0, "watermark": False}
    faces = {"p1": rec("p1.png", fa), "p2": rec("p2.png", fa), "p3": rec("p3.png", [1000 / 1600, 0.2, 1360 / 1600, 0.64]),
             "p4": rec("p4.png", fa), "p5": rec("p5.png", ft)}
    (pdir / "face_boxes.json").write_text(_j.dumps(faces))
    return pdir


def test_near_duplicate_panels_are_excluded(tmp_path):
    import numpy as np
    from PIL import Image
    pdir = _face_project(tmp_path)
    kept, rep = ts.rank_panels_report(str(pdir))
    ids = [k["panel_id"] for k in kept]
    assert ("p1" in ids) != ("p2" in ids), ids              # one of the near-dupes, never both
    assert rep["rejects"].get("duplicate") == 1
    assert rep["rejects"].get("dark") == 1 and rep["rejects"].get("no_face") == 1
    assert all(k["face_box"] for k in kept)                  # A6: face_box on every panel
    # render the two picks side by side (3D); the halves must be different art
    c = {"composition": "two-panels", "focal_file": kept[0]["file"], "face_box": kept[0]["face_box"],
         "second_file": kept[1]["file"], "second_face_box": kept[1]["face_box"],
         "chapter": "7", "palette": {"accent": [200, 30, 30]}}
    out = os.path.join(ART, "a6_no_near_dupes.png")
    ts.render_concept(str(pdir), c, {}, out)
    a = np.asarray(Image.open(out).convert("RGB"), dtype=np.int16)
    left, right = a[100:600, 40:600], a[100:600, 680:1240]
    assert np.abs(left.mean(axis=(0, 1)) - right.mean(axis=(0, 1))).sum() > 60
    # control: the two near-dupes really are the same picture to the eye
    h1, h2 = ts._dhash(str(pdir / "crops" / "p1.png")), ts._dhash(str(pdir / "crops" / "p2.png"))
    assert ts._lookalike(h1, h2)


# ------------------------------------------------------------------ A7
def test_default_composition_is_panel_hero(tmp_path):
    import colorsys
    from PIL import Image
    pdir = _face_project(tmp_path)
    meta = {"series": "Test Series", "chapter": "362"}
    style = {"palette": {"bg": [14, 16, 22], "accent": [210, 30, 30]}}
    cs = ts.rank_concepts(ts.build_concepts(str(pdir), meta, style, "HE WON - Test Series Chapter 362"),
                          style, "HE WON")
    rec = [c for c in cs if c.get("recommended")]
    assert len(rec) == 1 and rec[0]["composition"] == "panel-hero" == ts.DEFAULT_COMPOSITION
    assert ts.build_style_pack(str(pdir), meta)["composition"] == "panel-hero"
    out = os.path.join(ART, "a7_default_panel_hero.png")
    ts.render_concept(str(pdir), rec[0], style, out)
    im = Image.open(out).convert("RGB")
    px = im.load()
    assert im.size == (1280, 720)
    # the layout, measured: face in the right half, hook bottom-left, badge
    # top-left in the accent colour, an arrow between hook and face
    face_col = SKIN if rec[0]["focal_panel"] in ("p1", "p2") else (120, 200, 230)
    skin = _bbox_of(im, face_col)
    assert skin and (skin[0] + skin[2]) / 2 > 700, (rec[0]["focal_panel"], skin)
    hook = _bbox_of(im.crop((0, 440, 700, 720)), YELLOW)
    assert hook and hook[0] < 80
    badge = sum(1 for y in range(28, 90, 2) for x in range(28, 200, 2) if px[x, y] == (210, 30, 30))
    assert badge > 300
    arrow = sum(1 for y in range(100, 440, 2) for x in range(300, 900, 2) if px[x, y] == YELLOW)
    assert arrow > 50
    assert rec[0]["_arrow"]
    # ↻ rotates: round 1 starts further on and shifts the hue by 12 degrees
    cs1 = ts.build_concepts(str(pdir), meta, style, "HE WON", round_=1)
    assert cs1[0]["composition"] != "panel-hero"
    h0 = colorsys.rgb_to_hls(*[v / 255 for v in (210, 30, 30)])[0]
    h1 = colorsys.rgb_to_hls(*[v / 255 for v in cs1[0]["palette"]["accent"]])[0]
    assert abs(((h1 - h0 + 0.5) % 1.0) - 0.5) * 360 == pytest.approx(12, abs=2)
