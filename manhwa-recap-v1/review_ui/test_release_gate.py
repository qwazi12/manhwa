"""One verdict per video from the reports the studio already writes, and a
scorecard built from the same records (2026-11-13). No model calls."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_gate as rg  # noqa: E402
import scorecard as sc  # noqa: E402


def _w(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(value, f)


def _chapter(root, pid, critique=None, qa=None, validation=None, name="final.mp4"):
    p = os.path.join(root, pid)
    _w(os.path.join(p, "project.json"), {"id": pid})
    if critique is not None:
        _w(os.path.join(p, "critique.json"), critique)
    if validation is not None:
        _w(os.path.join(p, "validation.json"), validation)
    if qa is not None:
        _w(os.path.join(p, "exports", "_qa", name + ".qa.json"), qa)
    return p


HALLU = {"unit": 3, "type": "hallucination", "problem": "invents a name", "fix": "remove it"}
CLEAN = {"status": "done", "outcome": "clean", "issues": [HALLU], "revised": [{"unit": 3}],
         "unresolved": [], "verified": {"units": [3], "still_flagged": []}}
OPEN = {"status": "done", "outcome": "needs_review", "issues": [HALLU], "revised": [{"unit": 3}],
        "unresolved": [{**HALLU, "resolution": "still_flagged_after_rewrite"}]}
LEGACY = {"status": "done", "outcome": "needs_review", "issues": [HALLU], "revised": [{"unit": 3}],
          "unresolved": [{**HALLU, "resolution": "rewritten_unverified"}]}
PASSED, FAILED = {"status": "passed", "findings": []}, {
    "status": "failed", "findings": [{"rule": "clipping", "severity": "critical", "explanation": "3% clipped"}]}


def test_verdicts(tmp_path):
    root = str(tmp_path)
    assert rg.verdict(_chapter(root, "a_1", CLEAN, PASSED), "final.mp4")["release"] == "clear"
    v = rg.verdict(_chapter(root, "a_2", OPEN, PASSED), "final.mp4")
    assert v["release"] == "blocked" and v["critical"][0]["code"] == "script_unresolved"
    v = rg.verdict(_chapter(root, "a_3", LEGACY, PASSED), "final.mp4")
    assert v["release"] == "review" and v["review"][0]["code"] == "script_unverified"      # unknown, not condemned
    v = rg.verdict(_chapter(root, "a_4", CLEAN, FAILED), "final.mp4")
    assert v["release"] == "blocked" and v["critical"][0]["code"] == "media_failed"
    v = rg.verdict(_chapter(root, "a_5", {"status": "skipped: provider down", "outcome": "incomplete"}, PASSED), "final.mp4")
    assert v["critical"][0]["code"] == "script_unchecked"
    v = rg.verdict(_chapter(root, "a_6", CLEAN, PASSED, {"findings": [{"severity": "high", "issue": "order"}]}), "final.mp4")
    assert v["release"] == "review" and v["review"][0]["code"] == "validator_high"
    style = {"status": "done", "outcome": "needs_review", "issues": [], "revised": [],
             "unresolved": [{"unit": 1, "type": "style_violation", "problem": "x", "resolution": "not_repaired"}]}
    assert rg.verdict(_chapter(root, "a_7", style, PASSED), "final.mp4")["release"] == "review"


def test_nothing_blocks_until_the_gate_is_switched_on(tmp_path, monkeypatch):
    p = _chapter(str(tmp_path), "b_1", OPEN, FAILED)
    monkeypatch.delenv("RELEASE_GATE", raising=False)
    assert rg.mode() == "off" and rg.blocking(p, "final.mp4") == []
    monkeypatch.setenv("RELEASE_GATE", "critical")
    b = rg.blocking(p, "final.mp4")
    assert len(b) == 2 and all(x.startswith("Release check (") for x in b)
    legacy = _chapter(str(tmp_path), "b_2", LEGACY, PASSED)
    assert rg.blocking(legacy, "final.mp4") == []                     # review items do not block in "critical"
    monkeypatch.setenv("RELEASE_GATE", "strict")
    assert len(rg.blocking(legacy, "final.mp4")) == 1
    monkeypatch.setenv("RELEASE_GATE", "nonsense")
    assert rg.mode() == "off"


def test_a_merged_video_answers_for_the_chapters_in_it(tmp_path):
    root = str(tmp_path)
    _chapter(root, "s_1", CLEAN, PASSED)
    _chapter(root, "s_2", OPEN, PASSED)
    m = _chapter(root, "s_ch1-2", None, PASSED, name="final_range_1-2.mp4")
    _w(os.path.join(m, "exports", "_qa", "final_range_1-2.mp4.manifest.json"),
       {"context": {"kind": "range", "components": [{"project": "s_1", "name": "final.mp4"},
                                                    {"project": "s_2", "name": "final.mp4"}]}})
    v = rg.verdict(m, "final_range_1-2.mp4")
    assert v["release"] == "blocked" and v["critical"][0]["chapter"] == "s_2"


def test_scorecard_counts_and_says_what_it_cannot_know(tmp_path):
    root = str(tmp_path)
    _chapter(root, "c_1", CLEAN, PASSED)
    p2 = _chapter(root, "c_2", OPEN, FAILED, {"findings": [{"severity": "high", "issue": "x"}]})
    with open(os.path.join(p2, "edits.log.jsonl"), "w") as f:
        f.write('{"op": "reorder"}\n{"op": "add_line"}\n')
    _w(os.path.join(p2, "reviews.json"), {
        "final.mp4": {"status": "approved", "history": [{"status": "sent_back"}], "reviewed_by": None},
        "old.mp4": {"status": "merged"}})
    _w(os.path.join(root, "c_1", "reviews.json"), {"final.mp4": {"status": "approved", "history": []}})
    q = sc.quality(root)
    assert q["script"]["chapters"] == 2 and q["script"]["issues"] == 2 and q["script"]["by_type"] == {"hallucination": 2}
    assert q["script"]["outcome"] == {"clean": 1, "needs_review": 1} and q["script"]["rechecked"] == 1
    assert q["media"]["pass_pct"] == 50.0 and q["media"]["failed_rules"] == {"clipping": 1}
    assert q["timeline"]["high"] == 1
    c = q["corrections"]
    assert c["edits"] == 2 and c["exports_reviewed"] == 2 and c["sent_back"] == 1
    assert c["approved_first_time_pct"] == 50.0 and c["reviewer_recorded"] == 0 and "lower bounds" in c["caveat"]
    assert q["gate"]["blocked"] == 1 and q["gate"]["clear"] == 1 and q["gate"]["mode"] == "off"


def test_timeliness_from_release_and_from_detection():
    live = 1_800_000_000.0                                   # 2027-01-15 08:00 UTC
    series = {"mirrors": [{"release_dates": {"12": ["2027-01-14", False]}, "first_seen": {"12": live - 10 * 3600}},
                          {"release_dates": {"12": ["2027-01-13", True]}, "first_seen": {"12": 0}}]}
    posts = [{"project": "s_12", "label": "S 12", "url": "u1", "chapter_end": "12", "live_at": live},
             {"project": "s_13", "label": "S 13", "url": "u1", "chapter_end": "13", "live_at": live},
             {"project": "x_1", "label": "X 1", "url": "nope", "chapter_end": "1", "live_at": live}]
    t = sc.timeliness(posts, lambda url: series if url == "u1" else None)
    assert t["published"] == 3 and t["from_release"]["videos"] == 1 and t["from_detection"]["videos"] == 1
    r = t["videos"][0]
    assert r["release_day"] == "2027-01-13" and r["release_approx"] is True     # earliest any mirror reports
    assert r["hours_from_release"] == 56.0 and r["hours_from_detection"] == 10.0
    assert t["from_release"]["within_48h"] == 0 and t["from_detection"]["within_24h_pct"] == 100.0
    assert sum(t["unmeasured"].values()) == 2


def test_cost_summary():
    ledger = [{"cost": 1.2, "updated_at": 1}, {"cost": 0.4, "updated_at": 2}, {"cost": 0.9, "updated_at": 3}, {}]
    idx = {"by_stage": {("2026-11", "describe"): 3.0, ("2026-11", "unlabelled"): 1.0, ("2026-10", "voice"): 9.0}}
    c = sc.cost(ledger, idx, "2026-11")
    assert c["per_chapter_median"] == 0.9 and c["under_1_dollar_pct"] == 66.7 and c["under_50_cents_pct"] == 33.3
    assert c["month_by_stage"] == {"describe": 3.0, "unlabelled": 1.0} and c["month_labelled_pct"] == 75.0
