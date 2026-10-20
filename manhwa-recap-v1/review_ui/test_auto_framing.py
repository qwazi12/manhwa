"""Automatic orientation + subject framing regressions.

Synthetic artwork and a fake vision client only — no network, no spend.
"""
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "hyperframes")]
import auto_framing as af
import panel_framing as pf
import storyboard_edit as se
import board_data
import segments as hf_segments
import server


def marked(path, size=(200, 400), box=(0.1, 0.2, 0.4, 0.3)):
    """White picture with one red rectangle at a known normalized box."""
    w, h = size
    im = Image.new("RGB", size, "white")
    im.paste("red", (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    im.save(path)
    return path


def red_box(im):
    a = np.asarray(im.convert("RGB")).astype(int)
    ys, xs = np.where((a[..., 0] > 200) & (a[..., 1] < 60))
    h, w = a.shape[:2]
    return [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]


@pytest.fixture(autouse=True)
def _sequential(monkeypatch):
    # FakeVision infers each comparison sheet's order from call order.
    monkeypatch.setattr(af, "WORKERS", 1)


class FakeVision:
    """Answers like the model would; records every question it was asked."""
    def __init__(self, subject=(0.0, 0.3, 1.0, 0.6), upright_deg=0, conf=0.95, fail=False):
        self.subject, self.upright, self.conf, self.fail = subject, upright_deg, conf, fail
        self.calls = []

    def __call__(self, jpeg, prompt):
        self.calls.append("turn" if prompt == af.TURN_PROMPT else "subject")
        if self.fail:
            raise RuntimeError("usage cap reached")
        if prompt == af.TURN_PROMPT:
            # Work out which label holds the upright copy from the sheet order:
            # the fake knows the order from the number of turn calls so far.
            order = af.ORDERS[(self.calls.count("turn") - 1) % len(af.ORDERS)]
            label = "ABC"[order.index(self.upright)]
            return json.dumps({"upright": label, "confidence": self.conf, "evidence": "fake"})
        return json.dumps({"subject_box": list(self.subject), "subject": "hero"})


# ------------------------------------------------------------------ geometry
@pytest.mark.parametrize("deg", [0, 90, 180, 270])
def test_rotate_box_matches_the_pixels_panel_framing_turns(tmp_path, deg):
    src = marked(tmp_path / "a.png")
    got = red_box(pf.load_image(str(src), deg))
    assert af.rotate_box([0.1, 0.2, 0.4, 0.3], deg) == pytest.approx(got, abs=0.01)


def test_subject_crop_keeps_full_width_band_for_portrait_and_never_over_zooms():
    crop = af.subject_crop([0.3, 0.4, 0.6, 0.5], 900, 2000)
    assert crop[0] == 0 and crop[2] == 1
    ch, cw = (crop[3] - crop[1]) * 2000, 900
    assert ch / cw >= af.FRAME_AR - 1e-6          # never wider than a 16:9 frame
    assert crop[1] <= 0.4 - af.MARGIN + 1e-6 and crop[3] >= 0.5 + af.MARGIN - 1e-6


def test_subject_crop_band_at_the_edge_stays_inside_the_picture():
    crop = af.subject_crop([0.2, 0.0, 0.8, 0.05], 900, 2000)
    assert 0 <= crop[1] < crop[3] <= 1


def test_landscape_art_crops_to_subject_itself():
    crop = af.subject_crop([0.5, 0.2, 0.8, 0.9], 1600, 900)
    assert crop[0] > 0.4 and crop[2] < 0.9


# -------------------------------------------------------------------- policy
def analysis(subject=(0.0, 0.3, 1.0, 0.6), turn=None):
    a = {"subject": {"ok": True, "subject_box": list(subject), "subject": "hero"}}
    if turn is not None:
        a["turn"] = turn
    return a


def test_turn_is_suggested_by_default_and_applied_only_in_apply_mode():
    turn = {"turn_cw": 90, "turn_confidence": 0.95, "turn_evidence": "wings span the frame"}
    sug = af.decide(analysis(turn=turn), 900, 2000, "suggest")
    assert sug["rotation_deg"] == 0 and sug["suggested_turn"]["degrees"] == 90
    app = af.decide(analysis(turn=turn), 900, 2000, "apply")
    assert app["rotation_deg"] == 90 and app["suggested_turn"] is None
    assert "wings" in app["rotation_reason"]


def test_turned_crop_is_measured_in_the_turned_picture():
    turn = {"turn_cw": 90, "turn_confidence": 0.95, "turn_evidence": ""}
    # A subject at the left edge, 30-60% down a tall picture, sits in the top
    # band, 40-70% across, once the picture is turned clockwise.
    out = af.decide(analysis(subject=(0.0, 0.3, 0.3, 0.6), turn=turn), 900, 2000, "apply")
    x0, y0, x1, y1 = out["crop_bbox_norm"]
    assert y0 == 0.0 and y1 < 0.6
    assert x0 < 0.4 < 0.7 < x1 and x1 - x0 < 0.5


def test_crop_bounds_keep_whole_picture_when_subject_fills_it_or_is_a_sliver():
    assert af.decide(analysis(subject=(0, 0.02, 1, 0.98)), 900, 2000)["crop_bbox_norm"] is None
    sliver = af.decide(analysis(subject=(0.45, 0.5, 0.55, 0.52)), 1600, 900)
    assert sliver["crop_bbox_norm"] is None and "too tight" in sliver["crop_reason"]
    assert af.decide({"subject": {"ok": False}}, 900, 2000)["crop_bbox_norm"] is None


def test_crop_must_show_the_art_larger_than_the_current_framing():
    # Very tall scrolling panel: a long band would become a sliver of a card.
    sliver = af.decide(analysis(subject=(0, 0.1, 1, 0.8)), 900, 3600)
    assert sliver["crop_bbox_norm"] is None and "shrink" in sliver["crop_reason"]
    # The same panel's focused short band is accepted.
    assert af.decide(analysis(subject=(0, 0.4, 1, 0.55)), 900, 3600)["crop_bbox_norm"] is not None
    # A fitted panel must get larger, not just different.
    fitted = af.decide(analysis(subject=(0, 0.05, 1, 0.83)), 900, 1500)
    assert fitted["crop_bbox_norm"] is None
    good = af.decide(analysis(subject=(0, 0.3, 1, 0.6)), 900, 1500)["crop_bbox_norm"]
    assert af.display_scale(good, 900, 1500) >= af.MIN_GAIN * af.display_scale(None, 900, 1500)


@pytest.mark.parametrize("answers,expected", [
    ([(90, 0.95, "a"), (90, 0.9, "b")], 90),
    ([(90, 0.95, "a"), (0, 0.95, "b")], 0),       # disagreement: position bias
    ([(90, 0.6, "a"), (90, 0.95, "b")], 0),       # one answer unsure
    ([(90, 0.95, "a"), None], 0),                 # one answer failed
    ([(0, 0.95, "a"), (0, 0.99, "b")], 0),
])
def test_turn_needs_both_orders_to_agree_confidently(answers, expected):
    assert af.combine_turns(answers)["turn_cw"] == expected


def test_parse_answers_map_labels_through_the_sheet_order_and_reject_garbage():
    assert af.parse_turn('{"upright": "B", "confidence": 0.9}', (90, 270, 0))[0] == 270
    assert af.parse_turn("```json\n{\"upright\": \"C\", \"confidence\": 1}\n```", (0, 90, 270))[0] == 270
    assert af.parse_turn("not json", (0, 90, 270)) is None
    assert af.parse_subject('{"subject_box": [0, 300, 1000, 600]}')["subject_box"] == [0, 0.3, 1, 0.6]
    assert af.parse_subject("nope")["ok"] is False
    assert af.parse_subject('{"subject_box": [0.5, 0.5, 0.51, 0.9]}')["subject_box"] is None


def test_translated_lettering_settles_orientation_without_a_question():
    assert af.has_lettering("FORTUNATELY, IT SEEMS HIS ENERGY")
    assert not af.has_lettering("") and not af.has_lettering("!! ..")
    assert af.turn_candidate(900, 2000, "")
    assert not af.turn_candidate(900, 2000, "THE POWER I CAN CURRENTLY EXERT")
    assert not af.turn_candidate(900, 1200, "")               # not tall


# -------------------------------------------------------------- vision calls
def test_analysis_is_cached_and_skips_turn_questions_for_lettered_panels(tmp_path):
    tall = str(marked(tmp_path / "tall.png", (200, 450)))
    lettered = str(marked(tmp_path / "lettered.png", (200, 450), (0.2, 0.2, 0.3, 0.3)))
    fake = FakeVision(upright_deg=90)
    cache = str(tmp_path / "cache.json")
    out = af.analyze_panels({tall: "", lettered: "HAVE YOU FORGOTTEN THAT"}, fake, cache)
    assert fake.calls.count("subject") == 2 and fake.calls.count("turn") == 2
    assert out[tall]["turn"]["turn_cw"] == 90 and "turn" not in out[lettered]
    again = FakeVision(upright_deg=90)
    af.analyze_panels({tall: "", lettered: "HAVE YOU FORGOTTEN THAT"}, again, cache)
    assert again.calls == []                                   # nothing paid twice


def test_failed_questions_are_not_cached_and_retry(tmp_path):
    p = str(marked(tmp_path / "p.png"))
    cache = str(tmp_path / "c.json")
    out = af.analyze_panels({p: ""}, FakeVision(fail=True), cache)
    assert out[p]["subject"]["ok"] is False
    retry = FakeVision()
    af.analyze_panels({p: ""}, retry, cache)
    assert "subject" in retry.calls


# --------------------------------------------------------------- integration
def shot(i, pid, crops, source="fallback_full", crop=(0, 0, 1, 1)):
    return {"index": i, "panel_id": pid, "panel_file": str(crops / f"{pid}.png"),
            "start": float(i), "end": float(i + 1), "beat_text": f"beat {i}",
            "crop_bbox_norm": list(crop), "focus_source": source, "width": 200, "height": 450}


def test_planner_step_frames_defaults_keeps_closeups_and_suggests_turns(tmp_path, monkeypatch):
    crops = tmp_path / "crops"; crops.mkdir()
    for pid in ("a", "b"):
        marked(crops / f"{pid}.png", (200, 450))
    shots = [shot(0, "a", crops), shot(1, "b", crops, "vision", (0.1, 0.1, 0.5, 0.5))]
    monkeypatch.setenv("AUTO_TURN", "suggest")
    summary = af.apply_to_shots(shots, str(crops), None, ask=FakeVision(upright_deg=90),
                                cache_path=str(tmp_path / "c.json"))
    assert shots[0]["focus_source"] == "auto_subject" and shots[0]["crop_bbox_norm"] != [0, 0, 1, 1]
    assert shots[1]["crop_bbox_norm"] == [0.1, 0.1, 0.5, 0.5]         # planner close-up kept
    assert all(s["rotation_suggestion"]["degrees"] == 90 for s in shots)
    assert not any(s.get("rotation_deg") for s in shots)
    assert summary["framed"] == 1 and summary["suggested"] == 2


def test_apply_mode_turns_and_records_provenance(tmp_path, monkeypatch):
    crops = tmp_path / "crops"; crops.mkdir()
    marked(crops / "a.png", (200, 450))
    shots = [shot(0, "a", crops, "vision", (0.1, 0.1, 0.5, 0.5))]
    monkeypatch.setenv("AUTO_TURN", "apply")
    af.apply_to_shots(shots, str(crops), None, ask=FakeVision(upright_deg=90))
    s = shots[0]
    assert s["rotation_deg"] == 90 and s["rotation_source"] == "auto"
    assert s["focus_source"] == "auto_orientation"
    segs = hf_segments.build_segments(shots)
    assert segs[0]["rotation_deg"] == 90 and segs[0]["rotation_source"] == "auto"


def test_off_switch_and_missing_key_skip_without_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_FRAMING", "off")
    assert af.default_ask() is None
    monkeypatch.delenv("AUTO_FRAMING")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    shots = [shot(0, "a", tmp_path)]
    assert af.apply_to_shots(shots, str(tmp_path))["skipped"]
    assert shots[0]["focus_source"] == "fallback_full"


def test_segments_without_auto_fields_are_unchanged():
    shots = [{"index": 0, "panel_id": "a", "start": 0, "end": 1, "crop_bbox_norm": None}]
    assert not any(k.startswith("rotation") for k in hf_segments.build_segments(shots)[0])


# ---------------------------------------------------------------------- board
def chapter(tmp_path, segs):
    (tmp_path / "crops").mkdir(exist_ok=True)
    (tmp_path / "clips").mkdir(exist_ok=True)
    for s in segs:
        if not (tmp_path / "crops" / f"{s['panel_id']}.png").exists():
            marked(tmp_path / "crops" / f"{s['panel_id']}.png", (200, 450))
        (tmp_path / "clips" / f"seg_{s['seg_index']:03d}.mp4").write_bytes(b"clip")
    (tmp_path / "segments.json").write_text(json.dumps(segs))
    return segs


def seg(i, pid, pdir, **kw):
    return {"seg_index": i, "panel_id": pid, "panel_file": str(pdir / "crops" / f"{pid}.png"),
            "start": float(i), "end": float(i + 1), "dur": 1.0, "beats": [{"index": i, "start": float(i), "end": float(i + 1), "text": "x"}],
            "user_included": True, "clip": f"clips/seg_{i:03d}.mp4",
            "crop_bbox_norm": [0, 0, 1, 1], "focus_source": "fallback_full", **kw}


def test_board_auto_frame_only_touches_defaults_and_one_undo_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_TURN", "suggest")
    before = chapter(tmp_path, [
        seg(0, "a", tmp_path),
        seg(1, "b", tmp_path, focus_source="manual_full"),
        seg(2, "c", tmp_path, rotation_deg=180, focus_source="manual_orientation"),
        seg(3, "d", tmp_path, focus_source="vision", crop_bbox_norm=[.1, .1, .6, .6]),
    ])
    dry_segs, dry = se.auto_frame(str(tmp_path), ask=FakeVision(upright_deg=90), dry_run=True)
    assert dry["changed_segments"] == [0, 3] and dry["proposals"][0]["seg_index"] == 0
    assert json.loads((tmp_path / "segments.json").read_text()) == before

    out, summary = se.auto_frame(str(tmp_path), ask=FakeVision(upright_deg=90))
    assert summary["changed_segments"] == [0, 3]
    assert out[0]["focus_source"] == "auto_subject" and out[0]["rotation_suggestion"]["degrees"] == 90
    # A planner close-up keeps its crop but still gets the turn suggestion.
    assert out[3]["crop_bbox_norm"] == [.1, .1, .6, .6] and out[3]["rotation_suggestion"]["degrees"] == 90
    assert out[1:3] == before[1:3]                                  # reviewer decisions untouched
    assert not (tmp_path / "clips/seg_000.mp4").exists()
    assert all((tmp_path / f"clips/seg_{i:03d}.mp4").exists() for i in (1, 2, 3))   # suggestion only
    assert "auto_frame" in (tmp_path / "edits.log.jsonl").read_text()
    se.undo(str(tmp_path))
    assert se.load(str(tmp_path)) == before


def test_board_shows_suggestion_auto_turn_and_reviewer_turn_clears_it(tmp_path):
    chapter(tmp_path, [seg(0, "a", tmp_path, rotation_suggestion={"degrees": 90, "confidence": 0.95, "evidence": "wings"}),
                       seg(1, "b", tmp_path, rotation_deg=90, rotation_source="auto", rotation_reason="auto turn")])
    rows = board_data.build(str(tmp_path), {})["segments"]
    assert rows[0]["framing"]["suggestion"]["degrees"] == 90
    assert any(f["rule"] == "F5-turn-suggested" for f in rows[0]["framing"]["findings"])
    assert any(f["rule"] == "F6-auto-turned" for f in rows[1]["framing"]["findings"])
    assert rows[0]["crop"]["auto"] is False
    out = se.set_rotation(str(tmp_path), 0, 90)
    assert out[0]["rotation_deg"] == 90 and "rotation_suggestion" not in out[0]
    assert out[0]["focus_source"] == "manual_orientation"


def test_endpoint_is_bound_to_named_chapter_and_refused_during_its_render(tmp_path, monkeypatch):
    import ingest
    from fastapi.testclient import TestClient
    for name in ("original", "isolated"):
        d = tmp_path / name; d.mkdir()
        chapter(d, [seg(0, "a", d)])
    original = (tmp_path / "original/segments.json").read_bytes()
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    monkeypatch.setattr(server, "get_active_project_id", lambda: "original")
    monkeypatch.setattr(server, "_snapshot", lambda: None)
    monkeypatch.setattr(af, "default_ask", lambda: FakeVision())
    client = TestClient(server.app)
    r = client.post("/api/storyboard/auto_framing", json={"project": "isolated"})
    assert r.status_code == 200 and r.json()["changed_segments"] == [0]
    assert se.load(str(tmp_path / "isolated"))[0]["focus_source"] == "auto_subject"
    assert (tmp_path / "original/segments.json").read_bytes() == original
    assert client.post("/api/storyboard/auto_framing", json={"project": ""}).status_code == 400
    monkeypatch.setattr(server, "JOBS", {"j": {"project": "original", "type": "finalize", "status": "running"}})
    before = (tmp_path / "original/segments.json").read_bytes()
    assert client.post("/api/storyboard/auto_framing", json={"project": "original"}).status_code == 409
    assert (tmp_path / "original/segments.json").read_bytes() == before
    monkeypatch.setattr(af, "default_ask", lambda: None)
    assert client.post("/api/storyboard/auto_framing", json={"project": "isolated"}).status_code == 409
