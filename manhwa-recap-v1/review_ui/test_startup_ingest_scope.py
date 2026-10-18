"""Exercise real recovery functions with private files and a fake enqueue boundary.

Loading only their AST avoids server import/startup and all provider/account
configuration. Function bodies come from server.py unchanged.
"""

import ast
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest


def recovery(tmp_path):
    names = {'_all_ingest_jobs', '_load_ingest', '_persist_ingest',
             '_waiting_ingests', '_resume_interrupted', '_line_count'}
    path = Path(__file__).with_name('server.py')
    nodes = [n for n in ast.parse(path.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    calls = []
    scope = {'os': os, 'json': json, 'time': time, '_JOBS_DIR': str(tmp_path),
             'INGEST': {}, 'LINE_MAX': 2, 'RESUME_MAX': 2,
             '_LINE_ST': ('queued', 'running', 'paused', 'pausing'),
             '_ingest_mod': SimpleNamespace(PROJECTS=str(tmp_path)),
             '_read_json': lambda path: {},
             '_autopilot': SimpleNamespace(set_status_for_job=lambda *args: None),
             '_hold': lambda *args: pytest.fail('unexpected line overflow'),
             '_enqueue_ingest': lambda *args, **kwargs: calls.append((args, kwargs))}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope, calls


def put(root, name, **data):
    (root / (name + '.json')).write_text(json.dumps(data, sort_keys=True))


def test_boot_does_not_rewrite_historical_render_publish_or_stopped_ingest(tmp_path):
    scope, calls = recovery(tmp_path)
    for n, kind in enumerate(['finalize', 'finalize', 'publish', 'finalize']):
        jid = 'historical_' + str(n)
        put(tmp_path, 'render_' + jid, job=jid, type=kind,
            status='interrupted', ts=n, project='completed_chapter')
        put(tmp_path, jid, job=jid, status='error', resumes=1,
            error='no chapter link', ts=n)
    put(tmp_path, 'stopped_ingest', job='stopped_ingest', status='cancelled', control='stop')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert scope['_resume_interrupted']() == 0
    assert not calls
    assert not scope['INGEST']
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('status', ['interrupted', 'held', 'budget_paused'])
def test_waiting_ingests_excludes_other_worker_types(tmp_path, status):
    scope, _ = recovery(tmp_path)
    put(tmp_path, 'render_old', job='old', type='finalize', status=status)
    put(tmp_path, 'real_ingest', job='real_ingest', status=status,
        url='https://example.test/chapter/1')
    assert [x['job'] for x in scope['_waiting_ingests'](status)] == ['real_ingest']


@pytest.mark.parametrize('control', ['run', 'pause'])
def test_real_interrupted_ingest_still_resumes_with_its_control(tmp_path, control):
    scope, calls = recovery(tmp_path)
    put(tmp_path, 'real_ingest', job='real_ingest', status='interrupted',
        control=control, url='https://example.test/chapter/1', resumes=0)
    assert scope['_resume_interrupted']() == 1
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == 'https://example.test/chapter/1'
    assert kwargs['job_id'] == 'real_ingest'
    assert kwargs['control'] == control
