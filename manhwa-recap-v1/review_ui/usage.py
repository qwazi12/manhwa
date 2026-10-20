"""
Cost & abuse guardrails — shared usage tracker for every external API call the
pipeline makes (Gemini vision/text/embeddings, Google TTS).

Design:
  - `gate(kind, units, model=...)` is a context manager. On ENTER it checks
    whether making this call would breach any cap and raises UsageCapExceeded
    BEFORE the call happens (so an over-limit call never actually fires and
    never gets billed). On successful EXIT it commits the usage and appends a
    structured JSONL log line. A failed call (exception inside the `with`
    block) is never committed — no charge, no count.
  - State (daily counters + per-job counters) persists to a JSON file under
    review_ui/projects/_usage/ — that directory is the SAME one the ingest
    pipeline already writes projects into, which entrypoint.sh symlinks to the
    Railway persistent volume. No new mount/symlink needed; survives restarts.
  - Caps are env-configurable (sane defaults below). A per-job cap stops one
    runaway ingestion; a daily cap stops the day's TOTAL spend across every
    job, and persists across restarts (it's date-keyed and resets at UTC
    midnight).
  - Works across the subprocess boundary: `describe.py`'s Gemini calls happen
    in a separate OS process (panel-describe/run.py via subprocess.run), so
    the "current job id" is threaded through via the RECAP_JOB_ID env var for
    that child process; in-process callers (narrate/matcher/TTS, which run
    directly inside the ingest thread) use `set_job()` + thread-local storage
    instead. `get_job_id()` checks thread-local first, then the env var.

No dashboards — `calls.log.jsonl` is one JSON object per external call
(provider, kind, units, estimated cost, running job/day totals). `tail -f` or
`grep` it. `counters.json` is the current day's running totals, for a cheap
`GET /api/usage` view in the UI if wanted later.
"""

import contextlib
import fcntl
import json
import os
import threading
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
USAGE_DIR = os.path.join(HERE, "projects", "_usage")
os.makedirs(USAGE_DIR, exist_ok=True)
LOG_PATH = os.path.join(USAGE_DIR, "calls.log.jsonl")
COUNTS_PATH = os.path.join(USAGE_DIR, "counters.json")
LOCK_PATH = os.path.join(USAGE_DIR, ".lock")

_local = threading.local()


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---- caps (env-configurable) --------------------------------------------
# Per-job cap is a RUNAWAY-LOOP guard, not the spend limit — size it so one
# real full chapter never trips it: describe (~1 Gemini call/panel) + narrate
# (~1/scene) + match (embeds every beat AND every panel). A long chapter
# (167 beats + ~160 panels, like "A Painter Who Draws Dungeons" ch.1) needs
# ~530 calls; 2000 leaves headroom for the biggest chapters while still
# catching an infinite loop. The DAILY SPEND cap ($5) is the real wallet guard.
MAX_GEMINI_CALLS_PER_JOB = int(_envf("MAX_GEMINI_CALLS_PER_JOB", 2000))
MAX_TTS_CHARS_PER_JOB = int(_envf("MAX_TTS_CHARS_PER_JOB", 120000))
MAX_DAILY_GEMINI_CALLS = int(_envf("MAX_DAILY_GEMINI_CALLS", 6000))
MAX_DAILY_TTS_CHARS = int(_envf("MAX_DAILY_TTS_CHARS", 400000))
MAX_DAILY_SPEND_USD = _envf("MAX_DAILY_SPEND_USD", 5.0)

# Rough, clearly-labeled ESTIMATES (not billing-accurate) used only to give
# the daily spend cap a concrete number. Override via env if pricing changes.
# EST_COST_PER_GEMINI_CALL_USD is the FALLBACK for unrecognized models (sized
# for flash-tier describe calls, the historical default).
EST_COST_PER_GEMINI_CALL_USD = _envf("EST_COST_PER_GEMINI_CALL_USD", 0.001)
EST_COST_PER_TTS_1K_CHARS_USD = _envf("EST_COST_PER_TTS_1K_CHARS_USD", 0.016)

# Per-model per-call estimates. Matched by PREFIX (first hit wins), so version
# suffixes like "-preview" still resolve. Deliberately conservative (high) so
# the daily spend cap errs on the safe side: narration now uses a pro-tier
# model whose long-context calls cost far more per call than flash describe
# calls, and embedding calls cost far less — pricing them all at the flash
# rate understated real spend (the 2026-07-12 audit finding).
EST_GEMINI_MODEL_COST_USD = [
    ("gemini-3.1-pro",    _envf("EST_COST_PRO_CALL_USD", 0.02)),
    ("gemini-3-pro",      _envf("EST_COST_PRO_CALL_USD", 0.02)),
    ("gemini-embedding",  _envf("EST_COST_EMBED_CALL_USD", 0.0002)),
    ("gemini-3.5-flash",  EST_COST_PER_GEMINI_CALL_USD),
    ("gemini-3.1-flash",  EST_COST_PER_GEMINI_CALL_USD),
]


