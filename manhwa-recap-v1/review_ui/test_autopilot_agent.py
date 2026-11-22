"""Autopilot engine "agent" (owner, 2026-11-21: "Autopilot should rely on you
and when I choose it should be able to go back to its usual way ... A route
that calls you and a route that calls Gemini").

gemini (default): picks go to the paid ingest, as before. agent: a pick is
downloaded and cut for free and waits for a writer; GET /api/hand/queue lists
what waits; a hand-over is voiced and then rendered by itself;
agent_fallback_hours (0 = never) hands a chapter that waited too long to Gemini.
No network, no money. Run: python3 -m pytest test_autopilot_agent.py -q"""
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
from test_generation_cache import cache, ingest_fixture, offline  # noqa: E402,F401
from test_hand_made import URL, _body, studio  # noqa: E402,F401
import autopilot as ap  # noqa: E402
import hand_made  # noqa: E402


def _series(sid, rank, chapters):
    key = f"webtoon:{rank}"
    return {"id": sid, "title": sid.title(), "tier": "greenlight", "rank": rank, "best_mirror": key,
            "ingested": [], "mirrors": [{"series_key": key, "source": "webtoon", "support": "supported",
                                         "status": "ok", "series_url": f"https://src/{sid}",
                                         "chapters": [str(c) for c in chapters]}]}


class Fake:
    def __init__(self, view):
        self.view_rows, self.queued, self.prepared = view, [], []
        self.busy, self.preparing, self.writer_busy = False, False, set()
        self.prepare_error = None

    def enqueue(self, url, engine):
        self.queued.append((url, engine))
        return f"job{len(self.queued)}"

    def prepare(self, url):
        if self.prepare_error:
            raise RuntimeError(self.prepare_error)
        self.prepared.append(url)
        return url

    def deps(self):
        return {"view": lambda: self.view_rows, "refresh": lambda: None, "canon": lambda k: k,
                "live_projects": lambda: set(), "queue_busy": lambda: self.busy,
                "spend": lambda: (0.0, 10.0), "ap_spend": lambda: 0.0,
                "chapter_url": lambda row, ch: f"https://src/{row['series_id']}/{ch}",
                "project_id": lambda url: url.rsplit("/", 2)[-2] + "_" + url.rsplit("/", 1)[-1],
                "enqueue": self.enqueue, "prepare": self.prepare,
                "preparing": lambda: self.preparing, "writer_busy": lambda: self.writer_busy}


@pytest.fixture
def root(tmp_path):
    return str(tmp_path)


# ------------------------------------------------------------------ the setting
def test_engine_defaults_to_gemini_accepts_agent_and_refuses_anything_else(root):
    s = ap.settings(root)
    assert s["engine"] == "gemini" and s["agent_fallback_hours"] == 0
    ap.update_settings(root, {"engine": "agent"})
    assert ap.settings(root)["engine"] == "agent"
    for bad in ("claude", "Agent", "", "gpt"):
        with pytest.raises(ValueError):
            ap.update_settings(root, {"engine": bad})
    assert ap.settings(root)["engine"] == "agent"          # a refused change saves nothing
    ap.update_settings(root, {"engine": "gemini"})
    assert ap.settings(root)["engine"] == "gemini"
    for bad in (-1, 169, float("nan")):
        with pytest.raises(ValueError):
            ap.update_settings(root, {"agent_fallback_hours": bad})
    ap.update_settings(root, {"agent_fallback_hours": 12})
    assert ap.settings(root)["agent_fallback_hours"] == 12
    # back on gemini, a pick is the paid ingest exactly as before, and nothing is prepared
    fake = Fake([_series("alpha", 1, range(1, 6))])
    ap.update_settings(root, {"enabled": True})
    r = ap.tick(root, fake.deps(), time.time())
    assert r["job"] == "job1" and fake.queued == [("https://src/alpha/3", "gemini")] and fake.prepared == []


# ------------------------------------------------------------------ picking


