"""Owner, 2026-11-07: "Save as series defaults" redrew Murim 251-252's approved
cover master. Bulk redraws now replace only thumbnails the studio made itself.
No network: Drive and the copilot are faked. Run: python3 -m pytest test_redraw_keeps_picks.py -q"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
from PIL import Image  # noqa: E402


def _jpg(colour):
    b = io.BytesIO()
    Image.new("RGB", (1280, 720), colour).save(b, "JPEG")
    return b.getvalue()


def _proj(root, pid, sid):
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    json.dump({"series": sid, "series_id": sid, "chapter": "1"}, open(os.path.join(d, "project.json"), "w"))
    open(os.path.join(d, "exports", "v.mp4"), "wb").write(b"\0")
    return d


def test_redraw_replaces_only_the_studios_own_thumbnails(tmp_path, monkeypatch):
    import ingest
    import server
    import thumbnail as tb
    import thumbnail_studio as ts
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    auto = _proj(tmp_path, "auto-series_1", "auto-series")
    hand = _proj(tmp_path, "hand-series_1", "hand-series")
    picked = _proj(tmp_path, "picked-series_1", "picked-series")
    legacy = _proj(tmp_path, "legacy-series_1", "legacy-series")
    master = _proj(tmp_path, "murim-login_ch251-252", "murim-login")
    tb.save(auto, "v.mp4", _jpg((10, 10, 10)), source="auto")
    tb.save(hand, "v.mp4", _jpg((20, 20, 20)))                        # POST /api/thumbnail
    tb.save(picked, "v.mp4", _jpg((30, 30, 30)), source="picked")
    tb.save(legacy, "v.mp4", _jpg((40, 40, 40)))
    idx = json.load(open(os.path.join(legacy, "exports", "_thumbs", "index.json")))
    idx["v.mp4"].pop("source")                                        # saved before sources existed,
    json.dump(idx, open(os.path.join(legacy, "exports", "_thumbs", "index.json"), "w"))
    ts.save_concepts(legacy, {"v.mp4": {"chosen": {"at": time.time() - 3600}}})   # long after the copilot
    tb.save(master, "v.mp4", _jpg((50, 50, 50)), source="auto")

    redrawn = []
    monkeypatch.setattr(server, "_ensure_project_cover", lambda pdir: None)
    monkeypatch.setattr(server, "api_thumbcopilot_generate", lambda body: None)
    monkeypatch.setattr(ts, "get_concepts", lambda pdir, name: {"concepts": [{"id": "c1", "recommended": True}]})
    monkeypatch.setattr(server, "_thumbcopilot_apply", lambda body, source="auto": redrawn.append(body.project))
    monkeypatch.setattr(server, "_master_lookup", lambda: (lambda sid: sid == "murim-login"))
    events = []
    monkeypatch.setattr(server, "_ev", lambda kind, msg, level="ok": events.append(msg))

    assert server._rerender_chosen_thumbnails() == 1
    assert redrawn == ["auto-series_1"]
    assert "4 kept (series master or picked by hand)" in events[-1]


def test_sources_are_recorded(tmp_path):
    import thumbnail as tb
    d = str(tmp_path)
    assert tb.save(d, "a.mp4", _jpg((1, 2, 3)))["source"] == "upload"
    assert tb.save(d, "b.mp4", _jpg((1, 2, 3)), source="auto")["source"] == "auto"


def test_the_page_apply_is_a_pick_and_internal_redraws_are_auto():
    import inspect
    import server
    src = inspect.getsource(server)
    assert 'return _thumbcopilot_apply(body, source="picked")' in src
    assert "api_thumbcopilot_apply(ThumbApplyIn(" not in src.replace("def api_thumbcopilot_apply", "")


def test_drive_unreachable_means_keep(monkeypatch):
    import server
    import drive_store
    monkeypatch.setattr(drive_store, "configured", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert server._master_lookup()("any-series") is True
