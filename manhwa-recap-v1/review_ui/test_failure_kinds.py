"""Autopilot tells an account failure and a deterministic failure from a
passing one (2026-11-13). Before: ten chapters failed on HTTP 402 (the Gemini
credits had run out), each was retried and ten series ended up blocked; Death
Knight 95 failed twice on the same "cache is stale" text."""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
import autopilot as ap  # noqa: E402

E402 = ("describe el_006.png FAILED (HTTPError: HTTP Error 402: Payment Required) text: ''")
STALE = ("Description cache is stale or unknown; existing work preserved. Review "
         "descriptions.json.cache.json, then explicitly rerun describe")
FLAKY = "Gemini Interactions API 504 (model=gemini-3.8-flash): Deadline expired"


def _root(n=1):
    root = tempfile.mkdtemp()
    for i in range(n):
        ap.record_queued(root, f"asura:s{i}|{i + 1}", series_id=f"s{i}", chapter=str(i + 1),
                         project=f"s{i}_{i + 1}", job=f"job{i}", source="autopilot", url="u")
    return root


def test_error_kinds():
    assert ap.classify_error(E402) == "account"
    assert ap.classify_error("prepayment credits are depleted") == "account"
    assert ap.classify_error(STALE) == "deterministic"
    assert ap.classify_error("voice cache is edited; existing work preserved.") == "deterministic"
    assert ap.classify_error(FLAKY) == "transient"
    assert ap.classify_error(None) == "transient"


def test_account_failure_is_no_strike_and_holds_every_start():
    root = _root(2)
    e = ap.on_job_end(root, "job0", "error", E402)
    assert e["fails"] == 0 and e["fail_kind"] == "account"
    st = ap.load(root)
    assert ap.hold_left(st) > 0
    st["settings"]["enabled"] = True
    reason, pick = ap.decide(st, [], queue_busy=False, spent=0, cap=10)
    assert pick is None and "refused the account" in reason
    # the hold runs out by itself; nothing was struck against either chapter
    reason, _ = ap.decide(st, [], queue_busy=False, spent=0, cap=10, now=time.time() + ap.ACCOUNT_HOLD + 1)
    assert "refused the account" not in reason
    assert all(x.get("fails", 0) == 0 for x in st["ledger"].values())


def test_a_finished_chapter_clears_the_hold():
    root = _root(2)
    ap.on_job_end(root, "job0", "error", E402)
    ap.on_job_end(root, "job1", "done", cost=0.5)
    assert ap.hold_left(ap.load(root)) == 0


def test_deterministic_failure_blocks_at_once():
    root = _root()
    e = ap.on_job_end(root, "job0", "error", STALE)
    assert e["fails"] >= ap.FAIL_LIMIT and e["fail_kind"] == "deterministic"
    assert not ap.load(root).get("hold")


def test_transient_failure_gets_one_more_try():
    root = _root()
    assert ap.on_job_end(root, "job0", "error", FLAKY)["fails"] == 1
    assert ap.on_job_end(root, "job0", "error", FLAKY)["fails"] == ap.FAIL_LIMIT


def test_sources_are_read_while_autopilot_is_off():
    root = tempfile.mkdtemp()
    calls = []
    ap.runtime["last_refresh"] = 0.0
    deps = {"view": lambda: [], "refresh": lambda: calls.append(1), "canon": lambda k: k,
            "live_projects": lambda: set(), "queue_busy": lambda: False, "spend": lambda: (0.0, 10.0)}
    assert ap.load(root)["settings"].get("enabled") in (False, None)
    assert ap.tick(root, deps) is None
    assert calls == [1] and ap.runtime["last_result"] == "off"
    ap.tick(root, deps)                       # not again within REFRESH_EVERY
    assert calls == [1]
