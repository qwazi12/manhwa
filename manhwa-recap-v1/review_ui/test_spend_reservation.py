"""Spending guard (2026-11-13/14).
  * two calls running together could both pass a cap only one fitted under;
  * a call that raised after the provider answered was never recorded;
  * "no answer" was booked as "no money spent"; holds of dead processes were
    dropped; a failed ledger write lost the line.
Confirmed, estimated, uncertain and held money are kept apart, and all four
count against the cap. Local fakes only — no paid calls."""
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


class HttpError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


def test_provider_error_answer_is_not_billed(books):
    """HTTP 4xx/5xx is an answer: Gemini and Anthropic do not bill it."""
    for code in (400, 429, 503):
        with pytest.raises(HttpError):
            with usage.gate("gemini", 1, model="m"):
                raise HttpError(code)
    d = _counts()
    assert d["gemini_calls"] == 0 and d["est_cost_usd"] == 0 and d["uncertain_usd"] == 0 and d["reserved"] == {}
    assert _log() == []


def test_no_answer_is_uncertain_not_free(books):
    """A timeout or dropped connection may have been processed and billed."""
    for err in (TimeoutError("timed out"), ConnectionResetError("reset")):
        with pytest.raises(type(err)):
            with usage.gate("gemini", 1, model="m"):
                raise err
        break
    d = _counts()
    assert d["uncertain_usd"] == pytest.approx(0.60) and d["est_cost_usd"] == 0 and d["reserved"] == {}
    assert d["gemini_calls"] == 0                       # not counted as a completed call
    rec = _log()[-1]
    assert rec["outcome"] == "uncertain" and rec["uncertain_usd"] == pytest.approx(0.60) and rec["est_cost_usd"] == 0
    # it counts against the cap: $0.60 uncertain + $0.60 > $1
    with pytest.raises(usage.UsageCapExceeded) as e:
        with usage.gate("gemini", 1, model="m"):
            pass
    assert "ended without an answer" in str(e.value)
    assert usage.headroom() == pytest.approx(0.40) and usage.daily_summary()["committed_usd"] == pytest.approx(0.60)


def test_caller_can_say_nothing_was_sent(books):
    with pytest.raises(ValueError):
        with usage.gate("gemini", 1, model="m") as m:
            m.not_sent()
            raise ValueError("could not build the request")
    assert _counts()["uncertain_usd"] == 0 and _log() == []


def test_timeouts_cannot_be_retried_without_end(books, monkeypatch):
    monkeypatch.setattr(usage, "daily_cap", lambda: 1000.0)
    monkeypatch.setattr(usage, "_est_cost", lambda kind, units, model="": 0.01)
    made = 0
    with pytest.raises(usage.UsageCapExceeded) as e:
        for _ in range(100):
            try:
                with usage.gate("gemini", 1, model="m"):
                    made += 1
                    raise TimeoutError("timed out")
            except TimeoutError:
                pass
    assert made == usage.UNCERTAIN_STREAK and "in a row" in str(e.value)
    assert _counts()["uncertain_usd"] == pytest.approx(0.01 * usage.UNCERTAIN_STREAK)
    usage.set_job("another-job")                        # other work is not held up
    with usage.gate("gemini", 1, model="m"):
        pass
    usage.set_job("job-r")
    monkeypatch.setattr(usage, "UNCERTAIN_COOLDOWN_S", 0)   # after the cooldown the job may try again
    with usage.gate("gemini", 1, model="m"):
        pass
    assert _counts()["jobs"]["job-r"]["uncertain_streak"] == 0      # a good call ends the streak


def test_hold_of_a_dead_process_becomes_uncertain_spend(books):
    with usage._flock():
        d = usage._load_counts()
        d["reserved"]["dead"] = {"usd": 0.3, "key": "gemini_calls", "units": 1, "job": "x", "kind": "gemini",
                                 "model": "m", "at": time.time() - usage.RESERVE_TTL_S - 1}
        usage._save_counts(d)
    with pytest.raises(usage.UsageCapExceeded):          # 0.30 uncertain + 0.60 + … still over $1 with another
        with usage.gate("gemini", 1, model="m"):
            with usage.gate("gemini", 1, model="m"):
                pass
    d = _counts()
    assert "dead" not in d["reserved"] and d["uncertain_usd"] == pytest.approx(0.30)
    lines = [x for x in _log() if x.get("rid") == "dead"]
    assert len(lines) == 1 and lines[0]["outcome"] == "uncertain" and "gone" in lines[0]["why"]


