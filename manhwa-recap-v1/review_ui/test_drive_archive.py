"""Drive as the archive (owner, 2026-11-01): posted videos leave Railway only
once Drive holds the same bytes; merges fetch missing pieces from Drive."""
import json
import os

import pytest

import server as srv


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    p = root / "s_5"
    (p / "exports").mkdir(parents=True)
    (p / "exports" / "v.mp4").write_bytes(b"x" * 10)
    import ingest
    monkeypatch.setattr(ingest, "PROJECTS", str(root))
    monkeypatch.setattr(srv._drive, "configured", lambda: True)
    monkeypatch.setattr(srv._pq, "protected", lambda r: set())
    monkeypatch.setattr(srv, "_ev", lambda *a, **k: None)
    return p


def _posted(p, ok=True):
    (p / srv.PUBLISHES_NAME).write_text(json.dumps(
        {"v.mp4": {"results": [{"status": "published" if ok else "failed"}]}}))


def test_posted_and_verified_is_removed(proj, monkeypatch):
    _posted(proj)
    monkeypatch.setattr(srv._drive, "verify", lambda d, n: True)
    monkeypatch.setattr(srv, "_drive_copy", lambda *a: pytest.fail("no re-upload when Drive already matches"))
    srv._drive_archive_pass()
    assert not (proj / "exports" / "v.mp4").exists()


def test_unverified_copy_keeps_the_file(proj, monkeypatch):
    _posted(proj)
    monkeypatch.setattr(srv._drive, "verify", lambda d, n: False)
    sent = []
    monkeypatch.setattr(srv, "_drive_copy", lambda d, n: sent.append(n) or {"ok": 1})
    srv._drive_archive_pass()
    assert sent == ["v.mp4"] and (proj / "exports" / "v.mp4").exists()


@pytest.mark.parametrize("why", ["not posted", "queued"])
def test_unposted_or_queued_is_never_touched(proj, monkeypatch, why):
    _posted(proj, ok=(why == "queued"))
    if why == "queued":
        monkeypatch.setattr(srv._pq, "protected", lambda r: {("s_5", "v.mp4")})
    monkeypatch.setattr(srv._drive, "verify", lambda d, n: True)
    srv._drive_archive_pass()
    assert (proj / "exports" / "v.mp4").exists()


def test_merge_piece_comes_back_from_drive(tmp_path, monkeypatch):
    p = tmp_path / "s_4"
    (p / "exports").mkdir(parents=True)
    (p / "drive.json").write_text(json.dumps({"video": "old.mp4", "files": {"video": {"id": "v9"}}}))
    monkeypatch.setattr(srv, "_ev", lambda *a, **k: None)
    got = []
    monkeypatch.setattr(srv._drive, "download", lambda fid, dest: got.append((fid, dest)) or dest)
    assert srv._export_or_drive(str(p)) == "old.mp4"
    srv._restore_from_drive(str(p), "old.mp4")
    assert got == [("v9", os.path.join(str(p), "exports", "old.mp4"))]
    with pytest.raises(RuntimeError, match="no Drive copy"):
        srv._restore_from_drive(str(p), "other.mp4")


def _merged(p, hours_ago):
    import time
    (p / "reviews.json").write_text(json.dumps(
        {"v.mp4": {"status": "merged", "merged_into": "s_ch1-5", "merged_at": time.time() - hours_ago * 3600}}))


def test_chapter_inside_a_merge_leaves_after_a_day(proj, monkeypatch):
    # owner, 2026-11-05: merged chapters live in Drive, not on Railway
    _merged(proj, 25)
    monkeypatch.setattr(srv._drive, "verify", lambda d, n: True)
    srv._drive_archive_pass()
    assert not (proj / "exports" / "v.mp4").exists()


def test_fresh_merge_or_unverified_copy_keeps_the_chapter(proj, monkeypatch):
    _merged(proj, 2)
    monkeypatch.setattr(srv._drive, "verify", lambda d, n: True)
    srv._drive_archive_pass()
    assert (proj / "exports" / "v.mp4").exists()                 # still within the day
    _merged(proj, 25)
    monkeypatch.setattr(srv._drive, "verify", lambda d, n: False)
    monkeypatch.setattr(srv, "_drive_copy", lambda d, n: None)  # Drive copy failed
    srv._drive_archive_pass()
    assert (proj / "exports" / "v.mp4").exists()


def test_unmerge_fetches_a_piece_back_from_drive(tmp_path, monkeypatch):
    import ingest
    root = tmp_path / "projects"
    cp = root / "s_5"
    (cp / "exports").mkdir(parents=True)
    (cp / "reviews.json").write_text(json.dumps({"v.mp4": {"status": "merged", "merged_into": "s_ch4-5"}}))
    monkeypatch.setattr(ingest, "PROJECTS", str(root))
    monkeypatch.setattr(srv, "_ev", lambda *a, **k: None)
    got = []
    monkeypatch.setattr(srv, "_restore_from_drive", lambda d, n: got.append((os.path.basename(d), n)))
    srv._release_merge_pieces(str(root / "s_ch4-5"), {"merged_from": [{"project": "s_5", "name": "v.mp4", "review": None}]})
    import time
    for _ in range(50):
        if got:
            break
        time.sleep(0.02)
    assert got == [("s_5", "v.mp4")]
