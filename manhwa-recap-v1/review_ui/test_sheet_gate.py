"""Posting gate + sheet control (owner, 2026-11-07). No network: the sheet rows are given.
Run: python3 -m pytest test_sheet_gate.py -q"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)
import publish_queue as pq  # noqa: E402
import sheet_gate as sg  # noqa: E402

ET = ZoneInfo("America/New_York")
T = lambda *a: datetime(*a, tzinfo=ET).timestamp()
NOW = T(2026, 11, 7, 19, 0)            # Sat Nov 7, 7:00 PM ET
HDR = ["#", "Slot (ET)", "Queue ID", "Posts to", "Video", "Length (s)", "Flag", "Current title", "Current caption",
       "Proposed YouTube title", "Proposed YouTube description (first 2 lines)", "What changed", "Decision",
       "Your edits / notes"]


def row(qid, slot, decision="Approve", title="", desc="", notes=""):
    return ["1", slot, qid, "YT", "", "", "", "old", "old", title, desc, "", decision, notes]


def test_slots_parse_on_the_hour_only():
    assert sg.parse_slot("Sat Nov 7, 10:00 PM", NOW)[0] == T(2026, 11, 7, 22)
    assert sg.parse_slot("Sun, Nov 8 at 10:00 AM ET", NOW)[0] == T(2026, 11, 8, 10)
    assert sg.parse_slot("Mon Nov 9, 2 PM", NOW)[0] == T(2026, 11, 9, 14)
    at, err = sg.parse_slot("Sun Nov 8, 10:30 AM", NOW)
    assert at is None and "off the hour" in err
    at, err = sg.parse_slot("Mon Nov 8, 10:00 AM", NOW)          # Nov 8 is a Sunday
    assert at is None and "Sunday" in err
    assert sg.parse_slot("tomorrow", NOW)[0] is None
    assert sg.parse_slot("Fri Jan 1, 10:00 AM", NOW)[0] == T(2027, 1, 1, 10)   # nearest year
    assert sg.label(T(2026, 11, 8, 22)) == "Sun Nov 8, 10:00 PM"


def test_rows_parse_from_the_flamingo_tab():
    grid = [HDR, row("a_ch1-2", "Sat Nov 7, 10:00 PM", title="T", desc="L1\nL2"), [""] * 14,
            row("b_ch3", "Sun Nov 8, 10:00 AM", decision="")]
    rows = sg.parse_rows(grid)
    assert [r["queue_id"] for r in rows] == ["a_ch1-2", "b_ch3"]
    assert rows[0]["desc"] == "L1\nL2" and rows[0]["hashtags"] is None and rows[1]["decision"] == ""
    rows = sg.parse_rows([HDR[:12] + ["Hashtags"] + HDR[12:], row("a", "x")[:12] + ["#A #B"] + row("a", "x")[12:]])
    assert rows[0]["hashtags"] == "#A #B" and rows[0]["decision"] == "Approve"
    try:
        sg.parse_rows([["no", "header"]])
        assert False
    except ValueError:
        pass


def test_the_gate():
    at = NOW + 3 * 3600
    assert sg.gate(None, at, NOW)[0] == "hold"
    assert sg.gate({"decision": "Approve"}, at, NOW)[0] == "post"
    assert sg.gate({"decision": "approve"}, None, NOW)[0] == "hold"
    assert sg.gate({"decision": "Skip"}, at, NOW)[0] == "skip"
    assert sg.gate({"decision": "Edit"}, at, NOW)[0] == "hold"
    assert sg.gate({"decision": "Maybe"}, at, NOW)[0] == "hold"
    assert sg.gate({"decision": ""}, at, NOW)[0] == "hold"                  # blank, 3 h out
    assert sg.gate({"decision": ""}, NOW + 2 * 3600, NOW)[0] == "post"      # still blank 2 h before


def _rows(*rs):
    return sg.parse_rows([HDR, *rs])


def test_slot_edits_off_hour_and_same_hour_are_refused():
    vids = {"a": {"lane": "queued"}, "b": {"lane": "queued"}, "c": {"lane": "queued"}}
    prev = {"a": T(2026, 11, 7, 22), "b": T(2026, 11, 8, 10), "c": T(2026, 11, 8, 14)}
    ev = sg.evaluate(_rows(row("a", "Sat Nov 7, 10:00 PM"), row("b", "Sat Nov 7, 10:00 PM"),
                           row("c", "Sun Nov 8, 2:30 PM")), vids, prev, NOW)
    assert ev["slots"]["a"] == prev["a"]                    # unchanged slot wins the hour
    assert ev["slots"]["b"] == prev["b"]                    # the edit is refused, the old slot stays
    assert ev["slots"]["c"] == prev["c"]                    # off-hour refused, old slot stays
    txt = "\n".join(ev["needs_owner"])
    assert "b: slot refused — two videos in one hour" in txt and "c: slot refused" in txt and "off the hour" in txt


def test_an_uploaded_video_is_never_moved_from_the_sheet():
    up = T(2026, 11, 7, 14)
    vids = {"m": {"lane": "uploaded", "publish_at": up}, "q": {"lane": "queued"}}
    ev = sg.evaluate(_rows(row("m", "Sat Nov 7, 6:00 PM"), row("q", "Sat Nov 7, 2:00 PM")), vids, {}, T(2026, 11, 7, 10))
    assert ev["slots"]["m"] == up
    assert "q" not in ev["slots"]                           # its hour belongs to the uploaded video
    txt = "\n".join(ev["needs_owner"])
    assert "m: slot refused — already uploaded" in txt and "q: slot refused — two videos in one hour" in txt


def test_queued_but_not_in_the_sheet_is_held_and_listed():
    ev = sg.evaluate(_rows(row("a", "Sat Nov 7, 10:00 PM", decision="Edit", notes="fix hook")),
                     {"a": {"lane": "queued"}, "z": {"lane": "queued"}}, {}, NOW)
    g = {r["queue_id"]: r["gate"] for r in ev["rows"]}
    assert g == {"a": "hold", "z": "hold"}
    txt = "\n".join(ev["needs_owner"])
    assert "a: Decision is Edit — held (notes: fix hook)" in txt and "z: queued but not in the Posting Review sheet" in txt


def test_skip_does_not_hold_an_hour():
    ev = sg.evaluate(_rows(row("a", "Sat Nov 7, 10:00 PM", decision="Skip"), row("b", "Sat Nov 7, 10:00 PM")),
                     {"a": {"lane": "queued"}, "b": {"lane": "queued"}}, {}, NOW)
    assert "a" not in ev["slots"] and ev["slots"]["b"] == T(2026, 11, 7, 22)
    assert {r["queue_id"]: r["gate"] for r in ev["rows"]} == {"a": "skip", "b": "post"}


def test_sheet_text_is_used_as_written():
    md = {"title": "Old | Flamingo Recap", "description": "old 1\nold 2\n\nbody\n\n#Old #Tags"}
    r = {"title": "New Title | Series", "desc": "new 1\nnew 2", "hashtags": None}
    new, changed, probs = sg.sheet_text(md, r)
    assert new["title"] == "New Title | Series" and new["description"] == "new 1\nnew 2\n\nbody\n\n#Old #Tags"
    assert changed == ["title", "description"] and not probs and "hashtags" not in new
    new, changed, probs = sg.sheet_text(md, {**r, "hashtags": "#A #B #FlamingoRecap"})
    assert new["description"].endswith("body\n\n#A #B #FlamingoRecap") and "#Old" not in new["description"]
    assert "hashtags" in changed and new["hashtags"] == ["#A", "#B", "#FlamingoRecap"]
    # the sheet's description cell: line 1 + a hashtag line -> line 1 replaced, the tag line replaced
    new, changed, probs = sg.sheet_text(md, {"title": "", "desc": "new 1\n#A #B", "hashtags": None})
    assert new["description"] == "new 1\nold 2\n\nbody\n\n#A #B" and changed == ["description", "hashtags"]
    assert sg.sheet_text(md, {**r, "title": "x" * 101})[2]
    assert sg.sheet_text(md, {**r, "hashtags": " ".join(f"#t{i}" for i in range(16))})[2]
    assert sg.sheet_text(md, {"title": "", "desc": "", "hashtags": None})[1] == []   # empty cells change nothing


def test_switch_defaults_on_and_turns_off(tmp_path, monkeypatch):
    monkeypatch.delenv("SHEET_GATE_OFF", raising=False)
    root = str(tmp_path)
    assert sg.enabled(root)
    sg.set_on(root, False, by="test")
    assert not sg.enabled(root) and sg.load(root)["switch_log"][-1]["to"] is False
    sg.set_on(root, True)
    assert sg.enabled(root)
    monkeypatch.setenv("SHEET_GATE_OFF", "1")
    assert not sg.enabled(root)


SCHED = {"enabled": True, "times": ["10:00", "14:00", "22:00"], "tz": "America/New_York", "per_channel_per_day": 3}


def _q(*ids):
    return {"items": [{"id": i, "project": f"p{i}", "name": "v.mp4", "status": "queued"} for i in ids]}


def test_decide_with_the_gate_posts_only_pinned_videos_at_their_slot():
    tg = lambda x: ["yt"]
    d = _q("a", "b")
    at = T(2026, 11, 7, 22)
    up = at - 80 * 60                                        # 8:40 PM, inside the 90-min upload lead
    assert pq.decide(SCHED, d, up, tg, pins={})["action"] == "wait"           # nothing approved
    dec = pq.decide(SCHED, d, up, tg, pins={"b": at})
    assert dec["action"] == "post" and dec["item"]["id"] == "b" and dec["publish_at"] == at and dec["slot"] == "22:00"
    assert pq.decide(SCHED, d, at - 120 * 60, tg, pins={"b": at})["action"] == "wait"   # too early to upload
    # a sheet slot outside the posting times (on the hour) still posts
    at6 = T(2026, 11, 7, 18)
    dec = pq.decide(SCHED, d, at6 - 60 * 60, tg, pins={"a": at6})
    assert dec["action"] == "post" and dec["slot"] == "18:00"
    # too close to upload ahead: missed, no catch-up
    dec = pq.decide(SCHED, d, at - 30 * 60, tg, pins={"b": at})
    assert dec["action"] == "missed" and dec["item"]["id"] == "b"
    # a slot already used never fires twice
    d["slots_done"] = {"2026-11-07": {"22:00": "uploading pb"}}
    assert pq.decide(SCHED, d, up, tg, pins={"b": at})["action"] == "wait"
    # one upload per channel at a time
    d = _q("a", "b")
    d["items"].append({"id": "c", "project": "pc", "name": "v.mp4", "status": "posting"})
    assert pq.decide(SCHED, d, up, tg, pins={"b": at})["action"] == "wait"


def test_gate_off_is_the_old_schedule():
    tg = lambda x: ["yt"]
    d = _q("a", "b")
    at = T(2026, 11, 7, 22)
    dec = pq.decide(SCHED, d, at - 80 * 60, tg)
    assert dec["action"] == "post" and dec["item"]["id"] == "a"          # queue order, posting time


def test_planned_shows_the_sheet_slot():
    tg = lambda x: ["yt"]
    d = _q("a", "b")
    p = pq.planned(SCHED, d, NOW, tg, pins={"b": T(2026, 11, 8, 18)})
    assert list(p) == ["b"] and p["b"]["label"] == "Sun, Nov 8 at 6:00 PM ET"


# ----------------------------------------------------------- server: sync + scheduler

def _proj(root, pid, name, title, desc, status="approved"):
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    json.dump({"series": pid, "chapter": "1"}, open(os.path.join(d, "project.json"), "w"))
    open(os.path.join(d, "exports", name), "wb").write(b"\0" * 10)
    if status:
        json.dump({name: {"status": status}}, open(os.path.join(d, "reviews.json"), "w"))
    json.dump({name: {"title": title, "description": desc, "targets": ["mk:youtube"], "privacy": "public"}},
              open(os.path.join(d, "publish.json"), "w"))


def test_sync_applies_approve_rows_and_the_scheduler_obeys_the_sheet(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    root = str(tmp_path)
    ing.PROJECTS = root
    monkeypatch.delenv("SHEET_GATE_OFF", raising=False)
    _proj(root, "pa", "a.mp4", "Old A", "a1\na2\n\nbody A\n\n#X")
    _proj(root, "pb", "b.mp4", "Old B", "b1\n\nbody B")
    pq.add(root, [{"project": "pa", "name": "a.mp4"}, {"project": "pb", "name": "b.mp4"}])
    ids = {x["project"]: x["id"] for x in pq.load(root)["items"]}
    at_a, at_b = T(2026, 11, 7, 22), T(2026, 11, 8, 10)
    rows = _rows(row("pa", "Sat Nov 7, 10:00 PM", title="New A | Series", desc="n1\nn2"),
                 row("pb", "Sun Nov 8, 10:00 AM", decision="Edit"))
    # dry run writes nothing
    r = srv._sheet_sync(apply=False, now=NOW, rows=rows)
    assert r["pins"] == {ids["pa"]: at_a} and not r["changes"]
    assert srv.load_publish(os.path.join(root, "pa"))["a.mp4"]["title"] == "Old A"
    r = srv._sheet_sync(apply=True, now=NOW, rows=rows)
    md = srv.load_publish(os.path.join(root, "pa"))["a.mp4"]
    assert md["title"] == "New A | Series" and md["description"] == "n1\nn2\n\nbody A\n\n#X"
    assert [c["action"] for c in r["changes"]] == ["sheet text applied"] and r["changes"][0]["seq"] == 1
    assert any("pb: Decision is Edit" in n for n in r["needs_owner"])
    st = sg.load(root)
    assert st["last_sync"]["pins"] == {ids["pa"]: at_a} and st["slots"] == {"pa": at_a, "pb": at_b}
    assert "Edit" in st["last_sync"]["holds"][ids["pb"]]
    # applying again changes nothing
    assert srv._sheet_sync(apply=True, now=NOW, rows=rows)["changes"] == []
    # the scheduler: only pa, at 10 PM, with the slot as YouTube's publish time
    posted = []
    monkeypatch.setattr(srv, "_sheet_rows", lambda max_age=60: rows)
    monkeypatch.setattr(srv, "_local_post_blockers", lambda p, n: [])
    monkeypatch.setattr(srv, "_targets_of", lambda x: ["mk:youtube"])
    monkeypatch.setattr(srv, "studio_queue_post", lambda body, publish_at=None: posted.append((body.id, publish_at))
                        or {"ok": True, "privacy": "private"})
    real = srv._studio.load()
    monkeypatch.setattr(srv._studio, "load", lambda: {**real, "schedule": {**SCHED, "enabled_at": NOW - 86400}})
    dec = srv._schedule_post_pass(now=at_a - 80 * 60)
    assert posted == [(ids["pa"], at_a)], dec
    srv._schedule_post_pass(now=T(2026, 11, 8, 8, 40))     # pb's 10 AM slot: held (Edit)
    assert posted == [(ids["pa"], at_a)]
    # gate off: the schedule is the old one again (queue order at posting times)
    pq.mark(root, ids["pa"], status="posted")             # (the fake post above didn't settle it)
    sg.set_on(root, False)
    posted.clear()
    srv._schedule_post_pass(now=at_b - 80 * 60)
    assert posted == [(ids["pb"], at_b)]


def test_unreadable_sheet_posts_nothing(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    root = str(tmp_path)
    ing.PROJECTS = root
    monkeypatch.delenv("SHEET_GATE_OFF", raising=False)
    _proj(root, "pa", "a.mp4", "A", "a")
    pq.add(root, [{"project": "pa", "name": "a.mp4"}])
    posted = []

    def boom(max_age=60):
        raise RuntimeError("Drive down")
    monkeypatch.setattr(srv, "_sheet_rows", boom)
    monkeypatch.setattr(srv, "studio_queue_post", lambda body, publish_at=None: posted.append(body.id) or {})
    real = srv._studio.load()
    monkeypatch.setattr(srv._studio, "load", lambda: {**real, "schedule": {**SCHED, "enabled_at": NOW - 86400}})
    dec = srv._schedule_post_pass(now=T(2026, 11, 7, 20, 40))
    assert posted == [] and dec["action"] == "wait" and "can't be read" in dec["why"]


def test_approve_on_a_video_review_item_queues_it_at_its_slot(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    root = str(tmp_path)
    ing.PROJECTS = root
    monkeypatch.delenv("SHEET_GATE_OFF", raising=False)
    _proj(root, "pn", "n.mp4", "Old N", "x1\nbody", status=None)        # in Video review
    snap_pending = srv._pending_exports()
    assert list(snap_pending) == ["pn"] and snap_pending["pn"]["review_status"] == "review_pending"
    rows = _rows(row("pn", "Sun Nov 8, 6:00 PM", title="N | Series"))
    r = srv._sheet_sync(apply=True, now=NOW, rows=rows)
    q = [x for x in pq.load(root)["items"] if x["project"] == "pn"]
    assert len(q) == 1 and q[0]["status"] == "queued"
    assert r["pins"] == {q[0]["id"]: T(2026, 11, 8, 18)}
    assert srv.load_publish(os.path.join(root, "pn"))["n.mp4"]["title"] == "N | Series"
    assert [c["action"] for c in r["changes"]] == ["approved from the sheet, queued", "sheet text applied"]
    assert srv._pending_exports() == {}
