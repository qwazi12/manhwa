"""Read-only chapter/history regressions using synthetic files after a restart.

Run in the offline snapshot: no lifespan/startup workers and no provider calls.
"""
import copy
import json
from pathlib import Path

import pytest

URL = 'https://asurascans.com/comics/fixture-series-1234abcd/chapter/34'


@pytest.fixture
def studio(tmp_path, monkeypatch):
    import ingest
    import server
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ingest, 'PROJECTS', str(tmp_path))
    jobs = tmp_path / '_jobs'
    jobs.mkdir()
    monkeypatch.setattr(server, '_JOBS_DIR', str(jobs))
    monkeypatch.setattr(server, 'INGEST', {})
    monkeypatch.setattr(server, 'JOBS', {})
    # Client without a context manager does not invoke lifespan/startup recovery.
    client = TestClient(server.app)
    return server, ingest, client, tmp_path, jobs


def put(root, jid, **data):
    (root / (jid + '.json')).write_text(json.dumps(data, sort_keys=True))


def partial(studio, jid='stopped', variant='test-version', ts=10, **extra):
    srv, ing, _, root, jobs = studio
    pid = ing.project_id(URL, variant)
    folder = root / pid
    folder.mkdir(exist_ok=True)
    (folder / 'pages').mkdir(exist_ok=True)
    (folder / 'pages/001.jpg').write_bytes(b'preserved source fixture')
    (folder / 'descriptions.json').write_text('[{"id":"panel-1","description":"source fact"}]')
    put(jobs, jid, status='cancelled', control='stop', url=URL, variant=variant,
        stage='describe', ts=ts, error='stopped by user', **extra)
    return pid, folder


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def test_cancelled_partial_chapter_survives_restart_without_restarting_work(studio, monkeypatch):
    srv, _, client, root, jobs = studio
    pid, folder = partial(studio)
    for n, kind in enumerate(['finalize', 'finalize', 'publish', 'finalize']):
        # Four historical Waiting records plus their preexisting shadow errors.
        put(jobs, 'render_history' + str(n), type=kind, status='interrupted',
            project=pid, ts=100 + n)
        put(jobs, 'history' + str(n), status='error', resumes=1, ts=100 + n,
            error='no chapter link')
    for name in ['_enqueue_ingest', '_persist_ingest', '_hold', '_resume_interrupted']:
        monkeypatch.setattr(srv, name, lambda *a, **k: pytest.fail('GET must not start or rewrite work'))
    before = snapshot(root)
    for _ in range(2):  # Fresh memory before each read simulates repeated restarts.
        srv.INGEST.clear()
        response = client.get(f'/api/chapter/{pid}')
        assert response.status_code == 200, response.text
        chapter = response.json()
        assert chapter['id'] == pid
        assert chapter['chapter'] == '34'
        assert chapter['url'] == URL
        assert chapter['status']['reason'] == 'stopped by you'
        assert chapter['ingest']['status'] == 'cancelled'
        assert chapter['ingest']['job'] == 'stopped'
        assert chapter['busy'] is None
        assert chapter['videos'] == []
        assert chapter['can_rerun'] is False  # No fabricated completion metadata.
        assert not (folder / 'project.json').exists()
        steps = {s['key']: s for s in chapter['steps']}
        assert steps['scrape']['state'] == 'done'
        assert steps['describe']['state'] == 'done'
        assert steps['voice']['state'] == 'missing'
        assert steps['export']['state'] == 'missing'
        assert client.get('/api/jobsbar?failed=1').json()['jobs'] == []
        assert any(c['id'] == pid for c in client.get('/api/chapters').json()['chapters'])
        assert srv.INGEST == {}
        assert srv.JOBS == {}
    assert snapshot(root) == before


def test_variant_and_original_keep_separate_identity_and_latest_ingest(studio):
    srv, ing, client, root, jobs = studio
    original = ing.project_id(URL)
    folder = root / original
    folder.mkdir()
    (folder / 'project.json').write_text(json.dumps({'id': original, 'url': URL,
        'series': 'Fixture Series', 'chapter': '34'}))
    (folder / 'segments.json').write_text('[]')
    variant, _ = partial(studio, jid='older', ts=1)
    partial(studio, jid='latest', ts=20)
    rows = {c['id']: c for c in client.get('/api/chapters').json()['chapters']}
    assert rows[original]['status']['key'] == 'to_review'
    assert rows[variant]['status']['reason'] == 'stopped by you'
    assert rows[variant]['job'] == 'latest'
    assert len([c for c in rows if c == variant]) == 1
    assert client.get(f'/api/chapter/{original}').status_code == 200
    assert srv.INGEST == {}


def test_live_record_overrides_its_stale_disk_copy(studio):
    srv, _, _, _, _ = studio
    pid, _ = partial(studio)
    live = {'status': 'paused', 'control': 'pause', 'url': URL,
            'variant': 'test-version', 'ts': 10, 'msg': 'live state wins'}
    srv.INGEST['stopped'] = live
    before = copy.deepcopy(live)
    by_project, by_url = srv._ingest_by_project()
    assert by_project[pid]['status'] == 'paused'
    assert by_url[URL]['msg'] == 'live state wins'
    assert live == before
    assert 'job' not in live


@pytest.mark.parametrize('status', ['cancelled', 'error', 'done', 'paused'])
def test_missing_project_is_not_resurrected_from_disk_history(studio, status):
    srv, _, client, root, jobs = studio
    put(jobs, 'gone', status=status, url=URL, variant='deleted-version', ts=10)
    before = snapshot(root)
    assert srv._ingest_by_project() == ({}, {})
    assert client.get('/api/chapters').json()['chapters'] == []
    assert snapshot(root) == before


def test_live_queued_ingest_without_folder_is_still_listed(studio):
    srv, ing, client, _, _ = studio
    srv.INGEST['live'] = {'status': 'queued', 'url': URL, 'ts': 20}
    rows = client.get('/api/chapters').json()['chapters']
    assert rows[0]['id'] == ing.project_id(URL)
    assert rows[0]['status']['key'] == 'making'


def test_other_worker_records_and_broken_json_do_not_become_chapters(studio):
    srv, _, client, root, jobs = studio
    pid, _ = partial(studio)
    put(jobs, 'render_old', status='error', url=URL, variant='test-version', ts=99)
    put(jobs, 'typed', type='research', status='error', project=pid, url=URL, ts=100)
    (jobs / 'broken.json').write_text('{broken')
    before = snapshot(root)
    assert srv._ingest_by_project()[0][pid]['job'] == 'stopped'
    assert client.get(f'/api/chapter/{pid}').status_code == 200
    assert snapshot(root) == before