def test_restart_turns_every_hold_into_uncertain_spend(books):
    with usage._flock():
        d = usage._load_counts()
        for i in range(3):
            d["reserved"][f"r{i}"] = {"usd": 0.1, "key": "gemini_calls", "units": 1, "job": "x", "at": time.time()}
        usage._save_counts(d)
    assert usage.recover() == {"calls": 3, "uncertain_usd": pytest.approx(0.3)}
    d = _counts()
    assert d["reserved"] == {} and d["uncertain_usd"] == pytest.approx(0.3) and len(_log()) == 3
    assert usage.recover() == {"calls": 0, "uncertain_usd": 0}          # running it again changes nothing


def test_crash_mid_call_leaves_the_hold_then_recovery_books_it(books):
    """The process dies inside the call: no settle runs at all."""
    g = usage.gate("gemini", 1, model="m")
    g.__enter__()                                        # …and the process is killed here
    assert _counts()["reserved"] and usage.reserved_usd() == pytest.approx(0.60)
    usage.recover()                                      # next start
    d = _counts()
    assert d["reserved"] == {} and d["uncertain_usd"] == pytest.approx(0.60)


def test_midnight_rollover_keeps_holds_learned_sizes_and_unwritten_lines(books):
    with usage._flock():
        d = usage._load_counts()
        d.update(date="2000-01-01", est_cost_usd=2.0, confirmed_usd=1.5, estimated_usd=0.5, uncertain_usd=0.25)
        d["reserved"]["live"] = {"usd": 0.9, "key": "gemini_calls", "units": 1, "job": "x", "at": time.time()}
        d["observed"]["gemini|m|describe"] = [0.02]
        d["pending_ledger"] = [{"rid": "late", "est_cost_usd": 0.1}]
        usage._save_counts(d)
    with pytest.raises(usage.UsageCapExceeded):          # yesterday's spend is gone, the live hold is not
        with usage.gate("gemini", 1, model="m"):
            pass
    d = _counts()
    assert d["est_cost_usd"] == 0 and d["uncertain_usd"] == 0 and "live" in d["reserved"]
    assert d["observed"] == {"gemini|m|describe": [0.02]}
    life = d["lifetime"]
    assert life["est_cost_usd"] == pytest.approx(2.0) and life["uncertain_usd"] == pytest.approx(0.25)
    assert life["confirmed_usd"] == pytest.approx(1.5)
    assert [x["rid"] for x in _log()] == ["late"]       # the waiting line was written


def test_actual_cost_above_the_estimate_raises_the_next_hold(books, monkeypatch):
    monkeypatch.setattr(usage, "_est_cost", lambda kind, units, model="": 0.005)
    monkeypatch.setattr(usage, "token_cost", lambda *a, **k: 0.102)      # the dearest call seen in production
    monkeypatch.setattr(usage, "daily_cap", lambda: 10.0)
    with usage.stage("describe"):
        assert usage.reservation("gemini", 1, "m") == pytest.approx(0.005)
        with usage.gate("gemini", 1, model="m") as m:
            m.tokens(16000, 24000)
        assert _log()[-1]["held_usd"] == pytest.approx(0.005) and _log()[-1]["est_cost_usd"] == pytest.approx(0.102)
        assert usage.reservation("gemini", 1, "m") == pytest.approx(0.102)   # learned for this stage…
        with usage.gate("gemini", 1, model="m") as m:
            assert usage.reserved_usd() == pytest.approx(0.102)
            m.tokens(10, 10)
    with usage.stage("crops"):
        assert usage.reservation("gemini", 1, "m") == pytest.approx(0.005)   # …not for another stage


def test_unmetered_success_is_booked_as_estimated(books):
    with usage.gate("gemini", 1, model="m"):
        pass
    d = _counts()
    assert d["estimated_usd"] == pytest.approx(0.60) and d["confirmed_usd"] == 0 and d["est_cost_usd"] == pytest.approx(0.60)


