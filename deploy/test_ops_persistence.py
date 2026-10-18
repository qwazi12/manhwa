"""Offline migration regressions: no providers, jobs or production paths."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys

import pytest


DEPLOY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("persist_ops_data", DEPLOY / "persist_ops_data.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


def fixture_data(tmp_path):
    source = tmp_path / "container" / "data"
    target = tmp_path / "volume" / "studio-ops"
    source.mkdir(parents=True)
    target.parent.mkdir()
    with sqlite3.connect(source / "manhwa.db") as connection:
        connection.execute("CREATE TABLE records(value TEXT)")
        connection.execute("INSERT INTO records VALUES ('preserve me')")
    (source / "backups").mkdir()
    (source / "backups" / "existing.db.gz").write_bytes(b"existing backup bytes")
    return source, target


def rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM records").fetchall()


def test_prepare_copies_backup_and_committed_wal_without_changing_source(tmp_path):
    source, target = fixture_data(tmp_path)
    with sqlite3.connect(source / "manhwa.db") as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO records VALUES ('committed WAL row')")
        writer.commit()
        before = ops.fingerprint(source)
        result = ops.prepare(source, target)
        assert result["state"] == "prepared"
        assert ops.fingerprint(source) == before
        assert rows(target / "manhwa.db") == [('preserve me',), ('committed WAL row',)]
        assert not (target / "manhwa.db-wal").exists()
        assert not (target / "manhwa.db-shm").exists()
        assert not source.is_symlink()
        assert (target / "backups" / "existing.db.gz").read_bytes() == b"existing backup bytes"


def test_activation_retains_original_and_restart_keeps_new_persistent_rows(tmp_path):
    source, target = fixture_data(tmp_path)
    original = ops.fingerprint(source)
    ops.prepare(source, target)
    ops.prepare(source, target, activate=True)
    assert source.resolve() == target.resolve()
    assert ops.fingerprint(source.with_name('data.pre-persistence')) == original
    with sqlite3.connect(source / "manhwa.db") as connection:
        connection.execute("INSERT INTO records VALUES ('after activation')")
    assert ops.prepare(source, target, activate=True)["state"] == "already_linked"
    assert rows(source / "manhwa.db")[-1] == ('after activation',)


def test_new_container_uses_prepared_volume_and_preserves_its_updates(tmp_path):
    source, target = fixture_data(tmp_path)
    ops.prepare(source, target)
    with sqlite3.connect(target / "manhwa.db") as connection:
        connection.execute("INSERT INTO records VALUES ('new persistent row')")
    new_source = tmp_path / "next-container" / "data"
    new_source.parent.mkdir()
    ops.prepare(new_source, target, activate=True, require_prepared=True)
    assert rows(new_source / "manhwa.db")[-1] == ('new persistent row',)


def test_conflicting_target_is_never_overwritten(tmp_path):
    source, target = fixture_data(tmp_path)
    target.mkdir()
    (target / "important").write_text("keep")
    before = ops.fingerprint(source)
    with pytest.raises(RuntimeError, match="no migration record"):
        ops.prepare(source, target, activate=True)
    assert ops.fingerprint(source) == before
    assert (target / "important").read_text() == "keep"


def test_source_drift_after_preparation_prevents_activation(tmp_path):
    source, target = fixture_data(tmp_path)
    ops.prepare(source, target)
    (source / "backups" / "another.db.gz").write_bytes(b"newer")
    with pytest.raises(RuntimeError, match="changed or conflicts"):
        ops.prepare(source, target, activate=True)
    assert not source.is_symlink()
    assert (source / "backups" / "another.db.gz").read_bytes() == b"newer"


def test_prepared_copy_corruption_prevents_activation(tmp_path):
    source, target = fixture_data(tmp_path)
    ops.prepare(source, target)
    (target / "backups" / "existing.db.gz").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="copy changed"):
        ops.prepare(source, target, activate=True)
    assert not source.is_symlink()


def test_corrupt_database_does_not_publish_a_target_or_lose_source(tmp_path):
    source, target = fixture_data(tmp_path)
    (source / "manhwa.db").write_bytes(b"not SQLite")
    before = ops.fingerprint(source)
    with pytest.raises(sqlite3.DatabaseError):
        ops.prepare(source, target, activate=True)
    assert ops.fingerprint(source) == before
    assert not target.exists()
    assert not list(target.parent.glob('.ops-migration-*'))


def test_failed_copy_preserves_original_and_never_activates(tmp_path, monkeypatch):
    source, target = fixture_data(tmp_path)
    before = ops.fingerprint(source)
    def fail(*args, **kwargs):
        raise OSError("fixture disk full")
    monkeypatch.setattr(ops.shutil, "copy2", fail)
    with pytest.raises(OSError, match="disk full"):
        ops.prepare(source, target, activate=True)
    assert ops.fingerprint(source) == before
    assert not target.exists()


def test_source_symlink_and_missing_mount_are_refused(tmp_path):
    source, target = fixture_data(tmp_path)
    (source / "external").symlink_to(tmp_path / "private")
    with pytest.raises(RuntimeError, match="symlink"):
        ops.prepare(source, target)
    assert not target.exists()
    (source / "external").unlink()
    missing = tmp_path / "missing-mount" / "studio-ops"
    with pytest.raises(RuntimeError, match="mount is unavailable"):
        ops.prepare(source, missing)
    assert not missing.parent.exists()


def test_existing_studio_requires_pre_release_copy(tmp_path):
    source, target = fixture_data(tmp_path)
    before = ops.fingerprint(source)
    with pytest.raises(RuntimeError, match="pre-release data copy"):
        ops.prepare(source, target, activate=True, require_prepared=True)
    assert ops.fingerprint(source) == before
    assert not target.exists()


def test_existing_retained_original_is_never_replaced(tmp_path):
    source, target = fixture_data(tmp_path)
    ops.prepare(source, target)
    retained = source.with_name('data.pre-persistence')
    retained.mkdir()
    (retained / 'important').write_text('keep')
    with pytest.raises(RuntimeError, match='Retained original already exists'):
        ops.prepare(source, target, activate=True)
    assert not source.is_symlink()
    assert (retained / 'important').read_text() == 'keep'


def test_link_failure_rolls_back_original_directory(tmp_path, monkeypatch):
    source, target = fixture_data(tmp_path)
    ops.prepare(source, target)
    before = ops.fingerprint(source)
    def fail(*args, **kwargs):
        raise OSError('fixture link failure')
    monkeypatch.setattr(Path, 'symlink_to', fail)
    with pytest.raises(OSError, match='link failure'):
        ops.prepare(source, target, activate=True)
    assert ops.fingerprint(source) == before
    assert not source.with_name('data.pre-persistence').exists()


def test_actual_database_backup_survives_container_replacement(tmp_path, monkeypatch):
    source, target = fixture_data(tmp_path)
    ui = DEPLOY.parent / 'manhwa-recap-v1' / 'review_ui'
    cfg_spec = importlib.util.spec_from_file_location('ops_config', ui / 'ops_config.py')
    config = importlib.util.module_from_spec(cfg_spec)
    cfg_spec.loader.exec_module(config)
    monkeypatch.delenv('MANHWA_DATA_DIR', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(config, 'HERE', source.parent)
    # Never let an unrelated /data mount on a CI/developer host select storage.
    monkeypatch.setattr(config.Settings, 'data_path', property(lambda _: config.HERE / 'data'))
    monkeypatch.setitem(sys.modules, 'ops_config', config)
    backup_spec = importlib.util.spec_from_file_location('persistence_backup_fixture', ui / 'backup.py')
    backup = importlib.util.module_from_spec(backup_spec)
    backup_spec.loader.exec_module(backup)
    first = backup.create_backup(tag='before', upload_to_drive=False)
    import gzip
    original_backup = Path(first['path']).read_bytes()
    ops.prepare(source, target, activate=True)
    new_source = tmp_path / 'new-container' / 'data'
    new_source.parent.mkdir()
    ops.prepare(new_source, target, activate=True, require_prepared=True)
    monkeypatch.setattr(config, 'HERE', new_source.parent)
    backup._last_backup_cache = None
    status = backup.get_backup_status()
    assert first['filename'] in {x['filename'] for x in status['local_copies']}
    assert (new_source / 'backups' / first['filename']).read_bytes() == original_backup
    second = backup.create_backup(tag='after', upload_to_drive=False)
    restored = tmp_path / 'restored.db'
    restored.write_bytes(gzip.decompress(Path(second['path']).read_bytes()))
    assert rows(restored) == [('preserve me',)]
    assert (target / 'backups' / second['filename']).is_file()


def test_entrypoint_blocks_unprepared_studio_then_persists_data_without_job_edits(tmp_path):
    app = tmp_path / "app"
    ui = app / "manhwa-recap-v1" / "review_ui"
    source = ui / "data"
    target = app / "data" / "studio-ops"
    source.mkdir(parents=True)
    (app / "data" / "projects" / "chapter-34").mkdir(parents=True)
    job = app / "data" / "projects" / "chapter-34" / "render_interrupted.json"
    job.write_text(json.dumps({"status": "interrupted", "id": "untouched"}))
    (app / "manhwa-recap-v1" / "hyperframes").mkdir()
    (app / "panel-split").mkdir()
    (app / "deploy").mkdir()
    (app / "deploy" / "persist_ops_data.py").write_bytes((DEPLOY / "persist_ops_data.py").read_bytes())
    with sqlite3.connect(source / "manhwa.db") as connection:
        connection.execute("CREATE TABLE records(value TEXT)")
        connection.execute("INSERT INTO records VALUES ('live before release')")
    (source / "backups").mkdir()
    (source / "backups" / "latest.db.gz").write_bytes(b"retained backup")
    original_job = job.read_bytes()
    script = tmp_path / "entrypoint.sh"
    script.write_text((DEPLOY / "entrypoint.sh").read_text().replace('/app', str(app)))
    executable = tmp_path / "bin"
    executable.mkdir()
    probe = tmp_path / "server-started"
    shim = executable / "python"
    shim.write_text('#!/bin/sh\nif [ "$1" = "-m" ]; then\n touch ' +
                    shlex.quote(str(probe)) + '\n exit 0\nfi\nexec ' +
                    shlex.quote(sys.executable) + ' "$@"\n')
    shim.chmod(0o700)
    env = {k: v for k, v in os.environ.items() if k not in {'MANHWA_DATA_DIR', 'DATABASE_URL'}}
    env['PATH'] = str(executable) + os.pathsep + env['PATH']
    result = subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert not probe.exists()
    assert not source.is_symlink()
    ops.prepare(source, target)
    result = subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert probe.exists()
    assert source.resolve() == target.resolve()
    assert rows(source / 'manhwa.db') == [('live before release',)]
    assert (source / 'backups' / 'latest.db.gz').read_bytes() == b'retained backup'
    assert job.read_bytes() == original_job
