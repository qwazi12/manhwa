"""Owner, 2026-11-19: "Let's use these for the ab thumbnail tests ... We need to
add the correct chapter numbers ... thumbnails in the studio to adhere to ab
tests". A series keeps up to three wordless pictures; a video of the series
carries A (or the recorded winner) with its own chapter badge, and the A/B
plan lists A, B and C with that badge. No network.
Run: python3 -m pytest test_series_ab.py -q"""
import io
import os
import sys
import tempfile

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
import series_master as M  # noqa: E402


def _pic(colour, size=(832, 468)):
    b = io.BytesIO()
    Image.new("RGB", size, colour).save(b, "JPEG")
    return b.getvalue()


def _set(root, sid="fog-land"):
    M.ab_save(root, sid, "A", _pic((200, 30, 30)), option="52")
    M.ab_save(root, sid, "B", _pic((30, 200, 30)), option="53")
    return M.ab_save(root, sid, "C", _pic((30, 30, 200)), option="54")


def test_a_set_is_stored_at_full_size_and_listed():
    root = tempfile.mkdtemp()
    st = _set(root)
    assert sorted(st["variants"]) == ["A", "B", "C"] and st["options"] == {"A": "52", "B": "53", "C": "54"}
    assert Image.open(st["variants"]["B"]).size == M.SIZE
    assert M.ab_set(root, "no-such-series") is None


def test_a_video_carries_a_with_its_chapter_badge_then_the_winner():
    root = tempfile.mkdtemp()
    _set(root)
    out, v, fill = M.ab_thumbnail(root, "fog-land", "38-45", os.path.join(root, "t.jpg"))
    assert v == "A" and fill in M.NEON
    im = Image.open(out).convert("RGB")
    assert im.size == M.SIZE
    r, g, b = im.getpixel((1000, 600))                      # the picture itself
    assert r > 150 and g < 90 and b < 90
    assert im.getpixel((M.BADGE_X + 3, M.BADGE_Y + 40)) == (0, 0, 0) or sum(im.getpixel((M.BADGE_X + 3, M.BADGE_Y + 40))) < 60
    M.ab_winner(root, "fog-land", "C")
    assert M.ab_thumbnail(root, "fog-land", "46", os.path.join(root, "t2.jpg"))[1] == "C"
    assert M.ab_thumbnail(root, "fog-land", "46", os.path.join(root, "t3.jpg"), variant="B")[1] == "B"
    M.ab_winner(root, "fog-land", "")
    assert M.ab_set(root, "fog-land")["winner"] is None


def test_the_upload_uses_the_set_before_the_drive_master_and_falls_back_without_one():
    root = tempfile.mkdtemp()
    _set(root)
    work = os.path.join(root, "work")
    got = M.upload_thumbnail("fog-land-9299", "46", "fallback.jpg", work, ds=None, ab_root=root, ab_series="fog-land")
    assert got != "fallback.jpg" and os.path.isfile(got)
    # a series with no set, Drive not asked (ab only): the export's own thumbnail
    class _NoDrive:
        @staticmethod
        def configured():
            return False
    assert M.upload_thumbnail("x", "1", "fallback.jpg", work, ds=None, ab_root=root, ab_series="other-series") in ("fallback.jpg",)


@pytest.mark.parametrize("bad, why", [(b"not a picture", "not a picture"),
                                      (None, "16:9"), ("small", "640")])
def test_a_bad_picture_is_refused(bad, why):
    root = tempfile.mkdtemp()
    data = bad if isinstance(bad, bytes) else (_pic((1, 2, 3), (800, 800)) if bad is None else _pic((1, 2, 3), (320, 180)))
    with pytest.raises(ValueError) as e:
        M.ab_save(root, "fog-land", "A", data)
    assert why in str(e.value)
    with pytest.raises(ValueError):
        M.ab_save(root, "fog-land", "D", _pic((1, 2, 3)))


def test_the_ab_plan_and_the_video_thumbnail_follow_the_set(monkeypatch, tmp_path):
    import json
    import server
    import ingest
    import thumbnail as tb
    root = tmp_path / "projects"
    pdir = root / "fog-land-9299_ch38-45"
    (pdir / "exports").mkdir(parents=True)
    (pdir / "exports" / "final_range_38-45.mp4").write_bytes(b"x")
    (pdir / "project.json").write_text(json.dumps({"kind": "range", "series_id": "fog-land-9299", "series": "Fog Land",
                                                   "chapters_start": 38, "chapters_end": 45}))
    monkeypatch.setattr(ingest, "PROJECTS", str(root))
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(server, "_series_ab_sid", lambda p: "fog-land")
    _set(str(root))
    name = "final_range_38-45.mp4"
    assert server._series_ab_apply(str(pdir), name) == "A"
    rec = tb.get(str(pdir), name)
    assert rec["source"] == "auto" and (rec["width"], rec["height"]) == M.SIZE
    plan = server._abtest_plan(str(pdir), name, "[38-45] A Science TEACHER Fights To Survive Prison | Fog Land")
    assert plan["thumbnails"] == ["A", "B", "C"] and plan["type"] == "thumbnail"
    assert "variant=B" in plan["thumbnail_urls"]["B"] and "CH 38-45" in plan["thumbnail_designs"]["C"]
    # a thumbnail uploaded by hand for this video is kept
    tb.save(str(pdir), name, _pic((9, 9, 9), (1280, 720)), source="upload")
    assert server._series_ab_apply(str(pdir), name) is None
    assert server._series_ab_apply(str(pdir), name, actor="owner") == "A"
