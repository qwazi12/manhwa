"""The live QA project cannot reach connected distribution services."""
from pathlib import Path
import pytest
import qa_project_policy as policy


@pytest.mark.parametrize("marker", ["empty", "malformed", "symlink"])
def test_marker_is_fail_closed(tmp_path, marker):
    path = tmp_path / policy.MARKER
    if marker == "symlink":
        path.symlink_to(tmp_path / "missing")
    else:
        path.write_text("" if marker == "empty" else "invalid JSON")
    assert policy.restricted(tmp_path)
    assert not policy.restricted(tmp_path / "normal")


def test_real_export_worker_keeps_qa_and_packaging_but_never_uploads(tmp_path, monkeypatch):
    import server
    (tmp_path / policy.MARKER).touch()
    calls = []
    monkeypatch.setattr(server._mqa, "start", lambda *a: calls.append("qa"))
    monkeypatch.setattr(server, "_prepare_publish", lambda *a: calls.append("packaging"))
    monkeypatch.setattr(server, "_qc_bundle", lambda *a: calls.append("checks"))
    monkeypatch.setattr(server._drive, "configured", lambda: True)
    monkeypatch.setattr(server._drive, "copy_chapter", lambda *a, **k: pytest.fail("Drive upload attempted"))
    server._after_export(str(tmp_path), "final.mp4")
    server._drive_copy(str(tmp_path), "final.mp4")
    server._drive_copy_later(str(tmp_path), "final.mp4")
    assert calls == ["qa", "packaging", "checks"]


def test_review_and_scheduler_cannot_arm_a_qa_project(tmp_path, monkeypatch):
    import server
    import publish_queue
    project = tmp_path / "series_34-liveqa"
    project.mkdir()
    (project / policy.MARKER).touch()
    monkeypatch.setattr(server, "project_dir_for", lambda _: str(project))
    monkeypatch.setattr(server._mqa, "enforcement_enabled", lambda: False)
    assert server._media_readiness_blockers(str(project), "final.mp4") == [policy.REASON]
    with pytest.raises(server.HTTPException) as e:
        server.api_review_save(server.ReviewIn(project=project.name, name="final.mp4", status="approved"))
    assert e.value.status_code == 403
    queue = tmp_path / publish_queue.NAME
    queue.write_text('{"items": []}\n')
    before = queue.read_bytes(), queue.stat().st_mtime_ns
    added, skipped = publish_queue.add(str(tmp_path), [{"project": project.name, "name": "final.mp4"}])
    assert not added and skipped[0]["reason"] == policy.REASON
    assert (queue.read_bytes(), queue.stat().st_mtime_ns) == before