def _gemini_call_cost(model):
    m = (model or "").lower()
    for prefix, cost in EST_GEMINI_MODEL_COST_USD:
        if m.startswith(prefix):
            return cost
    return EST_COST_PER_GEMINI_CALL_USD


class UsageCapExceeded(RuntimeError):
    """A guardrail cap would be breached — raised BEFORE the API call fires."""


def set_job(job_id):
    """Call once at the start of an in-process job (ingestion thread)."""
    _local.job_id = job_id


def get_job_id():
    return getattr(_local, "job_id", None) or os.environ.get("RECAP_JOB_ID", "unknown")


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_counts():
    try:
        with open(COUNTS_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    if d.get("date") != _today():
        d = {"date": _today(), "gemini_calls": 0, "tts_chars": 0,
             "est_cost_usd": 0.0, "jobs": {}}
    d.setdefault("jobs", {})
    return d


def _save_counts(d):
    tmp = COUNTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, COUNTS_PATH)


def _append_log(entry):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


@contextlib.contextmanager
def _flock():
    """Cross-process advisory lock (calls are already serialized by the
    pipeline's own pacing, this is just a safety net against races)."""
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _est_cost(kind, units, model=""):
    if kind == "gemini":
        return units * _gemini_call_cost(model)
    return units / 1000.0 * EST_COST_PER_TTS_1K_CHARS_USD


@contextlib.contextmanager
def gate(kind, units, model=""):
    """kind: 'gemini' (units = number of calls, usually 1) or 'tts'
    (units = character count). Raises UsageCapExceeded BEFORE the wrapped
    call if it would breach a per-job or daily cap; commits usage + logs
    only if the wrapped call completes without raising."""
    job_id = get_job_id()
    est_cost = _est_cost(kind, units, model)

    with _flock():
        d = _load_counts()
        job = d["jobs"].setdefault(job_id, {"gemini_calls": 0, "tts_chars": 0})
        if kind == "gemini":
            if job["gemini_calls"] + units > MAX_GEMINI_CALLS_PER_JOB:
                raise UsageCapExceeded(
                    f"MAX_GEMINI_CALLS_PER_JOB={MAX_GEMINI_CALLS_PER_JOB} would be "
                    f"exceeded for job '{job_id}' ({job['gemini_calls']} + {units})")
            if d["gemini_calls"] + units > MAX_DAILY_GEMINI_CALLS:
                raise UsageCapExceeded(
                    f"MAX_DAILY_GEMINI_CALLS={MAX_DAILY_GEMINI_CALLS} would be "
                    f"exceeded today ({d['gemini_calls']} + {units})")
        else:
            if job["tts_chars"] + units > MAX_TTS_CHARS_PER_JOB:
                raise UsageCapExceeded(
                    f"MAX_TTS_CHARS_PER_JOB={MAX_TTS_CHARS_PER_JOB} would be "
                    f"exceeded for job '{job_id}' ({job['tts_chars']} + {units})")
            if d["tts_chars"] + units > MAX_DAILY_TTS_CHARS:
                raise UsageCapExceeded(
                    f"MAX_DAILY_TTS_CHARS={MAX_DAILY_TTS_CHARS} would be "
                    f"exceeded today ({d['tts_chars']} + {units})")
        if d["est_cost_usd"] + est_cost > MAX_DAILY_SPEND_USD:
            raise UsageCapExceeded(
                f"MAX_DAILY_SPEND_USD=${MAX_DAILY_SPEND_USD} would be exceeded "
                f"today (${d['est_cost_usd']:.4f} + ${est_cost:.4f} est.)")

    yield  # --- the actual API call happens here, outside the lock ---

    with _flock():
        d = _load_counts()
        job = d["jobs"].setdefault(job_id, {"gemini_calls": 0, "tts_chars": 0})
        if kind == "gemini":
            job["gemini_calls"] += units
            d["gemini_calls"] += units
        else:
            job["tts_chars"] += units
            d["tts_chars"] += units
        d["est_cost_usd"] = round(d["est_cost_usd"] + est_cost, 6)
        _save_counts(d)
        _append_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "provider": "gemini" if kind == "gemini" else "google-tts",
            "kind": kind, "model": model, "units": units,
            "unit": "call" if kind == "gemini" else "chars",
            "est_cost_usd": round(est_cost, 6),
            "job_totals": dict(job),
            "daily_totals": {"gemini_calls": d["gemini_calls"],
                              "tts_chars": d["tts_chars"],
                              "est_cost_usd": d["est_cost_usd"]},
        })


def daily_summary():
    with _flock():
        return _load_counts()
