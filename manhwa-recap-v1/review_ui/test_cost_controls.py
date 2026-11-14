"""Cost controls added 2026-11-13: every paid call is labelled with its stage
and chapter; pooled calls keep the requested tier; Flex stops being requested
while Google refuses it; image calls ask for low thinking; a code-only change
does not make paid work "stale"; a failed voice line cannot brick a chapter."""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.dirname(HERE)
ROOT = os.path.dirname(RECAP)
for p in (HERE, RECAP, os.path.join(ROOT, "panel-describe")):
    if p not in sys.path:
        sys.path.insert(0, p)
import generation_cache as cache  # noqa: E402
import usage  # noqa: E402


def _fresh_usage(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(usage, "USAGE_DIR", d)
    monkeypatch.setattr(usage, "LOG_PATH", os.path.join(d, "calls.log.jsonl"))
    for name in ("COUNTS_PATH", "LOCK_PATH"):
        if hasattr(usage, name):
            monkeypatch.setattr(usage, name, os.path.join(d, os.path.basename(getattr(usage, name))))
    return d


def test_log_line_carries_stage_project_and_requested_tier(monkeypatch):
    _fresh_usage(monkeypatch)
    usage.set_job("job-x")
    usage.set_tier("flex")
    with usage.stage("describe", project="series_12"):
        with usage.gate("gemini", 1, model="gemini-3.8-flash") as m:
            m.tokens(100, 20, 0)
    rec = json.loads(open(usage.LOG_PATH).read().splitlines()[-1])
    assert (rec["job_id"], rec["stage"], rec["project"]) == ("job-x", "describe", "series_12")
    assert rec["requested_tier"] == "flex"
    assert usage.get_stage() is None            # restored after the block
    usage.set_tier("")


def test_carry_takes_job_stage_project_and_tier_into_a_pool():
    usage.set_job("job-y")
    usage.set_stage("framing")
    usage.set_project("series_7")
    usage.set_tier("flex")
    seen = lambda: (usage.get_job_id(), usage.get_stage(), usage.get_project(), usage.requested_tier())
    with ThreadPoolExecutor(1) as ex:
        assert ex.submit(usage.carry(seen)).result() == ("job-y", "framing", "series_7", "flex")
        assert ex.submit(seen).result()[1:] == (None, None, "")     # nothing leaks into the worker
    usage.set_stage(None), usage.set_project(None), usage.set_tier("")


def test_flex_is_skipped_after_repeated_refusals_then_tried_again(monkeypatch):
    monkeypatch.setitem(usage._flex, "fails", 0)
    monkeypatch.setitem(usage._flex, "until", 0.0)
    usage.set_tier("flex")
    try:
        assert usage.effective_tier(now=1000) == "flex"
        for _ in range(usage.FLEX_TRIP):
            usage.flex_result(False, now=1000)
        assert usage.effective_tier(now=1001) == ""                       # breaker open: Standard
        assert usage.effective_tier(now=1000 + usage.FLEX_COOLDOWN_S + 1) == "flex"
        usage.flex_result(True)
        assert usage._flex["fails"] == 0
    finally:
        usage.set_tier("")


def test_thinking_levels(monkeypatch):
    monkeypatch.delenv("VISION_THINKING_LEVEL", raising=False)
    monkeypatch.delenv("SCRIPT_THINKING_LEVEL", raising=False)
    assert usage.thinking_level("vision", "gemini-3.8-flash") == "low"
    assert usage.thinking_level("script", "gemini-3.8-flash") == ""       # the writer keeps its default
    monkeypatch.setenv("VISION_THINKING_LEVEL", "default")
    assert usage.thinking_level("vision", "gemini-3.8-flash") == ""
    monkeypatch.setenv("VISION_THINKING_LEVEL", "low")
    usage.thinking_unsupported("some-old-model")
    assert usage.thinking_level("vision", "some-old-model") == ""


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_OK = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "{}"}]}],
       "usage": {"total_input_tokens": 10, "total_output_tokens": 2}}


def test_image_call_asks_for_low_thinking_and_survives_a_model_that_refuses_it(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    import describe
    monkeypatch.delenv("VISION_THINKING_LEVEL", raising=False)
    usage._no_thinking_level.discard("m1")
    sent = []

    def fake_open(req, timeout=None, context=None):
        body = json.loads(req.data)
        sent.append(body)
        if len(sent) == 1:
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(
                b'{"error":{"message":"Thinking level THINKING_LEVEL_LOW is not supported for this model."}}'))
        return _Resp(_OK)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert describe._call_interactions_api("AQ.k", "m1", "aGk=", "image/png") == "{}"
    assert sent[0]["generation_config"] == {"thinking_level": "low"}
    assert "generation_config" not in sent[1]
    sent.clear()
    sent.append(None)                                  # next call: no refusal
    assert describe._call_interactions_api("AQ.k", "m1", "aGk=", "image/png") == "{}"
    assert "generation_config" not in sent[1]          # not sent again for that model
    usage._no_thinking_level.discard("m1")


