"""Drive download + verify (owner, 2026-11-01: videos live in Drive, the
studio fetches them back when a compilation needs them). Fake Drive only."""
import hashlib
import io
import json
import os

import pytest

import drive_store as ds

DATA = b"video-bytes" * 1000


class Resp(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(body=DATA, md5=None, size=None, trashed=False):
    def opener(req):
        url = req.full_url
        if "alt=media" in url:
            return Resp(body)
        return Resp(json.dumps({"id": "v1", "size": str(len(DATA) if size is None else size),
                                "md5Checksum": md5 or hashlib.md5(DATA).hexdigest(),
                                "trashed": trashed}).encode())
    return opener


@pytest.fixture(autouse=True)
def token():
    ds._tok.update(value="test-token", exp=9e12)


def test_download_checks_size_and_md5_then_takes_the_name(tmp_path):
    dest = tmp_path / "exports" / "v.mp4"
    ds.download("v1", str(dest), _open=fake())
    assert dest.read_bytes() == DATA
    assert not [p for p in os.listdir(dest.parent) if p.endswith(".part")]


def test_truncated_download_never_becomes_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)
    dest = tmp_path / "v.mp4"
    with pytest.raises(ds.DriveError):
        ds.download("v1", str(dest), _open=fake(body=DATA[:-5]))
    assert os.listdir(tmp_path) == []


def test_binned_copy_is_refused(tmp_path):
    with pytest.raises(ds.DriveError, match="bin"):
        ds.download("v1", str(tmp_path / "v.mp4"), _open=fake(trashed=True))


def _rec(pdir, md5):
    (pdir / "drive.json").write_text(json.dumps({"video": "v.mp4", "files": {"video": {"id": "v1", "md5": md5}}}))


def test_verify_needs_matching_bytes(tmp_path):
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "v.mp4").write_bytes(DATA)
    _rec(tmp_path, hashlib.md5(DATA).hexdigest())
    assert ds.verify(str(tmp_path), "v.mp4", _open=fake())
    assert not ds.verify(str(tmp_path), "v.mp4", _open=fake(md5="0" * 32))
    assert not ds.verify(str(tmp_path), "other.mp4", _open=fake())
    assert not ds.verify(str(tmp_path), "v.mp4", _open=fake(trashed=True))


def test_verify_without_local_file_uses_recorded_checksum(tmp_path):
    _rec(tmp_path, hashlib.md5(DATA).hexdigest())
    assert ds.verify(str(tmp_path), "v.mp4", _open=fake())
    _rec(tmp_path, None)                       # old record, no checksum: not proof
    assert not ds.verify(str(tmp_path), "v.mp4", _open=fake())
