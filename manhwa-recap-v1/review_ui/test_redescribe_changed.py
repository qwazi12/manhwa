"""Chapter page: re-describe only the panels the describe safety check
stopped on (owner, 2026-11-01: Dark Mage 110 stuck on 2 re-cut panels)."""
import json

import pytest
from fastapi.testclient import TestClient

import server as srv


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    recs = [{"panel_id": f"p{i}", "file": f"p{i}.png", "ok": i != 3} for i in range(1, 5)]
    (tmp_path / "descriptions.json").write_text(json.dumps(recs))
    (tmp_path / "descriptions.json.cache.json").write_text(json.dumps({
        "status": "needs_review",
        "panels": {"p1.png": "current", "p2.png": "stale", "p3.png": "stale", "p4.png": "unknown"}}))
    monkeypatch.setattr(srv, "project_dir_for", lambda pid: str(tmp_path))
    state = {"status": "error"}
    monkeypatch.setattr(srv, "_ingest_by_project", lambda: ({"dm110": {"job": "j1", **state}}, {}))
    resumed = []
    monkeypatch.setattr(srv, "jobs_resume", lambda b: resumed.append(b.job_id) or {"ok": True, "job": b.job_id})
    monkeypatch.setenv("SHARED_SECRET", "")
    return tmp_path, state, resumed, TestClient(srv.app)


def test_reports_only_good_records_that_changed(chapter):
    ch = srv._changed_panels(str(chapter[0]))
    assert ch["panels"] == ["p2", "p4"] and ch["n"] == 2 and ch["est_usd"] > 0   # p3 failed anyway


def test_not_blocked_reports_nothing(chapter):
    (chapter[0] / "descriptions.json.cache.json").write_text(json.dumps({"status": "current", "panels": {}}))
    assert srv._changed_panels(str(chapter[0])) is None


def test_drops_only_changed_records_keeps_backup_and_resumes(chapter):
    d, _, resumed, c = chapter
    r = c.post("/api/chapter/redescribe-changed", json={"project": "dm110"})
    assert r.status_code == 200 and r.json()["panels"] == ["p2", "p4"]
    assert [x["panel_id"] for x in json.loads((d / "descriptions.json").read_text())] == ["p1", "p3"]
    backup = json.loads((d / r.json()["backup"]).read_text())
    assert len(backup) == 4
    assert resumed == ["j1"]


def test_refuses_while_running(chapter):
    d, state, resumed, c = chapter
    state["status"] = "running"
    r = c.post("/api/chapter/redescribe-changed", json={"project": "dm110"})
    assert r.status_code == 409 and resumed == []
    assert len(json.loads((d / "descriptions.json").read_text())) == 4