def test_a_refused_flex_request_is_repeated_on_standard_and_counted(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    import describe
    monkeypatch.setitem(usage._flex, "fails", 0)
    monkeypatch.setitem(usage._flex, "until", 0.0)
    sent = []

    def fake_open(req, timeout=None, context=None):
        body = json.loads(req.data)
        sent.append(body)
        if body.get("service_tier") == "flex":
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"{}"))
        return _Resp(_OK)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    usage.set_tier("flex")
    try:
        for _ in range(usage.FLEX_TRIP):
            describe._call_interactions_api("AQ.k", "m2", "aGk=", "image/png")
        assert [b.get("service_tier") for b in sent] == ["flex", None] * usage.FLEX_TRIP
        sent.clear()
        describe._call_interactions_api("AQ.k", "m2", "aGk=", "image/png")
        assert [b.get("service_tier") for b in sent] == [None]            # one request, not two
    finally:
        usage.set_tier("")
        usage._flex.update(fails=0, until=0.0)


def test_code_only_change_keeps_paid_work_current(monkeypatch, tmp_path):
    monkeypatch.delenv("CACHE_CODE_STRICT", raising=False)
    out = tmp_path / "script.txt"
    out.write_text("the script")
    rec = tmp_path / "script.cache.json"
    old = {"panels": [1, 2], "model": "m", "code": {"narrate.py": "aaa"}}
    cache.record(rec, old, {"script.txt": str(out)})
    new_code = dict(old, code={"narrate.py": "bbb"})
    assert cache.status(rec, new_code, {"script.txt": str(out)}) == "current"
    assert cache.status(rec, dict(old, model="other"), {"script.txt": str(out)}) == "stale"
    out.write_text("someone edited it")
    assert cache.status(rec, new_code, {"script.txt": str(out)}) == "edited"
    monkeypatch.setenv("CACHE_CODE_STRICT", "1")
    out.write_text("the script")
    assert cache.status(rec, new_code, {"script.txt": str(out)}) == "stale"


def test_a_cut_voice_pool_leaves_a_recognisable_record(tmp_path):
    rec = tmp_path / "voice.cache.json"
    assert cache.recorded_outputs(rec) is None
    inputs = {"beats": ["a", "b"], "code": {"gemini_tts.py": "x"}}
    cache.open_record(rec, inputs)
    assert cache.recorded_outputs(rec) == set() and cache.status(rec, inputs, {}) == "current"
    a = tmp_path / "beat_000.mp3"
    a.write_bytes(b"A")
    cache.record(rec, inputs, {"beat_000.mp3": str(a)})
    # a second line was written by a pool that was then cut: on disk, not in the record
    b = tmp_path / "beat_001.mp3"
    b.write_bytes(b"B")
    on_disk = {"beat_000.mp3": str(a), "beat_001.mp3": str(b)}
    assert cache.status(rec, inputs, on_disk) == "edited"                 # what bricked the chapter
    known = cache.recorded_outputs(rec)
    kept = {n: p for n, p in on_disk.items() if n in known}               # ingest drops the orphan…
    assert cache.status(rec, inputs, kept) == "current"                   # …and the chapter continues


def test_output_caps(monkeypatch):
    """A cap bounds what one call can cost. Crop planner: on by default;
    describe / script: only when set (not tried against the live API)."""
    for k in ("CROP_MAX_OUTPUT_TOKENS", "VISION_MAX_OUTPUT_TOKENS", "SCRIPT_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    assert usage.output_cap("crop", "m") == 8192
    assert usage.output_cap("vision", "m") == 0 and usage.output_cap("script", "m") == 0
    monkeypatch.setenv("VISION_MAX_OUTPUT_TOKENS", "4096")
    assert usage.output_cap("vision", "m") == 4096
    monkeypatch.setenv("CROP_MAX_OUTPUT_TOKENS", "0")
    assert usage.output_cap("crop", "m") == 0
    monkeypatch.setenv("CROP_MAX_OUTPUT_TOKENS", "junk")
    assert usage.output_cap("crop", "m") == 8192
    usage.output_cap_unsupported("old-model")
    assert usage.output_cap("vision", "old-model") == 0
