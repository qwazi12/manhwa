"""Offline orientation/framing regressions. Synthetic artwork only, no AI calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "hyperframes")]
import panel_framing as pf
import render_segments as rs
import fast_render as fr
import storyboard_edit as se
import board_data
import media_qa
import server


def picture(path, size=(120, 80), exif=1):
    im = Image.new("RGB", size, "red")
    im.paste("blue", (0, size[1] // 2, size[0], size[1]))
    im.paste("lime", (size[0] // 2, 0, size[0], size[1] // 2))
    meta = Image.Exif(); meta[274] = exif
    im.save(path, exif=meta)
    return path


def segment(path, **kw):
    return {"seg_index": 0, "panel_id": "art", "panel_file": str(path),
            "start": 0, "end": 1, "dur": 1, "beats": [], "user_included": True,
            "clip": "clips/seg_000.mp4", **kw}


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_preview_and_both_renderers_share_oriented_pixels(tmp_path, monkeypatch, degrees):
    src = picture(tmp_path / "art.png")
    before = src.read_bytes()
    seg = segment(src, rotation_deg=degrees)
    assets = tmp_path / "assets"; assets.mkdir()
    monkeypatch.setattr(rs, "ASSETS", str(assets))
    rs.stage_assets(seg, str(tmp_path))
    staged = np.asarray(Image.open(assets / pf.asset_name(seg)).convert("RGB"))
    preview = np.asarray(server._framed_panel(seg).convert("RGB"))
    fast = fr._read_bgr(str(src), degrees)[:, :, ::-1]
    assert np.array_equal(preview, staged)
    assert np.array_equal(fast, staged)
    assert rs.card_regime(seg, str(src))[3:] == (staged.shape[1], staged.shape[0])
    assert f'assets/{pf.asset_name(seg)}' in rs.seg_html(seg, str(tmp_path))
    assert src.read_bytes() == before


@pytest.mark.parametrize("exif", [2, 5, 6, 8])
def test_exif_then_manual_turn_not_double_applied(tmp_path, monkeypatch, exif):
    src = picture(tmp_path / "art.png", exif=exif)
    seg = segment(src, rotation_deg=90)
    monkeypatch.setattr(rs, "ASSETS", str(tmp_path))
    rs.stage_assets(seg, str(tmp_path))
    staged = Image.open(tmp_path / pf.asset_name(seg))
    assert staged.getexif().get(274, 1) == 1
    g = pf.geometry(src, 90)
    assert staged.size == (g["width"], g["height"])
    assert np.array_equal(np.asarray(staged), np.asarray(server._framed_panel(seg)))


def test_clockwise_direction_and_crop_coordinate_system(tmp_path):
    src = picture(tmp_path / "art.png")
    seg = segment(src, rotation_deg=90, crop_bbox_norm=[0, 0, .5, 1])
    # Blue lower half moves to the LEFT under a clockwise quarter turn.
    im = server._framed_panel(seg)
    assert im.size == (40, 120)
    assert im.getpixel((20, 60)) == (0, 0, 255)
    assert server.seg_crop_rect(seg) == (0, 0, 40, 120)


def test_same_native_bird_geometry_flags_narrow_fit_without_guessing_rotation(tmp_path):
    src = picture(tmp_path / "art.png", (900, 1924))
    seg = segment(src, width=1924, height=900)  # stale manifest must not win
    before = deepcopy(seg)
    result = pf.inspect(seg)
    assert result["rotation_deg"] == 0 and result["regime"] == "fit"
    assert result["geometry"]["width"] == 900
    assert [x["rule"] for x in result["findings"]] == ["F2-narrow-card"]
    assert seg == before
    turned = pf.inspect({**seg, "rotation_deg": 90})
    assert (turned["geometry"]["width"], turned["geometry"]["height"]) == (1924, 900)
    assert turned["regime"] == "fit" and not turned["findings"]


def test_landscape_crop_and_scroll_diagnostics(tmp_path):
    wide = picture(tmp_path / "wide.png", (900, 600))
    assert pf.inspect(segment(wide))["findings"] == []
    result = pf.inspect(segment(wide, crop_bbox_norm=[.4, 0, .6, 1]))
    assert result["findings"][0]["rule"] == "F1-wide-to-portrait"
    tall = picture(tmp_path / "tall.png", (300, 900))
    result = pf.inspect(segment(tall))
    assert result["regime"] == "scroll"
    assert result["findings"][0]["rule"] == "F3-scroll-review"


@pytest.mark.parametrize("degrees", [45, -90, 360, "90", True, None])
def test_bad_turn_refused_before_loading_or_writing(tmp_path, degrees):
    with pytest.raises(ValueError):
        se.set_rotation(str(tmp_path), 0, degrees)
    assert list(tmp_path.iterdir()) == []


def test_turn_is_undoable_invalidates_one_clip_and_clears_old_crop(tmp_path):
    src = picture(tmp_path / "art.png")
    before = segment(src, crop_bbox_norm=[.1, .1, .8, .8], crop_bbox_norm_ai=[0, .2, 1, 1])
    other = {**segment(src), "seg_index": 1, "start": 1, "end": 2, "clip": "clips/seg_001.mp4"}
    (tmp_path / "segments.json").write_text(json.dumps([before, other]))
    (tmp_path / "clips").mkdir()
    for n in (0, 1): (tmp_path / "clips" / f"seg_{n:03d}.mp4").write_bytes(b"existing clip")
    source_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    out = se.set_rotation(str(tmp_path), 0, 90)
    assert out[0]["rotation_deg"] == 90
    assert not any(k in out[0] for k in ("crop_bbox_norm", "crop_bbox_norm_ai"))
    assert not (tmp_path / "clips/seg_000.mp4").exists()
    assert (tmp_path / "clips/seg_001.mp4").read_bytes() == b"existing clip"
    assert out[1] == other
    assert hashlib.sha256(src.read_bytes()).hexdigest() == source_hash
    se.undo(str(tmp_path))
    assert se.load(str(tmp_path)) == [before, other]


def test_both_panel_swap_paths_drop_other_pictures_crop_and_rotation(tmp_path, monkeypatch):
    (tmp_path / "crops").mkdir()
    old = picture(tmp_path / "crops/old.png")
    picture(tmp_path / "crops/new.png", (80, 120))
    initial = segment(old, panel_id="old", rotation_deg=90, crop_bbox_norm=[0, 0, .5, .5],
                      crop_bbox_norm_ai=[.1, .1, .9, .9], focus_source="manual_full")
    def reset(): (tmp_path / "segments.json").write_text(json.dumps([initial]))
    reset()
    result = se.assign_panel(str(tmp_path), 0, "new", [{"panel_id": "new"}])
    assert all(k not in result[0] for k in ("rotation_deg", "crop_bbox_norm", "crop_bbox_norm_ai"))
    reset()
    monkeypatch.setattr(server, "load_segments", lambda: se.load(str(tmp_path)))
    monkeypatch.setattr(server, "_panel_dir", lambda: str(tmp_path / "crops"))
    monkeypatch.setattr(server, "_snapshot", lambda: None)
    monkeypatch.setattr(server, "_write_segments", lambda s: (tmp_path / "segments.json").write_text(json.dumps(s)))
    monkeypatch.setattr(server, "_start_render", lambda _: "offline")
    server.swap_panel(0, server.SwapIn(panel_id="new"))
    assert all(k not in se.load(str(tmp_path))[0] for k in ("rotation_deg", "crop_bbox_norm", "crop_bbox_norm_ai"))


def test_board_geometry_preview_key_and_media_identity_follow_turn(tmp_path):
    src = picture(tmp_path / "art.png", (900, 1924))
    seg = segment(src)
    target = tmp_path / "segments.json"
    target.write_text(json.dumps([seg]))
    key = server._thumb_key(seg)
    signature = server.cut_signature(pdir=str(tmp_path))
    identity = media_qa.input_manifest(str(tmp_path), {"speed": 1.15})
    assert "check framing" in board_data.build(str(tmp_path), {})["segments"][0]["flags"]
    seg["rotation_deg"] = 90
    target.write_text(json.dumps([seg]))
    board = board_data.build(str(tmp_path), {})["segments"][0]
    assert board["framing"]["geometry"]["width"] == 1924
    assert "check framing" not in board["flags"]
    assert server._thumb_key(seg) != key
    assert server.cut_signature(pdir=str(tmp_path)) != signature
    assert media_qa.input_manifest(str(tmp_path), {"speed": 1.15}) != identity


def test_orientation_endpoint_is_bound_to_named_chapter_not_current_selection(tmp_path, monkeypatch):
    import ingest
    from fastapi.testclient import TestClient
    for name in ("original", "isolated"):
        d = tmp_path / name; d.mkdir()
        src = picture(d / "art.png")
        (d / "segments.json").write_text(json.dumps([segment(src)]))
    original = (tmp_path / "original/segments.json").read_bytes()
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    monkeypatch.setattr(server, "get_active_project_id", lambda: "original")
    monkeypatch.setattr(server, "_snapshot", lambda: None)
    client = TestClient(server.app)
    result = client.post("/api/storyboard/rotation", json={"project": "isolated", "seg_index": 0, "degrees": 90})
    assert result.status_code == 200
    assert se.load(str(tmp_path / "isolated"))[0]["rotation_deg"] == 90
    assert (tmp_path / "original/segments.json").read_bytes() == original
    assert client.post("/api/storyboard/rotation", json={"project": "", "seg_index": 0, "degrees": 90}).status_code == 400
    monkeypatch.setattr(server, "JOBS", {"other": {"project": "isolated", "type": "finalize", "status": "running"}})
    before = (tmp_path / "isolated/segments.json").read_bytes()
    assert client.post("/api/storyboard/rotation", json={"project": "isolated", "seg_index": 0, "degrees": 180}).status_code == 409
    assert (tmp_path / "isolated/segments.json").read_bytes() == before


def test_real_pre_render_gate_reports_framing_and_refuses_invalid_turn(tmp_path, monkeypatch):
    src = picture(tmp_path / "art.png", (900, 1924))
    seg = segment(src)
    target = tmp_path / "segments.json"
    target.write_text(json.dumps([seg]))
    monkeypatch.setattr(server, "active_project_dir", lambda: str(tmp_path))
    result = se.validate_timeline(str(tmp_path))
    assert result["ok"]
    assert any(f["rule"] == "F2-narrow-card" for f in result["warnings"])
    seg["rotation_deg"] = 45
    target.write_text(json.dumps([seg]))
    result = se.validate_timeline(str(tmp_path))
    assert not result["ok"] and any(f["rule"] == "F4-invalid-rotation" for f in result["errors"])
    with pytest.raises(server.HTTPException) as blocked:
        server._gate_timeline([0], "render")
    assert blocked.value.status_code == 400
    assert "picture rotation" in blocked.value.detail and "cut narration" not in blocked.value.detail
    se.set_rotation(str(tmp_path), 0, 0)
    assert se.validate_timeline(str(tmp_path))["ok"]


def test_actual_rendered_clip_is_landscape_and_keeps_clockwise_color_positions(tmp_path, monkeypatch):
    src = picture(tmp_path / "art.png", (600, 900))
    seg = segment(src, rotation_deg=90)
    monkeypatch.setattr(rs, "CLIPS", str(tmp_path / "clips"))
    clip = rs.render_segment_fast(seg, str(tmp_path))
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", clip,
                                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    pixels = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 3)
    # Quarter-turn makes a wide 900x600 card. Left half is blue; upper-right red.
    assert pixels[400, 650, 2] > 210 and pixels[400, 650, 0] < 30
    assert pixels[400, 1250, 0] > 210 and pixels[400, 1250, 2] < 30


def test_segment_without_a_picture_file_is_reported_not_a_crash():
    assert pf.geometry(None) is None
    out = pf.inspect({"seg_index": 0, "panel_id": "gone"})
    assert out["regime"] == "unknown" and out["findings"][0]["rule"] == "F0-unreadable-image"
