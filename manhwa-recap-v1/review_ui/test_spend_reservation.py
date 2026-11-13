"""Spending guard, review findings 2026-11-13:
  * two calls running together could both pass a cap only one fitted under
    (the cap was compared with money already spent, not money in flight);
  * a call that raised after the provider answered was never recorded."""
import json
import os
import sys
import tempfile
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import usage  # noqa: E402


@pytest.fixture
def books(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(usage, "USAGE_DIR", d)
    monkeypatch.setattr(usage, "LOG_PATH", os.path.join(d, "calls.log.jsonl"))
    monkeypatch.setattr(usage, "COUNTS_PATH", os.path.join(d, "counters.json"))
    monkeypatch.setattr(usage, "LOCK_PATH", os.path.join(d, ".lock"))
    monkeypatch.setattr(usage, "daily_cap", lambda: 1.0)
    monkeypatch.setattr(usage, "_est_cost", lambda kind, units, model="": 0.60)
    usage.set_job("job-r")
    return d


def _counts():
    return json.load(open(usage.COUNTS_PATH))


def _log():
    try:
        return [json.loads(x) for x in open(usage.LOG_PATH).read().splitlines()]
    except FileNotFoundError:
        return []


def test_second_overlapping_call_is_refused(books):
    inside, release, out = threading.Event(), threading.Event(), {}

    def first():
        with usage.gate("gemini", 1, model="m"):
            inside.set()
            release.wait(5)

    t = threading.Thread(target=first)
    t.start()
    assert inside.wait(5)
    try:
        with pytest.raises(usage.UsageCapExceeded) as e:
            with usage.gate("gemini", 1, model="m"):
                out["ran"] = True
        assert "held for calls running now" in str(e.value)
    finally:
        release.set()
        t.join()
    assert "ran" not in out
    d = _counts()
    assert d["est_cost_usd"] == pytest.approx(0.60) and d["reserved"] == {}


def test_many_threads_never_pass_the_cap(books, monkeypatch):
    monkeypatch.setattr(usage, "_est_cost", lambda kind, units, model="": 0.30)
    ok, refused, gate_open = [], [], threading.Barrier(8)

    def call():
        gate_open.wait(5)
        try:
            with usage.gate("gemini", 1, model="m"):
                time.sleep(0.05)
            ok.append(1)
        except usage.UsageCapExceeded:
            refused.append(1)

    ts = [threading.Thread(target=call) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 3 and len(refused) == 5          # 3 x $0.30 fit under $1
    assert _counts()["est_cost_usd"] == pytest.approx(0.90)


def test_hold_is_replaced_by_the_real_cost(books, monkeypatch):
    monkeypatch.setattr(usage, "token_cost", lambda *a, **k: 0.10)
    with usage.gate("gemini", 1, model="m") as m:
        assert usage.reserved_usd() == pytest.approx(0.60)
        m.tokens(100, 10)
    d = _counts()
    assert d["reserved"] == {} and d["est_cost_usd"] == pytest.approx(0.10)


def test_failure_after_the_answer_is_recorded_and_charged(books, monkeypatch):
    monkeypatch.setattr(usage, "token_cost", lambda *a, **k: 0.25)
    with pytest.raises(ValueError):
        with usage.gate("gemini", 1, model="m") as m:
            m.tokens(1000, 200)
            raise ValueError("bad JSON in the answer")
    d = _counts()
    assert d["gemini_calls"] == 1 and d["est_cost_usd"] == pytest.approx(0.25) and d["reserved"] == {}
    rec = _log()[-1]
    assert rec["metered"] and rec["failed_after_response"].startswith("ValueError")


def test_failure_after_usage_was_parsed_but_not_reported(books, monkeypatch):
    monkeypatch.setattr(usage, "token_cost", lambda *a, **k: 0.25)
    with pytest.raises(KeyError):
        with usage.gate("gemini", 1, model="m"):
            usage.parse_gemini_usage({"usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 50}})
            raise KeyError("candidates")
    rec = _log()[-1]
    assert rec["prompt_tokens"] == 900 and rec["est_cost_usd"] == pytest.approx(0.25)


def test_failure_with_no_answer_is_released_without_a_charge(books):
    with pytest.raises(ConnectionError):
        with usage.gate("gemini", 1, model="m"):
            raise ConnectionError("503")
    d = _counts()
    assert d["gemini_calls"] == 0 and d["est_cost_usd"] == 0 and d["reserved"] == {}
    assert _log() == []
    with usage.gate("gemini", 1, model="m"):       # the hold did not linger
        pass


def test_hold_of_a_dead_process_expires(books, monkeypatch):
    with usage._flock():
        d = usage._load_counts()
        d["reserved"]["dead"] = {"usd": 0.9, "key": "gemini_calls", "units": 1, "job": "x",
                                 "at": time.time() - usage.RESERVE_TTL_S - 1}
        usage._save_counts(d)
    with usage.gate("gemini", 1, model="m"):
        pass
    assert "dead" not in _counts()["reserved"]


def test_holds_survive_the_day_rollover(books, monkeypatch):
    with usage._flock():
        d = usage._load_counts()
        d["date"] = "2000-01-01"
        d["reserved"]["live"] = {"usd": 0.9, "key": "gemini_calls", "units": 1, "job": "x", "at": time.time()}
        usage._save_counts(d)
    with pytest.raises(usage.UsageCapExceeded):
        with usage.gate("gemini", 1, model="m"):
            pass