def test_an_agent_pick_is_prepared_for_the_writer_and_never_ingested(root):
    fake = Fake([_series("alpha", 1, range(1, 11)), _series("bravo", 2, range(1, 6))])
    ap.update_settings(root, {"enabled": True, "engine": "agent", "per_day": 3})
    now = time.time()
    picks = [ap.tick(root, fake.deps(), now) for _ in range(3)]
    assert fake.queued == []                                      # no paid ingest at all
    assert fake.prepared == ["https://src/alpha/8", "https://src/bravo/3", "https://src/alpha/9"]
    assert all(p and p["engine"] == "agent" and p["job"] is None for p in picks)
    led = ap.load(root)["ledger"]
    assert {e["status"] for e in led.values()} == {ap.WRITER}
    # counted toward per_day like any pick: the 4th waits for tomorrow
    assert ap.tick(root, fake.deps(), now) is None and "limit" in ap.runtime["last_result"]
    # a chapter waiting for the writer is never picked again (round robin or the scheduled list)
    ap.priority_edit(root, "add", "alpha", ["8"])
    assert ap.tick(root, fake.deps(), now + 86400)["chapter"] != "8"
    assert fake.prepared.count("https://src/alpha/8") == 1


def test_an_agent_pick_waits_while_one_is_being_prepared(root):
    fake = Fake([_series("alpha", 1, range(1, 6))])
    fake.preparing = True
    ap.update_settings(root, {"enabled": True, "engine": "agent"})
    assert ap.tick(root, fake.deps(), time.time()) is None and fake.prepared == []
    assert "being downloaded" in ap.runtime["last_result"]


def test_a_failed_prepare_is_a_strike_and_the_hand_over_settles_the_ledger(root):
    fake = Fake([_series("alpha", 1, range(1, 6))])
    ap.update_settings(root, {"enabled": True, "engine": "agent"})
    fake.prepare_error = "no pages could be downloaded"
    now = time.time()
    assert ap.tick(root, fake.deps(), now) is None
    e = ap.load(root)["ledger"]["webtoon:1|3"]
    assert e["status"] == "failed" and e["fails"] == 1 and "no pages" in e["error"]
    # tried again after the cooldown, then handed over and voiced
    fake.prepare_error = None
    assert ap.tick(root, fake.deps(), now + ap.FAIL_COOLDOWN + 1)["chapter"] == "3"
    assert ap.writer_handed(root, "alpha_3", "hjob") is not None
    assert ap.load(root)["ledger"]["webtoon:1|3"]["status"] == "queued"
    ap.on_job_end(root, "hjob", "done", None, 0.12)
    e = ap.load(root)["ledger"]["webtoon:1|3"]
    assert e["status"] == "done" and e["cost"] == 0.12
    # the async prepare failing (server thread) is a strike too
    ap.tick(root, fake.deps(), now + ap.FAIL_COOLDOWN + 2)
    assert ap.writer_prepare_failed(root, "alpha_4", "blocked")["status"] == "failed"


def test_status_reports_the_engine_the_writers_backlog_and_how_to_switch_back(root):
    fake = Fake([_series("alpha", 1, range(1, 6))])
    ap.update_settings(root, {"enabled": True, "engine": "agent"})
    now = time.time()
    ap.tick(root, fake.deps(), now - 3600)
    st = ap.status(root, fake.deps(), now)
    assert st["engine"] == "agent"
    a = st["agent"]
    assert a["waiting_for_writer"] == 1 and 3590 <= a["oldest_wait_seconds"] <= 3610
    assert a["oldest"]["project"] == "alpha_3" and a["fallback"].startswith("off")
    assert '"engine": "gemini"' in a["switch_back"] and a["queue"] == "GET /api/hand/queue"


# ------------------------------------------------------------------ fallback
def _waiting_for_writer(root, fake, hours_ago):
    ap.update_settings(root, {"enabled": True, "engine": "agent", "per_day": 1})
    then = time.time() - hours_ago * 3600
    assert ap.tick(root, fake.deps(), then)["project"] == "alpha_3"
    ap.update_settings(root, {"per_day": 0})            # no new pick: only the fallback can start work


def test_fallback_is_off_by_default(root):
    fake = Fake([_series("alpha", 1, range(1, 6))])
    _waiting_for_writer(root, fake, 100)
    assert ap.tick(root, fake.deps(), time.time()) is None and fake.queued == []
    assert ap.load(root)["ledger"]["webtoon:1|3"]["status"] == ap.WRITER


