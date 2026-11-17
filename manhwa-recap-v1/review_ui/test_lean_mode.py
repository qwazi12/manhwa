"""Lean mode (experiment, 2026-11-17): one chapter, when its ingest asks for
it, writes its script with less thinking and describes several panels per
request. Off by default: every other chapter behaves exactly as before. No
network: every model call is mocked."""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.dirname(HERE)
ROOT = os.path.dirname(RECAP)
for p in (HERE, RECAP, os.path.join(ROOT, "panel-describe")):
    if p not in sys.path:
        sys.path.insert(0, p)
import usage  # noqa: E402


@pytest.fixture(autouse=True)
def _lean_off():
    usage.set_lean(False)
    yield
    usage.set_lean(False)


def test_script_thinking_is_lowered_only_for_a_lean_chapter(monkeypatch):
    for name in ("SCRIPT_THINKING_LEVEL", "VISION_THINKING_LEVEL", "LEAN_SCRIPT_THINKING_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    assert usage.thinking_level("script", "gemini-3.8-flash") == ""      # default: the model's own
    usage.set_lean(True)
    assert usage.thinking_level("script", "gemini-3.8-flash") == "low"
    assert usage.thinking_level("vision", "gemini-3.8-flash") == "low"   # image calls unchanged
    monkeypatch.setenv("LEAN_SCRIPT_THINKING_LEVEL", "medium")
    assert usage.thinking_level("script", "gemini-3.8-flash") == "medium"
    usage.set_lean(False)
    assert usage.thinking_level("script", "gemini-3.8-flash") == ""


def test_lean_rides_into_a_pool_and_does_not_leak():
    usage.set_lean(True)
    with ThreadPoolExecutor(1) as ex:
        assert ex.submit(usage.carry(usage.lean)).result() is True
        assert ex.submit(usage.lean).result() is False          # the worker itself stays normal
    usage.set_lean(False)
    with ThreadPoolExecutor(1) as ex:
        assert ex.submit(usage.carry(usage.lean)).result() is False


class _Resp:
    def __init__(self, text):
        self._b = json.dumps({"steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
                              "usage": {"total_input_tokens": 10, "total_output_tokens": 2}}).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _panels(n):
    from PIL import Image
    d = tempfile.mkdtemp(prefix="lean_")
    paths = []
    for i in range(1, n + 1):
        p = os.path.join(d, f"page001_panel_{i:03d}.png")
        Image.new("RGB", (300, 400), "white").save(p)
        paths.append(p)
    return paths


def _answer(i):
    return {"panel": i, "ocr_text": f"line {i}", "visual_description": f"Running, hero {i}",
            "lines": [{"text": f"line {i}", "type": "speech", "speaker": None,
                       "speaker_basis": "guess", "lang": "en"}]}


@pytest.fixture
def model(monkeypatch):
    """Every request the describe code sends, and a settable answer for each."""
    import urllib.request
    import describe
    d = tempfile.mkdtemp()
    monkeypatch.setattr(usage, "USAGE_DIR", d)
    monkeypatch.setattr(usage, "LOG_PATH", os.path.join(d, "calls.log.jsonl"))
    for name in ("COUNTS_PATH", "LOCK_PATH"):
        if hasattr(usage, name):
            monkeypatch.setattr(usage, name, os.path.join(d, os.path.basename(getattr(usage, name))))
    sent, answers = [], []

    def fake_open(req, timeout=None, context=None):
        body = json.loads(req.data)
        sent.append(body)
        n = sum(1 for part in body["input"][0]["content"] if part["type"] == "image")
        return _Resp(answers.pop(0) if answers else
                     json.dumps([_answer(i) for i in range(1, n + 1)] if n > 1 else _answer(1)))

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(describe.time, "sleep", lambda s: None)
    return describe, sent, answers


def test_one_request_describes_several_panels_in_order(model):
    describe, sent, _ = model
    paths = _panels(3)
    recs = describe.describe_panels(paths, "AQ.k", "m-batch")
    assert len(sent) == 1                                        # one request for three panels
    parts = sent[0]["input"][0]["content"]
    assert [p["type"] for p in parts] == ["image", "image", "image", "text"]
    assert parts[3]["text"].startswith(describe.BATCH_HEAD.format(n=3))
    assert describe.VISION_PROMPT in parts[3]["text"]            # the per-panel instructions are unchanged
    assert [r["panel_id"] for r in recs] == ["page001_panel_001", "page001_panel_002", "page001_panel_003"]
    assert [r["ocr_text"] for r in recs] == ["line 1", "line 2", "line 3"]
    assert all(r["ok"] and r["source"] == "gemini" and r["width"] == 300 for r in recs)


@pytest.mark.parametrize("bad", [
    json.dumps([_answer(1), _answer(2)]),                        # one panel missing
    json.dumps([_answer(2), _answer(1), _answer(3)]),            # out of order
    "not json",
])
def test_an_unexpected_answer_falls_back_to_one_panel_per_request(model, bad):
    describe, sent, answers = model
    answers.append(bad)
    recs = describe.describe_panels(_panels(3), "AQ.k", "m-batch")
    assert len(sent) == 4                                        # the shared request, then one each
    assert [sum(1 for p in b["input"][0]["content"] if p["type"] == "image") for b in sent] == [3, 1, 1, 1]
    assert all(r["ok"] for r in recs) and [r["ocr_text"] for r in recs] == ["line 1"] * 3


def test_a_single_panel_or_an_old_key_uses_the_normal_call(model, monkeypatch):
    describe, sent, _ = model
    recs = describe.describe_panels(_panels(1), "AQ.k", "m-batch")
    assert len(sent) == 1 and recs[0]["ok"]
    assert sent[0]["input"][0]["content"][-1]["text"] == describe.VISION_PROMPT
    with pytest.raises(ValueError):
        describe.describe_batch_with_gemini(_panels(2), "AIzaLegacy", "m-batch")


def test_runner_groups_panels_only_when_asked(model, monkeypatch, tmp_path):
    """run.py: DESCRIBE_BATCH unset = one request per panel; 4 = groups of four,
    with a sliver still skipped without a request."""
    describe, sent, _ = model
    from PIL import Image
    import run as runner
    crops = tmp_path / "crops"
    crops.mkdir()
    for i in range(1, 10):
        Image.new("RGB", (300, 400), "white").save(crops / f"page001_panel_{i:03d}.png")
    Image.new("RGB", (300, 20), "white").save(crops / "page001_panel_010.png")     # sliver
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.k")
    monkeypatch.setenv("DESCRIBE_WORKERS", "2")

    def go(out):
        monkeypatch.setattr(sys, "argv", ["run.py", "--input", str(crops), "--out", str(out), "--model", "m-batch"])
        sent.clear()
        runner.main()
        return json.load(open(out))

    monkeypatch.delenv("DESCRIBE_BATCH", raising=False)
    recs = go(tmp_path / "single.json")
    assert len(sent) == 9 and len(recs) == 10
    monkeypatch.setenv("DESCRIBE_BATCH", "4")
    recs = go(tmp_path / "grouped.json")
    assert sorted(sum(1 for p in b["input"][0]["content"] if p["type"] == "image") for b in sent) == [1, 4, 4]
    assert [r["panel_id"] for r in recs] == [f"page001_panel_{i:03d}" for i in range(1, 11)]
    assert all(r["ok"] for r in recs) and recs[9]["source"] == "size-filter"
    assert all(r["generation"]["inputs"]["source_sha256"] for r in recs)


def test_lean_is_recorded_on_the_job_and_reaches_the_pipeline(monkeypatch):
    import server
    got = {}

    def fake_run(url, progress, job_id=None, fresh=False, engine="gemini", variant="", direct=None,
                 tier="", lean=False, keep_source=False):
        got[job_id] = lean
        return {"id": None}
    monkeypatch.setattr(server, "_persist_ingest", lambda j: None)
    monkeypatch.setattr(server, "_load_ingest", lambda j: None)
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(server, "_after_ingest", lambda j: None)
    monkeypatch.setattr(server, "_queue_wake", lambda: None)
    import ingest
    monkeypatch.setattr(ingest, "run_ingest", fake_run)
    server._QUEUE.clear()
    try:
        a = server._enqueue_ingest("https://x/ch/1")
        b = server._enqueue_ingest("https://x/ch/2", lean=True)
        assert "lean" not in server.INGEST[a] and server.INGEST[b]["lean"] is True
        for j in (a, b):
            server._run_ingest_job(j, server.INGEST[j]["url"])
        assert got == {a: False, b: True}
    finally:
        server._QUEUE.clear()
        for j in list(got):
            server.INGEST.pop(j, None)