def test_ledger_write_failure_loses_nothing(books, monkeypatch):
    monkeypatch.setattr(usage, "token_cost", lambda *a, **k: 0.10)
    real = usage._append_log
    broken = {"on": True}

    def append(entry):
        if broken["on"]:
            raise OSError("disk full")
        real(entry)
    monkeypatch.setattr(usage, "_append_log", append)
    with usage.gate("gemini", 1, model="m") as m:        # the call itself must not fail
        m.tokens(10, 10)
    d = _counts()
    assert d["est_cost_usd"] == pytest.approx(0.10) and len(d["pending_ledger"]) == 1 and _log() == []
    broken["on"] = False
    with usage.gate("gemini", 1, model="m") as m:
        m.tokens(10, 10)
    assert len(_log()) == 2 and _counts()["pending_ledger"] == []
    assert len({x["rid"] for x in _log()}) == 2


def test_crash_between_saving_money_and_writing_the_ledger(books, monkeypatch):
    """Process dies after counters.json is saved and after the line was
    appended, before the 'written' mark is saved: no loss, no double line."""
    monkeypatch.setattr(usage, "token_cost", lambda *a, **k: 0.10)
    with usage.gate("gemini", 1, model="m") as m:
        m.tokens(10, 10)
    line = _log()[-1]
    with usage._flock():                                 # put the line back as "still waiting"
        d = usage._load_counts()
        d["pending_ledger"] = [dict(line, _retry=True)]
        usage._save_counts(d)
    with usage.gate("gemini", 1, model="m") as m:
        m.tokens(10, 10)
    assert [x["rid"] for x in _log()].count(line["rid"]) == 1 and _counts()["pending_ledger"] == []


def test_counters_that_cannot_be_saved_stop_paid_calls(books, monkeypatch):
    def fail(d):
        raise OSError("read-only file system")
    monkeypatch.setattr(usage, "_save_counts", fail)
    ran = []
    with pytest.raises(usage.UsageCapExceeded) as e:
        with usage.gate("gemini", 1, model="m"):
            ran.append(1)
    assert not ran and "cannot be written" in str(e.value)


def test_settle_failure_keeps_the_hold_against_the_cap(books, monkeypatch):
    real = usage._save_counts
    n = {"saves": 0}

    def flaky(d):
        n["saves"] += 1
        if n["saves"] == 2:                              # the settle's save
            raise OSError("disk full")
        real(d)
    monkeypatch.setattr(usage, "_save_counts", flaky)
    with usage.gate("gemini", 1, model="m"):             # the call still succeeds
        pass
    d = _counts()
    assert len(d["reserved"]) == 1                       # its hold is still there…
    with pytest.raises(usage.UsageCapExceeded):          # …and still counts
        with usage.gate("gemini", 1, model="m"):
            pass


def test_concurrent_workers_with_mixed_outcomes_stay_under_the_cap(books, monkeypatch):
    """24 workers, $0.10 holds, $1 cap: successes, timeouts and provider
    errors interleaved. Committed money (spent + uncertain + held) never
    passes the cap and every dollar is in exactly one bucket."""
    monkeypatch.setattr(usage, "_est_cost", lambda kind, units, model="": 0.10)
    monkeypatch.setattr(usage, "token_cost", lambda *a, **k: 0.10)
    monkeypatch.setattr(usage, "UNCERTAIN_STREAK", 10 ** 6)
    peak, lock, start = [0.0], threading.Lock(), threading.Barrier(24)

    def worker(i):
        start.wait(5)
        usage.set_job(f"w{i % 4}")
        for k in range(3):
            try:
                with usage.gate("gemini", 1, model="m") as m:
                    with lock:
                        peak[0] = max(peak[0], usage.committed_usd())
                    time.sleep(0.002)
                    if (i + k) % 3 == 0:
                        raise TimeoutError("timed out")
                    if (i + k) % 3 == 1:
                        raise HttpError(503)
                    m.tokens(10, 10)
            except (usage.UsageCapExceeded, TimeoutError, HttpError):
                pass

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    d = _counts()
    assert peak[0] <= 1.0 + 1e-9
    assert d["reserved"] == {}
    assert d["est_cost_usd"] + d["uncertain_usd"] <= 1.0 + 1e-9
    assert d["est_cost_usd"] == pytest.approx(d["confirmed_usd"] + d["estimated_usd"])
    log = _log()
    assert sum(x["est_cost_usd"] for x in log) == pytest.approx(d["est_cost_usd"])
    assert sum(x.get("uncertain_usd", 0) for x in log) == pytest.approx(d["uncertain_usd"])
    assert len({x["rid"] for x in log}) == len(log)