def test_fallback_hands_a_chapter_that_waited_too_long_to_gemini(root):
    fake = Fake([_series("alpha", 1, range(1, 6))])
    _waiting_for_writer(root, fake, 5)
    ap.update_settings(root, {"agent_fallback_hours": 6})
    assert ap.tick(root, fake.deps(), time.time()) is None and fake.queued == []     # not yet
    fake.writer_busy = {"alpha_3"}                                                   # a writer holds it
    assert ap.tick(root, fake.deps(), time.time() + 2 * 3600) is None and fake.queued == []
    fake.writer_busy = set()
    fake.busy = True                                                                 # the line is busy
    assert ap.tick(root, fake.deps(), time.time() + 2 * 3600) is None and fake.queued == []
    fake.busy = False
    r = ap.tick(root, fake.deps(), time.time() + 2 * 3600)
    assert r["fallback"] is True and fake.queued == [("https://src/alpha/3", "gemini")]
    e = ap.load(root)["ledger"]["webtoon:1|3"]
    assert e["status"] == "queued" and e["job"] == "job1" and e["engine"] == "gemini"


def test_switching_back_releases_what_no_writer_took(root):
    fake = Fake([_series("alpha", 1, range(1, 11))])
    ap.update_settings(root, {"enabled": True, "engine": "agent"})
    now = time.time()
    ap.tick(root, fake.deps(), now)
    ap.tick(root, fake.deps(), now)
    assert ap.release_writer(root, keep={"alpha_9"}) == ["webtoon:1|8"]
    assert set(ap.load(root)["ledger"]) == {"webtoon:1|9"}
    ap.update_settings(root, {"engine": "gemini"})
    assert ap.tick(root, fake.deps(), now)["chapter"] == "8" and fake.queued == [("https://src/alpha/8", "gemini")]


# ------------------------------------------------------------------ server routes
@pytest.fixture
def srv(studio, monkeypatch):
    pdir, counts, ingest = studio
    sys.modules.pop("server", None)            # the harness's stand-in; the real module is wanted here
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SHARED_SECRET", "s")
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    return server, TestClient(server.app), {"x-shared-secret": "s"}, pdir


def test_the_writers_queue_and_claims(srv, monkeypatch):
    server, c, h, pdir = srv
    root, pid = str(pdir.parent), pdir.name
    # one being prepared, one whose prepare failed, beside the prepared chapter
    hand_made.prep_save(str(pdir.parent / "other_2"), {"url": URL, "state": "preparing", "at": time.time()})
    hand_made.prep_save(str(pdir.parent / "other_3"), {"url": URL, "state": "failed", "at": time.time(),
                                                        "error": "blocked"})
    q = c.get("/api/hand/queue", headers=h).json()
    assert [r["chapter_id"] for r in q["waiting"]] == [pid] and q["next"] == pid
    assert q["waiting"][0]["state"] == "waiting" and q["waiting"][0]["n_panels"] == 3
    assert [r["chapter_id"] for r in q["preparing"]] == ["other_2"]
    assert [r["chapter_id"] for r in q["failed"]] == ["other_3"] and q["failed"][0]["error"] == "blocked"
    # a claim keeps a second writer off it; the same writer renews; release gives it back
    r = c.post("/api/hand/queue/claim", headers=h, json={"pid": pid, "by": "w1"})
    assert r.status_code == 200 and r.json()["by"] == "w1"
    assert c.post("/api/hand/queue/claim", headers=h, json={"pid": pid, "by": "w2"}).status_code == 409
    assert c.post("/api/hand/queue/claim", headers=h, json={"pid": pid}).status_code == 409
    assert c.post("/api/hand/queue/claim", headers=h, json={"chapter": pid, "by": "w1"}).status_code == 200
    q = c.get("/api/hand/queue", headers=h).json()
    assert q["waiting"][0]["claim"]["by"] == "w1" and q["next"] is None
    # a claim runs out by itself
    assert hand_made.claims(root, now=time.time() + hand_made.CLAIM_S + 1) == {}
    assert c.post("/api/hand/queue/claim", headers=h, json={"pid": pid, "release": True}).json()["released"]
    assert c.post("/api/hand/queue/claim", headers=h, json={"pid": "other_2"}).status_code == 409
    # a hand-over clears the claim; with no board yet it is listed as "sent", with a board it leaves
    c.post("/api/hand/queue/claim", headers=h, json={"pid": pid, "by": "w1"})
    monkeypatch.setattr(server, "_enqueue_ingest", lambda url, **kw: "job9")
    assert c.post(f"/api/hand/{pid}", headers=h, json=_body()).status_code == 200
    q = c.get("/api/hand/queue", headers=h).json()
    assert q["waiting"] == [] and [r["chapter_id"] for r in q["sent"]] == [pid] and q["sent"][0]["claim"] is None
    (pdir / "segments.json").write_text("[]")
    assert c.get("/api/hand/queue", headers=h).json()["sent"] == []


