"""Owner, 2026-11-17: Fog Land ch.46's board was replaced by A Regressor's Tale
ch.37's. A narration edit on ch.37 loaded ch.37's segments, spent 3.5 s
re-voicing the line, and saved to the chapter that was open by then (Fog Land,
opened in another tab during those seconds). A board edit now stays with one
chapter for the whole request: the chapter the page names, else the chapter
open when the request arrives.
No network, no real voice. Run: python3 -m pytest test_board_edit_pin.py -q"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"


def _chapter(root, pid, lines):
    pdir = os.path.join(root, pid)
    os.makedirs(os.path.join(pdir, "audio"))
    os.makedirs(os.path.join(pdir, "clips"))
    segs = [{"seg_index": i, "panel_id": f"page001_panel_{i + 1:03d}", "start": 0.0, "dur": 4.0, "end": 4.0,
             "clip": f"clips/seg_{i:03d}.mp4", "beats": [{"index": i, "text": t, "start": 0.0, "end": 3.0}]}
            for i, t in enumerate(lines)]
    json.dump(segs, open(os.path.join(pdir, "segments.json"), "w"))
    json.dump({"id": pid}, open(os.path.join(pdir, "project.json"), "w"))
    return pdir


def _lines(pdir):
    return [b["text"] for s in json.load(open(os.path.join(pdir, "segments.json"))) for b in s["beats"]]


@pytest.fixture
def studio(tmp_path, monkeypatch):
    import ingest
    import server
    from fastapi.testclient import TestClient
    root, work = str(tmp_path / "projects"), str(tmp_path / "work")
    os.makedirs(root)
    os.makedirs(work)
    monkeypatch.delenv("SHARED_SECRET", raising=False)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "WORK", work)
    monkeypatch.setattr(server, "VERSIONS", str(tmp_path / "versions"))
    os.makedirs(server.VERSIONS)
    server._ACTIVE_CACHE["key"] = None
    reg = _chapter(root, "regressor_37", ["Eunhyun woke.", "He trained.", "He flew."])
    fog = _chapter(root, "fog-land_46", ["Paul stayed.", "Wilson ran.", "Max built a gun.", "The beast came."])
    started = []
    monkeypatch.setattr(server, "_start_render", lambda idx: started.append(
        (os.path.basename(server.active_project_dir()), list(idx))) or "job")
    monkeypatch.setattr(server, "_recompute_timeline", lambda segs: None)
    c = TestClient(server.app)

    def voice_then(open_other=None):
        """The re-voice call; while it runs, the owner opens another chapter."""
        def synth(text, out_path, style=None, engine=None):
            if open_other:
                assert c.get(f"/api/board/{open_other}").status_code == 200       # activates it
            open(out_path, "wb").write(b"mp3")
        monkeypatch.setattr(server, "_synth_rest", synth)

    yield server, c, reg, fog, started, voice_then
    server._ACTIVE_CACHE["key"] = None


def test_an_edit_stays_in_its_chapter_when_another_is_opened_while_it_saves(studio):
    server, c, reg, fog, started, voice_then = studio
    assert c.post("/api/activate", json={"id": "regressor_37"}).status_code == 200
    voice_then(open_other="fog-land_46")
    r = c.post("/api/segments/1/narration", json={"beats": [{"index": 1, "text": "He trained for hours."}]})
    assert r.status_code == 200, r.text
    assert server.get_active_project_id() == "fog-land_46"                # the other chapter did open
    assert _lines(reg) == ["Eunhyun woke.", "He trained for hours.", "He flew."]
    assert _lines(fog) == ["Paul stayed.", "Wilson ran.", "Max built a gun.", "The beast came."]
    assert os.path.exists(os.path.join(reg, "audio", "beat_001.mp3"))
    assert not os.path.exists(os.path.join(fog, "audio", "beat_001.mp3"))
    assert started == [("regressor_37", [1])]


def test_an_edit_goes_to_the_chapter_its_page_shows_not_the_one_opened_last(studio):
    server, c, reg, fog, started, voice_then = studio
    voice_then()
    assert c.post("/api/activate", json={"id": "fog-land_46"}).status_code == 200      # a second tab
    r = c.post("/api/segments/0/narration", json={"beats": [{"index": 0, "text": "Eunhyun woke again."}]},
               headers={"x-chapter": "regressor_37"})
    assert r.status_code == 200, r.text
    assert _lines(reg)[0] == "Eunhyun woke again." and _lines(fog)[0] == "Paul stayed."
    assert server.get_active_project_id() == "fog-land_46"                # the open chapter is not switched
    assert started == [("regressor_37", [0])]


@pytest.mark.parametrize("name", ["deleted-chapter_9", "..", "../regressor_37", "a b"])
def test_an_edit_for_a_chapter_that_is_gone_is_refused_and_changes_nothing(studio, name):
    server, c, reg, fog, started, voice_then = studio
    voice_then()
    assert c.post("/api/activate", json={"id": "fog-land_46"}).status_code == 200
    r = c.post("/api/segments/0/narration", json={"beats": [{"index": 0, "text": "Changed."}]},
               headers={"x-chapter": name})
    assert r.status_code == 409 and "Reload" in r.json()["detail"]
    assert _lines(fog)[0] == "Paul stayed." and _lines(reg)[0] == "Eunhyun woke." and started == []


def test_reading_and_opening_a_chapter_are_not_pinned(studio):
    server, c, reg, fog, started, voice_then = studio
    assert c.post("/api/activate", json={"id": "regressor_37"}).status_code == 200
    r = c.get("/api/board/fog-land_46", headers={"x-chapter": "regressor_37"})
    assert r.status_code == 200 and server.get_active_project_id() == "fog-land_46"
    assert server._REQ_PDIR.get() is None                                 # nothing left pinned


# ---- owner review 2026-11-17, "issues waiting to happen" ---------------------

def test_an_issues_tab_fix_goes_to_the_chapter_its_page_shows(studio, monkeypatch):
    import validator_actions
    server, c, reg, fog, started, voice_then = studio
    seen = []
    monkeypatch.setattr(validator_actions, "MUTATING", set(), raising=False)
    monkeypatch.setattr(validator_actions, "apply_action",
                        lambda pdir, action, params: seen.append(os.path.basename(pdir)) or {"ok": True})
    monkeypatch.setattr(server._validator, "load_report", lambda pdir: seen.append(os.path.basename(pdir)) and None)
    assert c.post("/api/activate", json={"id": "fog-land_46"}).status_code == 200       # opened on the phone
    r = c.post("/api/validate/action", json={"action": "swap", "params": {}}, headers={"x-chapter": "regressor_37"})
    assert r.status_code == 200, r.text
    assert c.get("/api/validation", headers={"x-chapter": "regressor_37"}).status_code == 200
    assert seen == ["regressor_37", "regressor_37"]
    assert c.get("/api/validation").status_code == 200                                   # a page that names no chapter
    assert seen[-1] == "fog-land_46"                                                     # reads the open one, as before


def test_a_tick_made_while_a_line_is_being_recorded_is_not_overwritten(studio, monkeypatch):
    import threading
    import time
    server, c, reg, fog, started, voice_then = studio
    recording, order = threading.Event(), []

    def slow(text, out_path, style=None, engine=None):
        recording.set()
        time.sleep(0.4)
        open(out_path, "wb").write(b"mp3")
        order.append("line recorded")
    monkeypatch.setattr(server, "_synth_rest", slow)
    out = {}
    t = threading.Thread(target=lambda: out.update(edit=c.post(
        "/api/segments/1/narration", json={"beats": [{"index": 1, "text": "He trained for hours."}]},
        headers={"x-chapter": "regressor_37"})))
    t.start()
    assert recording.wait(5)
    r = c.post("/api/storyboard/set_included", json={"panel_id": "page001_panel_003", "included": True},
               headers={"x-chapter": "regressor_37"})
    order.append("tick saved")
    t.join(10)
    assert r.status_code == 200 and out["edit"].status_code == 200
    assert order == ["line recorded", "tick saved"]                       # the tick waited for the edit
    segs = json.load(open(os.path.join(reg, "segments.json")))
    assert segs[1]["beats"][0]["text"] == "He trained for hours." and segs[2].get("user_included") is True


def test_edits_are_refused_while_a_render_re_records_the_voice(studio):
    server, c, reg, fog, started, voice_then = studio
    voice_then()
    server.JOBS["rv1"] = {"project": "regressor_37", "type": "finalize", "stage": "revoice", "status": "running"}
    try:
        r = c.post("/api/segments/0/narration", json={"beats": [{"index": 0, "text": "Changed."}]},
                   headers={"x-chapter": "regressor_37"})
        assert r.status_code == 409 and "re-recorded" in r.json()["detail"]
        assert _lines(reg)[0] == "Eunhyun woke."
        r = c.post("/api/segments/0/narration", json={"beats": [{"index": 0, "text": "Paul left."}]},
                   headers={"x-chapter": "fog-land_46"})
        assert r.status_code == 200 and _lines(fog)[0] == "Paul left."    # another chapter is not held up
    finally:
        server.JOBS.pop("rv1", None)


def test_undo_walks_back_a_picture_swap_and_a_line_edit_with_its_recording(studio):
    from PIL import Image
    server, c, reg, fog, started, voice_then = studio
    said = []

    def synth(text, out_path, style=None, engine=None):
        said.append(text)
        open(out_path, "wb").write(text.encode())
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(server, "_synth_rest", synth)
    crops = os.path.join(reg, "crops")
    os.makedirs(crops)
    for i in (1, 2, 3):
        Image.new("RGB", (40, 40), "white").save(os.path.join(crops, f"page001_panel_{i:03d}.png"))
    mp.setattr(server, "_panel_dir", lambda: crops)
    h = {"x-chapter": "regressor_37"}
    try:
        open(os.path.join(reg, "audio", "beat_001.mp3"), "wb").write(b"He trained.")
        assert c.post("/api/segments/0/panel", json={"panel_id": "page001_panel_003"}, headers=h).status_code == 200
        assert c.post("/api/segments/1/narration", json={"beats": [{"index": 1, "text": "He trained for hours."}]},
                      headers=h).status_code == 200
        segs = json.load(open(os.path.join(reg, "segments.json")))
        assert segs[0]["panel_id"] == "page001_panel_003" and segs[1]["beats"][0]["text"] == "He trained for hours."
        r = c.post("/api/storyboard/undo", headers=h)                      # walks back the line edit
        assert r.status_code == 200 and r.json()["undid"] == "edit line", r.text
        segs = json.load(open(os.path.join(reg, "segments.json")))
        assert segs[1]["beats"][0]["text"] == "He trained." and segs[0]["panel_id"] == "page001_panel_003"
        assert said == ["He trained for hours.", "He trained."]            # the old recording is put back
        assert open(os.path.join(reg, "audio", "beat_001.mp3"), "rb").read() == b"He trained."
        r = c.post("/api/undo", headers=h)                                 # the old page's Undo: same stack
        assert r.status_code == 200 and r.json()["restored"] == "swap picture"
        assert json.load(open(os.path.join(reg, "segments.json")))[0]["panel_id"] == "page001_panel_001"
        assert _lines(fog) == ["Paul stayed.", "Wilson ran.", "Max built a gun.", "The beast came."]
    finally:
        mp.undo()


def test_a_swap_during_a_render_is_kept_and_the_old_clip_is_removed(studio, monkeypatch):
    from PIL import Image
    server, c, reg, fog, started, voice_then = studio
    crops = os.path.join(reg, "crops")
    os.makedirs(crops)
    Image.new("RGB", (40, 40), "white").save(os.path.join(crops, "page001_panel_002.png"))
    monkeypatch.setattr(server, "_panel_dir", lambda: crops)
    clip = os.path.join(reg, "clips", "seg_000.mp4")
    open(clip, "wb").write(b"old picture")

    def busy(idx):
        raise server.HTTPException(409, "a render is already running")
    monkeypatch.setattr(server, "_start_render", busy)
    r = c.post("/api/segments/0/panel", json={"panel_id": "page001_panel_002"}, headers={"x-chapter": "regressor_37"})
    assert r.status_code == 200 and r.json()["job"] is None
    assert json.load(open(os.path.join(reg, "segments.json")))[0]["panel_id"] == "page001_panel_002"
    assert not os.path.exists(clip)                                        # finalize renders the missing clip


def test_a_line_edit_that_fails_half_way_changes_no_recording(studio, monkeypatch):
    server, c, reg, fog, started, voice_then = studio
    segs = json.load(open(os.path.join(reg, "segments.json")))
    segs[0]["beats"].append({"index": 9, "text": "Second line.", "start": 3.0, "end": 4.0})
    json.dump(segs, open(os.path.join(reg, "segments.json"), "w"))
    for i, t in ((0, b"old 0"), (9, b"old 9")):
        open(os.path.join(reg, "audio", f"beat_{i:03d}.mp3"), "wb").write(t)
    n = []

    def synth(text, out_path, style=None, engine=None):
        n.append(text)
        if len(n) == 2:
            raise RuntimeError("daily cap reached")
        open(out_path, "wb").write(b"new")
    monkeypatch.setattr(server, "_synth_rest", synth)
    r = c.post("/api/segments/0/narration", headers={"x-chapter": "regressor_37"},
               json={"beats": [{"index": 0, "text": "First changed."}, {"index": 9, "text": "Second changed."}]})
    assert r.status_code == 500 and len(n) == 2
    assert open(os.path.join(reg, "audio", "beat_000.mp3"), "rb").read() == b"old 0"
    assert sorted(os.listdir(os.path.join(reg, "audio"))) == ["beat_000.mp3", "beat_009.mp3"]
    assert _lines(reg)[:1] == ["Eunhyun woke."]


@pytest.mark.parametrize("name", ["_trash", ".", "_covers", "a/b"])
def test_a_folder_that_is_not_a_chapter_is_never_taken_for_one(studio, name):
    server = studio[0]
    with pytest.raises(server.HTTPException) as e:
        server.project_dir_for(name)
    assert e.value.status_code == 400
