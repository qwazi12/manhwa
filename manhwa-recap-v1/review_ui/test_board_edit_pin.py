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
