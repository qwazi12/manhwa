"""Offline boundary tests; no service launch, provider requests or real media."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from starlette.testclient import TestClient
from starlette.responses import JSONResponse
from starlette.applications import Starlette
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prepare
from staging_backend import OfflineBoundary, blocked


def test_environment_never_inherits_external_credentials_or_remote_database(tmp_path):
    inherited = dict(PATH=os.environ["PATH"], HOME="/production/home", GEMINI_API_KEY="secret",
                     DATABASE_URL="postgresql://production", BACKEND_URL="https://production",
                     UPLOAD_POST_API_KEY="secret", GOOGLE_APPLICATION_CREDENTIALS="/prod/key.json")
    env = prepare.environment(tmp_path, dict(user="u", password="p", shared_secret="s"), inherited)
    assert env["BACKEND_URL"] == "http://127.0.0.1:8138"
    assert env["DATABASE_URL"] == f"sqlite:///{tmp_path}/data/staging.db"
    assert env["HOME"] == str(tmp_path / "home")
    assert env["MAX_DAILY_SPEND_USD"] == env["MAX_DAILY_SPEND_CEILING_USD"] == "0"
    assert env["AUTOPILOT_SCHEDULER"] == env["BACKUP_ENABLED"] == "0"
    assert not any(k in env for k in ("GEMINI_API_KEY", "UPLOAD_POST_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"))


def test_state_symlinks_cannot_escape_snapshot(tmp_path):
    outside = tmp_path / "production"
    outside.mkdir()
    root = tmp_path / "staging"
    root.mkdir()
    (root / "projects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        prepare.checked(root, root / "projects")


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/publishing/publish"), ("GET", "/api/youtube/callback"),
    ("GET", "/api/publishing/connect"), ("POST", "/api/drive/copy"),
    ("POST", "/api/autopilot/run"), ("POST", "/api/studio/queue/post"),
    ("POST", "/api/studio/bulk"), ("POST", "/api/schedule"),
    ("POST", "/api/spend/cap"), ("POST", "/api/backup/run"),
])
def test_unsafe_routes_do_not_reach_actual_application(method, path):
    async def downstream(request):
        raise AssertionError("Unsafe route reached application")
    actual = Starlette(routes=[Route("/{path:path}", downstream, methods=[method])])
    with TestClient(OfflineBoundary(actual)) as client:
        response = client.request(method, path)
    assert response.status_code == 403
    assert response.json()["code"] == "staging_disabled"


def test_real_application_responses_pass_through_unchanged():
    async def downstream(request):
        return JSONResponse({"path": request.url.path, "actual": True})
    actual = Starlette(routes=[Route("/{path:path}", downstream)])
    with TestClient(OfflineBoundary(actual)) as client:
        for path in ("/api/source-coverage", "/api/board/staging-ch34", "/export/video.mp4"):
            assert client.get(path).json() == {"path": path, "actual": True}
    assert not blocked("POST", "/api/storyboard/assign")
    assert not blocked("POST", "/api/publish")  # local metadata editing, not upload


def test_guard_blocks_external_connections_in_inherited_python_worker():
    script = '''
import socket
for action in (lambda: socket.create_connection(("generativelanguage.googleapis.com", 443)),
               lambda: socket.socket().connect(("8.8.8.8", 443)),
               lambda: socket.socket().connect_ex(("8.8.8.8", 443))):
    try: action()
    except PermissionError: pass
    else: raise AssertionError("External connection permitted")
s=socket.socket(); s.bind(("127.0.0.1", 0)); s.listen()
c=socket.create_connection(s.getsockname()); peer,_=s.accept()
c.close(); peer.close(); s.close()
'''
    env = {"PATH": os.environ["PATH"], "FLAMINGO_STAGING": "1", "PYTHONPATH": str(HERE)}
    subprocess.run([sys.executable, "-c", script], env=env, check=True, timeout=15)


def test_launch_rejected_before_reading_any_snapshot_without_owner_approval():
    result = subprocess.run([sys.executable, str(HERE / "prepare.py"), "launch", "/does/not/exist"],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "Owner approval required" in result.stderr


def test_prepare_uses_committed_sources_only_and_private_credentials(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    for cmd in (["git", "init", "-b", "dev/flamingo-test"],
                ["git", "config", "user.name", "Test"],
                ["git", "config", "user.email", "test@example.invalid"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    tools = root / "deploy/staging"
    tools.mkdir(parents=True)
    for name in ("prepare.py", "sitecustomize.py", "staging_backend.py"):
        (tools / name).write_text("# tracked fixture\n")
    subprocess.run(["git", "add", "deploy"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
    (root / ".env").write_text("GEMINI_API_KEY=private\n")
    production = root / "manhwa-recap-v1/review_ui/projects/original"
    production.mkdir(parents=True)
    (production / "project.json").write_text('{"unchanged":true}')
    monkeypatch.setattr(prepare, "ROOT", root)
    stage = root / "work/staging"
    manifest = prepare.prepare(stage, dependencies=False)
    assert manifest["prepared"] and not manifest["deployed"]
    assert not (stage / "app/.env").exists()
    assert not (stage / "app/manhwa-recap-v1/review_ui/projects").exists()
    assert (production / "project.json").read_text() == '{"unchanged":true}'
    assert (stage.stat().st_mode & 0o777) == 0o700
    assert ((stage / "credentials.json").stat().st_mode & 0o777) == 0o600
    assert len(json.loads((stage / "credentials.json").read_text())["password"]) >= 24
    with pytest.raises(ValueError, match="overwrite"):
        prepare.prepare(stage, dependencies=False)
