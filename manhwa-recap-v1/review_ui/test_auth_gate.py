"""Backend auth is deny-by-default (2026-10-27): without the shared secret the
public Railway URL served chapter art, the legacy board, /review and the API
map. Only health probes stay open."""
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
import server
from fastapi.testclient import TestClient

SECRET = "test-shared-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SHARED_SECRET", SECRET)
    return TestClient(server.app)


@pytest.mark.parametrize("path", ["/segimg/0", "/storyboard", "/review", "/openapi.json", "/docs",
                                  "/api/home", "/thumb/0", "/panelimg/x", "/export/x.mp4",
                                  "/splitimg/a/b.png", "/manifest.webmanifest", "/", "/anything-new"])
def test_every_path_needs_the_secret(client, path):
    assert client.get(path, follow_redirects=False).status_code == 401
    assert client.get(path, headers={"x-shared-secret": "wrong"}, follow_redirects=False).status_code == 401


def test_the_secret_opens_the_door(client):
    r = client.get("/openapi.json", headers={"x-shared-secret": SECRET})
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_health_probes_stay_open(client, path):
    assert client.get(path).status_code != 401


def test_writes_are_refused_without_the_secret(client):
    assert client.post("/api/projects/delete", json={"ids": ["x"]}).status_code == 401
