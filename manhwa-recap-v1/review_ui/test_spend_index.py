"""The usage index reads only what was appended (2026-11-13: the 40 MB log was
re-parsed after every paid call) and reports spend per stage."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spend_index as si  # noqa: E402

DAY = lambda ts: "2026-11-13"


def _line(job, cost, stage=None, **k):
    e = {"ts": "2026-11-13T15:00:00+00:00", "job_id": job, "provider": "gemini", "est_cost_usd": cost, **k}
    if stage:
        e["stage"] = stage
    return json.dumps(e) + "\n"


def test_totals_and_incremental_read(tmp_path, monkeypatch):
    p = str(tmp_path / "calls.log.jsonl")
    open(p, "w").write(_line("a", 0.10, "describe", thought_tokens=500, project="s_1")
                       + _line("a", 0.05, "voice", project="s_1") + _line("b", 0.20))
    st = si.refresh(p, DAY)
    assert st["lines"] == 3 and round(st["by_job"]["a"], 2) == 0.15
    assert round(st["by_stage"][("2026-11", "describe")], 2) == 0.10
    assert round(st["by_stage"][("2026-11", "unlabelled")], 2) == 0.20
    assert st["thinking"][("2026-11", "describe")] == 500
    assert round(st["by_project"]["s_1"], 2) == 0.15
    assert round(st["by_job_day"][("a", "2026-11-13")], 2) == 0.15
    # nothing new: nothing is parsed
    monkeypatch.setattr(si.json, "loads", lambda *_: (_ for _ in ()).throw(AssertionError("re-read")))
    assert si.refresh(p, DAY)["lines"] == 3
    monkeypatch.undo()
    # appended lines only; a half-written last line waits for its newline
    open(p, "a").write(_line("a", 0.30, "script") + '{"ts": "2026-11-13T15:0')
    st = si.refresh(p, DAY)
    assert st["lines"] == 4 and round(st["by_job"]["a"], 2) == 0.45
    open(p, "a").write('0:00+00:00", "job_id": "c", "est_cost_usd": 1.0}\n')
    assert round(si.refresh(p, DAY)["by_job"]["c"], 2) == 1.0


def test_a_replaced_log_is_read_again(tmp_path):
    p = str(tmp_path / "calls.log.jsonl")
    open(p, "w").write(_line("a", 0.10) + _line("a", 0.10))
    assert si.refresh(p, DAY)["lines"] == 2
    os.remove(p)
    open(p, "w").write(_line("z", 0.50))
    st = si.refresh(p, DAY)
    assert st["lines"] == 1 and "a" not in st["by_job"] and st["by_job"]["z"] == 0.5


def test_tail_reads_the_end_only(tmp_path):
    p = str(tmp_path / "calls.log.jsonl")
    with open(p, "w") as f:
        for i in range(5000):
            f.write(_line("j%d" % i, 0.001))
    got = si.tail(p, 3, block=512)
    assert [e["job_id"] for e in got] == ["j4997", "j4998", "j4999"]
    assert len(si.tail(p, 100000)) == 5000
    assert si.tail(str(tmp_path / "missing"), 5) == []
