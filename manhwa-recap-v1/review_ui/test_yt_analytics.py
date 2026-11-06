"""F3 read-only YouTube Analytics (owner, 2026-11-06). All HTTP is faked."""
import json
import os
import urllib.parse

import pytest

import yt_analytics as ya

GRANT = {"type": "authorized_user", "client_id": "cid", "client_secret": "csec-test",
         "refresh_token": "rt-test", "scopes": [ya.SCOPE]}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    ya._tok.update(value=None, exp=0.0)
    monkeypatch.delenv(ya.ENV, raising=False)


def _fake(rows, calls):
    def http(url, data=None, token=None):
        calls.append((url, data, token))
        if url == ya.TOKEN_URI:
            return {"access_token": "at-test", "expires_in": 3600}
        cols = ["views", "likes"] if "dimensions" not in url else ["day", "views"]
        return {"columnHeaders": [{"name": c} for c in cols], "rows": rows}
    return http


def test_status_not_configured_and_bad_grants(monkeypatch):
    assert ya.status()["configured"] is False
    for bad in ("{", json.dumps({**GRANT, "type": "service_account"}),
                json.dumps({**GRANT, "scopes": ["https://www.googleapis.com/auth/youtube"]}),
                json.dumps({**GRANT, "token_uri": "https://evil.example/token"})):
        monkeypatch.setenv(ya.ENV, bad)
        st = ya.status()
        assert st["configured"] is False and "rt-test" not in json.dumps(st) and "csec-test" not in json.dumps(st)
    monkeypatch.setenv(ya.ENV, json.dumps(GRANT))
    assert ya.status()["configured"] is True


def test_report_queries_own_channel_for_one_video(monkeypatch):
    monkeypatch.setenv(ya.ENV, json.dumps(GRANT))
    calls = []
    r = ya.video_report("IvrdUG__pkg", "2026-11-06", "2026-11-12", _http=_fake([[120, 7]], calls))
    assert r["status"] == "observed" and r["metrics"] == {"views": 120, "likes": 7}
    (turl, tdata, _), (rurl, _, tok) = calls
    assert turl == ya.TOKEN_URI and tdata["grant_type"] == "refresh_token" and tdata["refresh_token"] == "rt-test"
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(rurl).query))
    assert q["ids"] == "channel==MINE" and q["filters"] == "video==IvrdUG__pkg"
    assert q["startDate"] == "2026-11-06" and q["endDate"] == "2026-11-12" and tok == "at-test"
    assert "estimatedMinutesWatched" in q["metrics"] and "subscribersGained" in q["metrics"]
    ya.video_report("IvrdUG__pkg", "2026-11-06", "2026-11-07", _http=_fake([[1, 1]], calls))
    assert sum(1 for c in calls if c[0] == ya.TOKEN_URI) == 1          # token reused in memory


def test_by_day_and_missing_rows(monkeypatch):
    monkeypatch.setenv(ya.ENV, json.dumps(GRANT))
    r = ya.video_report("IvrdUG__pkg", "2026-11-06", "2026-11-07", by_day=True,
                        _http=_fake([["2026-11-06", 5], ["2026-11-07", 9]], []))
    assert r["days"] == [{"day": "2026-11-06", "views": 5}, {"day": "2026-11-07", "views": 9}]
    r = ya.video_report("IvrdUG__pkg", "2026-11-06", "2026-11-07", _http=_fake([], []))
    assert r["status"] == "missing" and "metrics" not in r


def test_bad_input_and_not_configured(monkeypatch):
    with pytest.raises(ya.InputError):
        ya.video_report("short", "2026-11-06", "2026-11-07", _http=_fake([], []))
    with pytest.raises(ya.InputError):
        ya.video_report("IvrdUG__pkg", "2026-11-08", "2026-11-07", _http=_fake([], []))
    with pytest.raises(ya.InputError):
        ya.video_report("IvrdUG__pkg", "nov 6", "2026-11-07", _http=_fake([], []))
    with pytest.raises(ya.NotConfigured):
        ya.video_report("IvrdUG__pkg", "2026-11-06", "2026-11-07", _http=_fake([], []))


def test_routes_read_only(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import ingest
    import server
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    monkeypatch.delenv("SHARED_SECRET", raising=False)
    c = TestClient(server.app)
    assert c.get("/api/growth/analytics/status").json()["configured"] is False
    assert c.get("/api/growth/analytics/video?video_id=IvrdUG__pkg&start=2026-11-06&end=2026-11-07").status_code == 503
    assert c.get("/api/growth/analytics/video?video_id=bad&start=2026-11-06&end=2026-11-07").status_code == 400
    monkeypatch.setenv(ya.ENV, json.dumps(GRANT))
    monkeypatch.setattr(ya, "_http_json", _fake([[3, 1]], []))
    r = c.get("/api/growth/analytics/video?video_id=IvrdUG__pkg&start=2026-11-06&end=2026-11-07")
    assert r.status_code == 200 and r.json()["metrics"] == {"views": 3, "likes": 1}
    assert os.listdir(tmp_path) == []                                   # nothing written