def test_a_hand_over_links_the_autopilot_pick_to_its_voice_job(srv, monkeypatch):
    server, c, h, pdir = srv
    root, pid = str(pdir.parent), pdir.name
    ap.record_writer(root, "k|1", series_id="s", chapter="1", project=pid, url=URL)
    monkeypatch.setattr(server, "_enqueue_ingest", lambda url, **kw: "job9")
    assert c.post(f"/api/hand/{pid}", headers=h, json=_body()).status_code == 200
    e = ap.load(root)["ledger"]["k|1"]
    assert e["status"] == "queued" and e["job"] == "job9"


def test_autopilot_prepares_through_the_hand_route_once(srv, monkeypatch):
    server, c, h, pdir = srv
    calls = []
    monkeypatch.setattr(server, "api_hand_prepare", lambda body: calls.append(body.url) or {"chapter": "x"})
    assert server._ap_hand_prepare(URL) == pdir.name and calls == []        # already cut: not downloaded again
    hand_made.prep_save(str(pdir), {"url": URL, "state": "failed", "at": time.time()})
    assert server._ap_hand_prepare(URL) == "x" and calls == [URL]
    deps = server._ap_deps()
    assert deps["prepare"] is server._ap_hand_prepare and deps["preparing"]() is False


def test_the_settings_route_switches_engines_and_back(srv):
    server, c, h, pdir = srv
    root = str(pdir.parent)
    assert c.post("/api/autopilot/settings", headers=h, json={"engine": "claude"}).status_code == 400
    r = c.post("/api/autopilot/settings", headers=h, json={"engine": "agent", "agent_fallback_hours": 0}).json()
    assert r["settings"]["engine"] == "agent" and "gemini" in r["engine_note"]
    ap.record_writer(root, "k|1", series_id="s", chapter="1", project="free_1", url=URL)
    ap.record_writer(root, "k|2", series_id="s", chapter="2", project=pdir.name, url=URL)
    hand_made.claim(root, pdir.name, "w1")
    r = c.post("/api/autopilot/settings", headers=h, json={"engine": "gemini"}).json()
    assert r["settings"]["engine"] == "gemini" and r["released"] == ["k|1"]
    assert set(ap.load(root)["ledger"]) == {"k|2"}                          # the claimed one stays


def test_a_finished_hand_over_is_rendered_by_itself_and_a_failed_one_is_not(srv, monkeypatch):
    server, c, h, pdir = srv
    monkeypatch.setattr(server.threading, "Timer", lambda *a, **k: SimpleNamespace(start=lambda: None))
    for status, source, renders in [("done", "hand", True), ("error", "hand", False), ("cancelled", "hand", False),
                                    ("budget_paused", "hand", False),
                                    ("done", "autopilot", False), ("done", "manual", False)]:
        added = []
        monkeypatch.setattr(server, "render_queue_add",
                            lambda body: added.append((body.projects, body.keep_voice)) or {"added": body.projects})
        monkeypatch.setitem(server.INGEST, "jh", {"status": status, "source": source, "url": URL, "variant": "",
                                                  "error": None if status == "done" else "boom"})
        server._after_ingest("jh")
        assert added == ([([pdir.name], True)] if renders else []), (status, source)
