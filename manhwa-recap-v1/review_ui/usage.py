"""
Cost & abuse guardrails — shared usage tracker for every external API call the
pipeline makes (Gemini vision/text/embeddings, Google TTS, Claude validation).

Three `kind`s, three units: 'gemini' and 'claude' count CALLS, 'tts' counts
CHARACTERS. Every one of them is gated, metered and logged the same way, so a
new provider can never spend money outside the daily cap or outside the cost
figure the storyboard header shows.

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
import sys
import threading
import time
import uuid
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
# Claude does board VALIDATION, not generation: one call per batch of rows plus
# a vision call per flagged panel. A 138-panel chapter is ~6 batch calls + at
# most a few dozen vision calls, so 400/job catches a loop without ever tripping
# on real work. The daily SPEND cap below is still the real wallet guard.
MAX_CLAUDE_CALLS_PER_JOB = int(_envf("MAX_CLAUDE_CALLS_PER_JOB", 400))
MAX_DAILY_CLAUDE_CALLS = int(_envf("MAX_DAILY_CLAUDE_CALLS", 1200))
MAX_TTS_CHARS_PER_JOB = int(_envf("MAX_TTS_CHARS_PER_JOB", 120000))
MAX_DAILY_GEMINI_CALLS = int(_envf("MAX_DAILY_GEMINI_CALLS", 6000))
MAX_DAILY_TTS_CHARS = int(_envf("MAX_DAILY_TTS_CHARS", 400000))
MAX_DAILY_SPEND_USD = _envf("MAX_DAILY_SPEND_USD", 5.0)
# Owner, 2026-10-05: the daily limit can be changed in Settings, but never past
# this ceiling, which stays a Railway variable. The check below is unchanged;
# only the number it compares against comes from daily_cap().
MAX_DAILY_SPEND_CEILING_USD = _envf("MAX_DAILY_SPEND_CEILING_USD", 25.0)
CAP_OVERRIDE_NAME = "spend_cap.json"


def _cap_path():
    return os.path.join(USAGE_DIR, CAP_OVERRIDE_NAME)


def cap_override():
    """The limit set in Settings, or None (then the Railway value applies)."""
    try:
        with open(_cap_path(), encoding="utf-8") as f:
            rec = json.load(f)
        v = float(rec.get("usd"))
        return {**rec, "usd": v} if v > 0 else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def daily_cap():
    """The daily spend limit in force: the Settings value if set, never above
    the Railway ceiling; otherwise MAX_DAILY_SPEND_USD."""
    o = cap_override()
    if o is None:
        return MAX_DAILY_SPEND_USD
    return min(o["usd"], max(MAX_DAILY_SPEND_CEILING_USD, MAX_DAILY_SPEND_USD))


def set_daily_cap(usd, by="owner"):
    """usd=None goes back to the Railway value. Returns (before, after)."""
    before = daily_cap()
    if usd is None:
        try:
            os.remove(_cap_path())
        except OSError:
            pass
    else:
        usd = round(float(usd), 2)
        top = max(MAX_DAILY_SPEND_CEILING_USD, MAX_DAILY_SPEND_USD)
        if not 0.5 <= usd <= top:
            raise ValueError(f"the daily limit must be between $0.50 and ${top:.0f} "
                             f"(the ceiling is the Railway variable MAX_DAILY_SPEND_CEILING_USD)")
        os.makedirs(USAGE_DIR, exist_ok=True)
        tmp = _cap_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"usd": usd, "set_at": time.time(), "by": by, "previous": before}, f)
        os.replace(tmp, _cap_path())
    return before, daily_cap()

# Rough, clearly-labeled ESTIMATES (not billing-accurate) used only to give
# the daily spend cap a concrete number. Override via env if pricing changes.
# EST_COST_PER_GEMINI_CALL_USD is the FALLBACK for unrecognized models (sized
# for flash-tier describe calls, the historical default).
EST_COST_PER_GEMINI_CALL_USD = _envf("EST_COST_PER_GEMINI_CALL_USD", 0.001)
EST_COST_PER_TTS_1K_CHARS_USD = _envf("EST_COST_PER_TTS_1K_CHARS_USD", 0.016)
# Pre-flight guess for a Claude call, used ONLY by the cap check that runs
# BEFORE the call. Real token counts replace it on the way out. Sized for a
# validator batch (a couple of dozen rows in, a findings list out) and rounded
# UP, so the daily cap errs toward stopping early rather than overspending.
EST_COST_PER_CLAUDE_CALL_USD = _envf("EST_COST_PER_CLAUDE_CALL_USD", 0.05)

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
    # Pre-call guess only (the cap check runs BEFORE the call). Sized from a
    # measured describe call (~2,250 in / ~800 out incl. thinking) at the
    # published $1.50/$9.00 — the old $0.001 under-guessed it ~10x.
    # 2026-11-14: raised to the measured 99th percentile of a metered call
    # (production log, 2026-10-20..11-13: 3.8-flash n=9,465 p50 $0.0026 p95
    # $0.0118 p99 $0.0208 max $0.102; 3.5-flash n=451 p99 $0.033; 2.5-flash
    # n=1,509 p99 $0.0107 max $0.158). At the old values 17% of 3.8-flash
    # calls and every 2.5-flash call cost more than was held for them.
    ("gemini-3.5-flash",  _envf("EST_COST_FLASH35_CALL_USD", 0.033)),
    ("gemini-3.8-flash",  _envf("EST_COST_FLASH38_CALL_USD", 0.021)),
    ("gemini-2.5-flash",  _envf("EST_COST_FLASH25_CALL_USD", 0.011)),
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


def set_stage(stage):
    """Label this thread's paid calls with the pipeline stage that makes them
    (describe, script, voice, framing, …). Written to every log line so spend
    can be reported per stage; before 2026-11-13 no call recorded its stage."""
    _local.stage = (stage or "").strip() or None


def get_stage():
    """Thread setting first; a subprocess (describe) gets RECAP_STAGE."""
    return getattr(_local, "stage", None) or os.environ.get("RECAP_STAGE") or None


def set_project(project_id):
    """The chapter (project folder name) this thread's paid calls belong to."""
    _local.project = (project_id or "").strip() or None


def get_project():
    return getattr(_local, "project", None) or os.environ.get("RECAP_PROJECT") or None


@contextlib.contextmanager
def stage(name, project=None):
    """`with usage.stage("seo", project=pid):` — label the calls made inside,
    then restore what was set before."""
    prev_s, prev_p = getattr(_local, "stage", None), getattr(_local, "project", None)
    _local.stage = name
    if project:
        _local.project = project
    try:
        yield
    finally:
        _local.stage, _local.project = prev_s, prev_p


@contextlib.contextmanager
def scope(job_id=None, stage=None, project=None):
    """Label everything paid inside the block — job, stage, chapter — and put
    back what was set before (request threads are reused). Work that runs
    outside a chapter job (packaging after an ingest, a title rewrite from the
    site) used to be logged under the job "unknown": 16% of spend between
    2026-10-25 and 11-08 could not be traced to a chapter."""
    prev = (getattr(_local, "job_id", None), getattr(_local, "stage", None),
            getattr(_local, "project", None))
    if job_id:
        _local.job_id = job_id
    if stage:
        _local.stage = stage
    if project:
        _local.project = project
    try:
        yield
    finally:
        _local.job_id, _local.stage, _local.project = prev


def carry(fn):
    """Wrap fn so it runs under the CALLER's job id in a worker thread.

    The job id is thread-local; ThreadPoolExecutor workers start without it,
    so every voice line recorded 4-at-a-time landed in one shared "unknown"
    job. Its per-job cap (MAX_TTS_CHARS_PER_JOB) then filled up across a day's
    chapters and paused every new one as if the daily budget were spent
    (owner, 2026-10-05: "why the works keep pausing").

    The requested service tier, the stage label and the project ride along
    too (2026-11-13): they are thread-local as well, so pooled calls asked for
    Standard although the chapter asked for Flex, and carried no label."""
    jid = getattr(_local, "job_id", None)
    stg = getattr(_local, "stage", None)
    proj = getattr(_local, "project", None)
    tier = getattr(_req_local, "tier", "")

    def run(*a, **k):
        prev = (getattr(_local, "job_id", None), getattr(_local, "stage", None),
                getattr(_local, "project", None), getattr(_req_local, "tier", ""))
        if jid:
            _local.job_id = jid
        if stg:
            _local.stage = stg
        if proj:
            _local.project = proj
        if tier:
            _req_local.tier = tier
        try:
            return fn(*a, **k)
        finally:
            _local.job_id, _local.stage, _local.project, _req_local.tier = prev
    return run


def _today():
    # Eastern Time (user directive 2026-07-19): daily caps reset at midnight
    # America/New_York, and the storyboard shows the ET date so a UTC
    # rollover can never look like lost spend again.
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:                       # zoneinfo/tzdata missing: fall back
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Every counter bucket, in one place. Adding a provider means adding it here
# and nowhere else — the day bucket, each job bucket and the lifetime bucket
# are all filled through this, so they cannot drift apart again.
_CALL_COUNTERS = ("gemini_calls", "claude_calls", "tts_chars")
# Money, kept apart (2026-11-14). est_cost_usd (the day's spend everything
# else reads) = confirmed_usd + estimated_usd.
#   confirmed_usd  priced from the tokens / audio the provider reported
#   estimated_usd  the call succeeded but reported no usage: the estimate stands
#   uncertain_usd  the call ended with NO answer (timeout, dropped connection,
#                  process died mid-call): it may have been billed. Counted at
#                  the amount held for it, against the cap, never as "free".
# Money still held for running calls is in "reserved".
_MONEY = ("confirmed_usd", "estimated_usd", "uncertain_usd")


def _zero_counts():
    return {k: 0 for k in _CALL_COUNTERS}


def _job_slot(d, job_id):
    """The job's counter dict, with any counter added after it was first
    written backfilled to 0. Counter files predating a new provider are the
    normal case, not an edge case."""
    job = d["jobs"].setdefault(job_id, _zero_counts())
    for k in _CALL_COUNTERS:
        job.setdefault(k, 0)
    return job


def _load_counts():
    try:
        with open(COUNTS_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    if d.get("date") != _today():
        # Day rollover: fold the finished day into the lifetime totals so
        # all-time spend survives every reset (and restarts — it's on disk).
        life = d.get("lifetime", dict(_zero_counts(), est_cost_usd=0.0))
        for k in _CALL_COUNTERS:
            life[k] = life.get(k, 0) + d.get(k, 0)
        life["est_cost_usd"] = round(life.get("est_cost_usd", 0.0) + d.get("est_cost_usd", 0.0), 6)
        for k in _MONEY:
            life[k] = round(life.get(k, 0.0) + d.get(k, 0.0), 6)
        # holds, learned call sizes and unwritten ledger lines belong to no day
        d = dict(_zero_counts(), date=_today(), est_cost_usd=0.0,
                 jobs={}, lifetime=life, reserved=d.get("reserved") or {},
                 observed=d.get("observed") or {}, pending_ledger=d.get("pending_ledger") or [])
    d.setdefault("jobs", {})
    d.setdefault("reserved", {})
    d.setdefault("observed", {})
    d.setdefault("pending_ledger", [])
    for k in _MONEY:
        d.setdefault(k, 0.0)
    # A counters.json written before a provider existed has no key for it.
    for k in _CALL_COUNTERS:
        d.setdefault(k, 0)
    if "lifetime" not in d:
        # First run after the lifetime feature landed: reconstruct history
        # from the append-only call log so no past spend is dropped, then
        # subtract today's (still-active) counters which are tracked live.
        life = dict(_zero_counts(), est_cost_usd=0.0)
        _bucket = {"gemini": "gemini_calls", "claude": "claude_calls"}
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    life[_bucket.get(e.get("kind"), "tts_chars")] += e.get("units", 0)
                    life["est_cost_usd"] += e.get("est_cost_usd", 0.0)
        except FileNotFoundError:
            pass
        for k in _CALL_COUNTERS:
            life[k] -= d.get(k, 0)
        life["est_cost_usd"] = round(max(0.0, life["est_cost_usd"] - d.get("est_cost_usd", 0.0)), 6)
        d["lifetime"] = life
    return d


def _save_counts(d):
    tmp = COUNTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, COUNTS_PATH)


def _append_log(entry):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _ledger_tail_ids(block=256 * 1024):
    """rids in the last part of the ledger (to avoid writing a line twice)."""
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - block))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return set()
    out = set()
    for line in data.splitlines():
        try:
            rid = json.loads(line).get("rid")
        except (ValueError, AttributeError):
            continue
        if rid:
            out.add(rid)
    return out


def _flush_ledger(d):
    """Write the lines waiting in counters.json to the ledger. A line is put
    in counters.json (durably, with the money) BEFORE it is appended, so a
    failed or interrupted append loses nothing: it is written on a later call.
    Returns True when d changed. Call under the lock."""
    pending = d.get("pending_ledger") or []
    if not pending:
        return False
    seen = _ledger_tail_ids() if len(pending) > 1 or pending[0].get("_retry") else set()
    left = []
    for i, e in enumerate(pending):
        if e.get("rid") in seen:
            continue
        try:
            _append_log({k: v for k, v in e.items() if k != "_retry"})
        except OSError as err:
            print(f"[usage] ledger not writable ({err}); {len(pending) - i} line(s) kept for later", file=sys.stderr)
            left = [dict(x, _retry=True) for x in pending[i:]]
            break
    d["pending_ledger"] = left
    return True


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


# ---------------------------------------------------------------- tokens
# Per-call pricing is a blunt instrument: a 40-token prompt and an
# image-bearing describe call both counted the same. These are per-MILLION
# token rates, applied to the token counts the API actually reports.
#
# THE DEFAULTS BELOW ARE PLACEHOLDERS, NOT QUOTED PRICES. Set them from your
# own Google rate card via env; until then the UI labels the figure as being
# at "default rates" so nobody mistakes it for a bill.
def _price(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Google's PUBLISHED paid Standard-tier rates (ai.google.dev/gemini-api/docs/
# pricing, read 2026-10-03). Output INCLUDES thinking tokens. The Batch and
# Flex tiers bill exactly half (applied from the response's service_tier).
# gemini-3.8-flash rates double on 2027-01-01 (handled by date below).
GEMINI_PRICES_READ = "2026-10-03"
GEMINI_TOKEN_PRICES_PER_1M = [
    # (model prefix, input $/1M tokens, output $/1M tokens)
    # 3.1 Pro Preview: $2/$12 for prompts <= 200k tokens (ours always are).
    ("gemini-3.1-pro", _price("PRICE_PRO_IN_PER_1M", 2.00),
                       _price("PRICE_PRO_OUT_PER_1M", 12.0)),
    ("gemini-3-pro",   _price("PRICE_PRO_IN_PER_1M", 2.00),
                       _price("PRICE_PRO_OUT_PER_1M", 12.0)),
    ("gemini-embedding", _price("PRICE_EMBED_IN_PER_1M", 0.20), 0.0),
    ("gemini-3.5-flash", _price("PRICE_FLASH_IN_PER_1M", 1.50),
                         _price("PRICE_FLASH_OUT_PER_1M", 9.00)),
    ("gemini-3.8-flash", _price("PRICE_FLASH38_IN_PER_1M", 0.75),
                         _price("PRICE_FLASH38_OUT_PER_1M", 3.75)),
    ("gemini-3.1-flash", _price("PRICE_FLASH_IN_PER_1M", 1.50),
                         _price("PRICE_FLASH_OUT_PER_1M", 9.00)),
    ("gemini-2.5-flash", _price("PRICE_FLASH25_IN_PER_1M", 0.30),
                         _price("PRICE_FLASH25_OUT_PER_1M", 2.50)),
]
# Cached input is billed at the context-caching rate, not the input rate.
GEMINI_CACHED_PRICE_PER_1M = [
    ("gemini-3.5-flash", 0.15), ("gemini-3.8-flash", 0.075), ("gemini-2.5-flash", 0.03),
]
# Rates that change on a date: (prefix, from YYYY-MM-DD, input, output).
GEMINI_PRICE_CHANGES = [("gemini-3.8-flash", "2027-01-01", 1.50, 7.50)]
DISCOUNTED_TIERS = {"batch": 0.5, "flex": 0.5}

# Gemini TTS: Google's PUBLISHED paid-tier rates (ai.google.dev pricing,
# read 2026-10-02): text in $0.50/1M, audio out $9.00/1M (Flash) or $6.00/1M
# (Flash-Lite), audio billed at 25 tokens per second. These rates DOUBLE on
# 2027-01-01 ($1.00 / $18.00 / $12.00) — set the env vars then.
GEMINI_TTS_PRICES_PER_1M = [
    ("gemini-3.8-flash-lite-tts", _price("PRICE_TTS_IN_PER_1M", 0.50),
                                  _price("PRICE_TTS_LITE_OUT_PER_1M", 6.00)),
    ("gemini-3.8-flash-tts", _price("PRICE_TTS_IN_PER_1M", 0.50),
                             _price("PRICE_TTS_OUT_PER_1M", 9.00)),
]
GEMINI_TTS_PRICE_CHANGES = [("gemini-3.8-flash-lite-tts", "2027-01-01", 1.00, 12.00),
                            ("gemini-3.8-flash-tts", "2027-01-01", 1.00, 18.00)]
TTS_AUDIO_TOKENS_PER_SEC = 25
TTS_CHARS_PER_SEC = 12.0     # measured: 80 chars -> 6.5 s of Charon


def _dated(model, changes, rin, rout):
    """Apply a published future price change once its date has come. An env
    override (operator set the price by hand) always wins."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for prefix, since, nin, nout in changes:
        if (model or "").startswith(prefix) and today >= since:
            return nin, nout
    return rin, rout


def _gemini_tts_rates(model):
    for prefix, rin, rout in GEMINI_TTS_PRICES_PER_1M:
        if (model or "").startswith(prefix):
            if not os.environ.get("PRICE_TTS_OUT_PER_1M"):
                return _dated(model, GEMINI_TTS_PRICE_CHANGES, rin, rout)
            return rin, rout
    return GEMINI_TTS_PRICES_PER_1M[-1][1], GEMINI_TTS_PRICES_PER_1M[-1][2]


def gemini_tts_cost(model, prompt_tokens, audio_tokens):
    rin, rout = _gemini_tts_rates(model)
    return (prompt_tokens / 1e6) * rin + (audio_tokens / 1e6) * rout


# Claude rates, unlike the Gemini block above, are Anthropic's PUBLISHED list
# prices per million tokens (Opus 5 $5/$25, Sonnet 5 $3/$15, Haiku 4.5 $1/$5),
# not placeholders — rate_card() reports that difference so the UI can stop
# calling the Claude figure an assumption. Still env-overridable for when
# pricing moves. Matched by PREFIX, first hit wins; the fallback is the
# Opus (most expensive) rate so an unrecognised model is never under-billed.
CLAUDE_TOKEN_PRICES_PER_1M = [
    ("claude-opus",   _price("PRICE_CLAUDE_OPUS_IN_PER_1M", 5.0),
                      _price("PRICE_CLAUDE_OPUS_OUT_PER_1M", 25.0)),
    ("claude-sonnet", _price("PRICE_CLAUDE_SONNET_IN_PER_1M", 3.0),
                      _price("PRICE_CLAUDE_SONNET_OUT_PER_1M", 15.0)),
    ("claude-haiku",  _price("PRICE_CLAUDE_HAIKU_IN_PER_1M", 1.0),
                      _price("PRICE_CLAUDE_HAIKU_OUT_PER_1M", 5.0)),
]

CLAUDE_RATES_ARE_DEFAULT = not any(
    os.environ.get(k) for k in
    ("PRICE_CLAUDE_OPUS_IN_PER_1M", "PRICE_CLAUDE_OPUS_OUT_PER_1M",
     "PRICE_CLAUDE_SONNET_IN_PER_1M", "PRICE_CLAUDE_SONNET_OUT_PER_1M",
     "PRICE_CLAUDE_HAIKU_IN_PER_1M", "PRICE_CLAUDE_HAIKU_OUT_PER_1M"))

RATES_ARE_DEFAULT = not any(
    os.environ.get(k) for k in
    ("PRICE_PRO_IN_PER_1M", "PRICE_PRO_OUT_PER_1M", "PRICE_FLASH_IN_PER_1M",
     "PRICE_FLASH_OUT_PER_1M", "PRICE_EMBED_IN_PER_1M",
     "EST_COST_PER_TTS_1K_CHARS_USD"))


def _token_rates(model):
    m = (model or "").lower()
    if m.startswith("claude"):
        for prefix, rin, rout in CLAUDE_TOKEN_PRICES_PER_1M:
            if m.startswith(prefix):
                return rin, rout
        # Unknown Claude model: bill it at the Opus rate rather than guessing
        # low. Overstating spend fails safe; understating it does not.
        return (_price("PRICE_CLAUDE_OPUS_IN_PER_1M", 5.0),
                _price("PRICE_CLAUDE_OPUS_OUT_PER_1M", 25.0))
    for prefix, rin, rout in GEMINI_TOKEN_PRICES_PER_1M:
        if m.startswith(prefix):
            return _dated(m, GEMINI_PRICE_CHANGES, rin, rout)
    # Unknown Gemini model: bill at 3.5 Flash, the dearest Flash we run.
    return (_price("PRICE_FLASH_IN_PER_1M", 1.50),
            _price("PRICE_FLASH_OUT_PER_1M", 9.00))


def token_cost(model, prompt_tokens, output_tokens, cached_tokens=0, tier=""):
    """Dollar cost of one call from its REAL token counts. Output includes
    thinking. Cached input is billed at the caching rate; Batch/Flex at half."""
    rin, rout = _token_rates(model)
    m = (model or "").lower()
    rcache = next((r for p, r in GEMINI_CACHED_PRICE_PER_1M if m.startswith(p)), None)
    cached = min(int(cached_tokens or 0), int(prompt_tokens or 0)) if rcache is not None else 0
    cost = ((prompt_tokens - cached) / 1e6) * rin + (cached / 1e6) * (rcache or 0) \
        + (output_tokens / 1e6) * rout
    return cost * DISCOUNTED_TIERS.get((tier or "").lower(), 1.0)


_tier_local = threading.local()
_req_local = threading.local()


def set_tier(tier):
    """The service tier this thread's Gemini calls should REQUEST ('flex' for
    autopilot chapters — half price, may queue 1–15 min; '' = Standard)."""
    _req_local.tier = (tier or "").lower()


def requested_tier():
    """Thread setting first; a subprocess (describe) gets RECAP_SERVICE_TIER."""
    return getattr(_req_local, "tier", "") or os.environ.get("RECAP_SERVICE_TIER", "").lower()


# ---- Flex availability -------------------------------------------------
# Measured 2026-11-13: Google refused 8 of 8 Flex requests in 0.3 s each
# ("high demand"), and since 2026-11-04 one call in ~2,350 was served on Flex.
# Every refused request was then repeated on Standard, so a chapter paid full
# price AND made each call twice. After FLEX_TRIP refusals in a row Flex is
# skipped for FLEX_COOLDOWN_S, then tried again; one success closes the breaker.
FLEX_TRIP = int(os.environ.get("FLEX_TRIP", 3))
FLEX_COOLDOWN_S = float(os.environ.get("FLEX_COOLDOWN_S", 600))
_flex = {"fails": 0, "until": 0.0, "refused": 0, "served": 0}
_flex_lock = threading.Lock()


def effective_tier(now=None):
    """The tier to put on the next request: the requested one, unless Flex is
    requested and its breaker is open."""
    tier = requested_tier()
    if tier == "flex" and (now or time.time()) < _flex["until"]:
        return ""
    return tier


def flex_result(served, now=None):
    """Call after a Flex request: served=True on success, False on a capacity
    refusal (429/503)."""
    with _flex_lock:
        if served:
            _flex["fails"] = 0
            _flex["served"] += 1
            return
        _flex["fails"] += 1
        _flex["refused"] += 1
        if _flex["fails"] >= FLEX_TRIP:
            _flex["fails"] = 0
            _flex["until"] = (now or time.time()) + FLEX_COOLDOWN_S
            print(f"[usage] Flex refused {FLEX_TRIP} times in a row — using Standard "
                  f"for {FLEX_COOLDOWN_S:.0f}s, then trying Flex again", flush=True)


def flex_state():
    return dict(_flex)


# ---- Thinking ----------------------------------------------------------
# No call limited thinking before 2026-11-13, and thinking tokens (billed as
# output) were 48% of spend. Measured on 24 real panels: at "low" the text read
# from every panel was identical and thinking fell from ~270 tokens a panel to
# 0; speaker labels differed from the stored ones on 10 panels — the same 10 a
# second run at the default setting differed on. So image calls (describe,
# framing, cut checks) default to "low". Script writing keeps the model's
# default unless SCRIPT_THINKING_LEVEL is set. "" or "default" = model default.
_THINKING_DEFAULTS = {"vision": "low", "script": ""}
_no_thinking_level = set()          # models that rejected the setting


def thinking_level(kind, model=""):
    """'low' | 'medium' | 'high' | '' for a call of this kind ('vision' or
    'script'), from <KIND>_THINKING_LEVEL."""
    lvl = os.environ.get(f"{kind.upper()}_THINKING_LEVEL", _THINKING_DEFAULTS.get(kind, ""))
    lvl = (lvl or "").strip().lower()
    if lvl in ("", "default", "off") or model in _no_thinking_level:
        return ""
    return lvl


# Largest answer a call may produce (thinking counts), so one runaway call has
# a known worst cost. <KIND>_MAX_OUTPUT_TOKENS; 0 = no cap.
#   crop    on (8192): the SDK setting is documented; the dearest crop call on
#           record produced 63,061 output tokens ($0.158) for a few boxes
#   vision / script  off by default: the Interactions API field has not been
#           tried against the live service (no paid call was made for it) and
#           a cap that is too low would cut a script short. Set them after the
#           one-chapter validation run.
_OUTPUT_CAP_DEFAULTS = {"crop": 8192, "vision": 0, "script": 0}
_no_output_cap = set()


def output_cap(kind, model=""):
    try:
        cap = int(os.environ.get(f"{kind.upper()}_MAX_OUTPUT_TOKENS", _OUTPUT_CAP_DEFAULTS.get(kind, 0)))
    except ValueError:
        cap = _OUTPUT_CAP_DEFAULTS.get(kind, 0)
    return 0 if cap <= 0 or model in _no_output_cap else cap


def output_cap_unsupported(model):
    """The API rejected max_output_tokens for this model: stop sending it."""
    _no_output_cap.add(model)


def thinking_unsupported(model):
    """The API rejected thinking_level for this model: stop sending it."""
    _no_thinking_level.add(model)


def parse_gemini_usage(res):
    """Token counts from ANY Gemini response shape we receive, or {} if none.

    * generateContent REST: usageMetadata{promptTokenCount, candidatesTokenCount,
      thoughtsTokenCount, cachedContentTokenCount}
    * Interactions API (AQ. keys): usage{total_input_tokens, total_output_tokens,
      total_thought_tokens, total_cached_tokens} + top-level service_tier
    Thinking tokens are billed as output, so 'output' includes them. Before
    2026-10-03 the Interactions shape was read with generateContent names,
    every count came back 0, and 3,145 calls were recorded as free."""
    try:
        res = res or {}
        um = res.get("usageMetadata")
        if um:
            out = {"prompt": um.get("promptTokenCount") or 0,
                   "thoughts": um.get("thoughtsTokenCount") or 0,
                   "cached": um.get("cachedContentTokenCount") or 0}
            out["output"] = (um.get("candidatesTokenCount") or 0) + out["thoughts"]
        else:
            u = res.get("usage") or {}
            if not u:
                return {}
            out = {"prompt": u.get("total_input_tokens") or u.get("inputTokenCount") or 0,
                   "thoughts": u.get("total_thought_tokens") or 0,
                   "cached": u.get("total_cached_tokens") or 0}
            out["output"] = (u.get("total_output_tokens") or u.get("outputTokenCount") or 0) \
                + out["thoughts"]
        out["tier"] = str(res.get("service_tier") or "standard").lower()
        _tier_local.tier = out["tier"]
        _tier_local.thoughts = out["thoughts"]
        _tier_local.seen = dict(out)     # read by gate() when the caller fails before reporting
        return out
    except Exception:
        return {}


class Meter:
    """Handed to the caller by gate() so it can report what the API actually
    charged for. Without a report the old flat per-call estimate stands, so
    call sites that have not been updated keep working unchanged."""

    __slots__ = ("prompt_tokens", "output_tokens", "cached_tokens", "reported",
                 "thought_tokens", "tier", "free")

    def __init__(self):
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.thought_tokens = 0
        self.tier = ""
        self.reported = False
        self.free = False

    def not_sent(self):
        """The caller knows no request left the machine (it failed while being
        built): if the block raises, nothing is charged or held as uncertain."""
        self.free = True
        return self

    def tokens(self, prompt=0, output=0, cached=0):
        # A report of ZERO tokens is not a measurement — no real call costs
        # nothing. Treat it as unreported so the call keeps its estimate and
        # is logged metered=false, instead of being recorded as free.
        if not (int(prompt or 0) or int(output or 0)):
            return self
        self.prompt_tokens += int(prompt or 0)
        self.output_tokens += int(output or 0)
        self.cached_tokens += int(cached or 0)
        self.reported = True
        return self

    def from_anthropic(self, resp):
        """Read the anthropic SDK's usage block. Different field names from
        google-genai's, hence a separate reader rather than one that guesses:
        input_tokens / output_tokens, and cache_read_input_tokens for the part
        served from the prompt cache.

        Cache READS are counted as cached (they are billed at a tenth of the
        input rate, so folding them into prompt_tokens would overstate spend);
        cache WRITES are billed above the input rate and ARE part of
        input_tokens, so they are left there. Same contract as from_response:
        never break a pipeline over accounting."""
        try:
            u = getattr(resp, "usage", None)
            if u is None:
                return self
            cached = getattr(u, "cache_read_input_tokens", 0) or 0
            return self.tokens(
                getattr(u, "input_tokens", 0) or 0,
                getattr(u, "output_tokens", 0) or 0,
                cached)
        except Exception:
            return self

    def from_response(self, resp):
        """Read google-genai's usage_metadata if the SDK returned one.
        Silently does nothing when absent — never break a pipeline over
        accounting."""
        try:
            um = getattr(resp, "usage_metadata", None)
            if um is None:
                return self
            thoughts = getattr(um, "thoughts_token_count", 0) or 0
            self.thought_tokens += thoughts
            return self.tokens(
                getattr(um, "prompt_token_count", 0) or 0,
                (getattr(um, "candidates_token_count", 0) or 0) + thoughts,
                getattr(um, "cached_content_token_count", 0) or 0)
        except Exception:
            return self


def _est_cost(kind, units, model=""):
    if kind == "gemini":
        return units * _gemini_call_cost(model)
    if kind == "claude":
        return units * EST_COST_PER_CLAUDE_CALL_USD
    if kind == "tts" and (model or "").startswith("gemini"):
        # Before the call: estimate the audio from the text length.
        secs = units / TTS_CHARS_PER_SEC
        return gemini_tts_cost(model, units / 4.0, secs * TTS_AUDIO_TOKENS_PER_SEC)
    return units / 1000.0 * EST_COST_PER_TTS_1K_CHARS_USD


# Money held for calls that are running right now (2026-11-13/14). The gate
# used to compare the cap with money already SPENT, so two calls that started
# together both passed a cap only one of them fitted under. Each call now
# holds an amount in counters.json from the moment it is let through until it
# is settled; the cap check counts spent + uncertain + held.
#
# How much is held: the larger of the model's estimate (set to the measured
# 99th percentile) and the most expensive of the last OBSERVED_KEEP calls of
# the same kind / model / stage — so a stage that runs dearer than the table
# says is held at what it actually costs.
#
# A hold that is never settled (the process died mid-call, or the hold is
# older than RESERVE_TTL_S) is NOT dropped as if nothing was spent: it becomes
# uncertain_usd with a ledger line. recover() does that for every hold at
# server start, when no earlier call can still be running.
RESERVE_TTL_S = float(os.environ.get("USAGE_RESERVE_TTL_S", 1800))
OBSERVED_KEEP = int(os.environ.get("USAGE_OBSERVED_KEEP", 200))
# Calls of one job that ended with no answer, in a row, before the job is
# refused more paid calls for UNCERTAIN_COOLDOWN_S (a retry loop against a
# provider that times out can be billed for every attempt).
UNCERTAIN_STREAK = int(os.environ.get("USAGE_UNCERTAIN_STREAK", 6))
UNCERTAIN_COOLDOWN_S = float(os.environ.get("USAGE_UNCERTAIN_COOLDOWN_S", 900))
_UNIT_KEY = {"claude": "claude_calls", "gemini": "gemini_calls"}


def _obs_key(kind, model, stage):
    return f"{kind}|{model or ''}|{stage or ''}"


def reservation(kind, units, model="", stage=None, d=None):
    """What is held for one call before it runs."""
    est = _est_cost(kind, units, model)
    if kind == "tts":
        return est                        # billed per character / second of audio: already exact
    if d is None:
        with _flock():
            d = _load_counts()
    seen = (d.get("observed") or {}).get(_obs_key(kind, model, stage if stage is not None else get_stage())) or []
    return max(est, max(seen) * units if seen else 0.0)


def _abandon(d, rid, hold, why):
    """An unsettled hold becomes uncertain spend, with a ledger line."""
    usd = round(float(hold.get("usd") or 0), 6)
    d["reserved"].pop(rid, None)
    d["uncertain_usd"] = round(d.get("uncertain_usd", 0.0) + usd, 6)
    d.setdefault("pending_ledger", []).append({
        "ts": datetime.now(timezone.utc).isoformat(), "rid": rid, "job_id": hold.get("job"),
        "stage": hold.get("stage"), "project": hold.get("project"), "kind": hold.get("kind"),
        "model": hold.get("model"), "units": hold.get("units"), "est_cost_usd": 0.0,
        "uncertain_usd": usd, "outcome": "uncertain", "why": why, "metered": False})


def _held(d, now=None):
    """Live holds. Holds past RESERVE_TTL_S are turned into uncertain spend
    (their call cannot still be running), never silently dropped."""
    now = now or time.time()
    res = d.setdefault("reserved", {})
    for rid in [r for r, v in res.items() if now - float(v.get("at") or 0) > RESERVE_TTL_S]:
        _abandon(d, rid, res[rid], f"no result after {int(RESERVE_TTL_S)}s — the process that made the call is gone")
    return list(res.values())


def recover(why="the server restarted while this call was running"):
    """Server start: nothing from before the restart can still be running, so
    every hold left behind is a call whose outcome will never be reported.
    Each becomes uncertain spend. Returns how many, and their total."""
    with _flock():
        d = _load_counts()
        holds = dict(d.get("reserved") or {})
        for rid, h in holds.items():
            _abandon(d, rid, h, why)
        if holds or d.get("pending_ledger"):
            _save_counts(d)
            if _flush_ledger(d):
                _save_counts(d)
    return {"calls": len(holds), "uncertain_usd": round(sum(float(h.get("usd") or 0) for h in holds.values()), 6)}


def reserved_usd():
    """Money currently held for calls in flight (shown next to today's spend)."""
    with _flock():
        return round(sum(float(v.get("usd") or 0) for v in (_load_counts().get("reserved") or {}).values()), 6)


def committed_usd(d=None):
    """Everything that counts against today's cap: spent (confirmed +
    estimated), uncertain, and held for calls still running."""
    if d is None:
        with _flock():
            d = _load_counts()
    return round(float(d.get("est_cost_usd") or 0) + float(d.get("uncertain_usd") or 0)
                 + sum(float(v.get("usd") or 0) for v in (d.get("reserved") or {}).values()), 6)


def headroom():
    """Dollars left under today's cap, counting uncertain and held money."""
    with _flock():
        d = _load_counts()
    return round(daily_cap() - committed_usd(d), 6)


def answered_with_error(exc):
    """True when the provider ANSWERED with an HTTP error (the exception
    carries a 4xx/5xx status). Gemini and Anthropic do not bill a request they
    answer with an error. A timeout or a dropped connection carries no status:
    the request may have been processed and billed."""
    for attr in ("code", "status_code", "status", "http_status"):
        v = getattr(exc, attr, None)
        if callable(v):
            try:
                v = v()
            except Exception:  # noqa
                v = None
        if isinstance(v, int) and not isinstance(v, bool) and 400 <= v <= 599:
            return True
    return False


@contextlib.contextmanager
def gate(kind, units, model=""):
    """kind: 'gemini' or 'claude' (units = number of calls, usually 1), or
    'tts' (units = character count). Raises UsageCapExceeded BEFORE the wrapped
    call if it — together with every call already running and every call whose
    billing is uncertain — would breach a per-job or daily cap.

    How a call ends decides where its money goes:
      succeeded, usage reported     -> confirmed_usd (real tokens)
      succeeded, no usage reported  -> estimated_usd (the model's table estimate)
      failed AFTER an answer        -> confirmed_usd, ledger: failed_after_response
      provider answered with an error (HTTP 4xx/5xx), or the cap refused it,
        or the caller called meter.not_sent()   -> nothing charged
      ended with no answer (timeout, dropped connection, unknown error)
                                    -> uncertain_usd (the amount held)"""
    job_id = get_job_id()
    stage, project = get_stage(), get_project()
    ukey = _UNIT_KEY.get(kind, "tts_chars")
    rid = uuid.uuid4().hex

    with _flock():
        d = _load_counts()
        changed = _flush_ledger(d)
        job = _job_slot(d, job_id)
        held = _held(d)
        est_cost = reservation(kind, units, model, stage, d)
        held_units = sum(v.get("units") or 0 for v in held if v.get("key") == ukey)
        held_job = sum(v.get("units") or 0 for v in held if v.get("key") == ukey and v.get("job") == job_id)
        held_usd = sum(float(v.get("usd") or 0) for v in held)
        refusal = None
        streak = job.get("uncertain_streak") or 0
        if streak >= UNCERTAIN_STREAK and time.time() - float(job.get("uncertain_at") or 0) < UNCERTAIN_COOLDOWN_S:
            refusal = (f"{streak} paid calls in a row for job '{job_id}' ended without an answer (they may have "
                       f"been billed); no more are made for this job for {int(UNCERTAIN_COOLDOWN_S // 60)} minutes")
        elif kind == "claude":
            if job["claude_calls"] + held_job + units > MAX_CLAUDE_CALLS_PER_JOB:
                refusal = (f"MAX_CLAUDE_CALLS_PER_JOB={MAX_CLAUDE_CALLS_PER_JOB} would be "
                           f"exceeded for job '{job_id}' ({job['claude_calls'] + held_job} + {units})")
            elif d["claude_calls"] + held_units + units > MAX_DAILY_CLAUDE_CALLS:
                refusal = (f"MAX_DAILY_CLAUDE_CALLS={MAX_DAILY_CLAUDE_CALLS} would be "
                           f"exceeded today ({d['claude_calls'] + held_units} + {units})")
        elif kind == "gemini":
            if job["gemini_calls"] + held_job + units > MAX_GEMINI_CALLS_PER_JOB:
                refusal = (f"MAX_GEMINI_CALLS_PER_JOB={MAX_GEMINI_CALLS_PER_JOB} would be "
                           f"exceeded for job '{job_id}' ({job['gemini_calls'] + held_job} + {units})")
            elif d["gemini_calls"] + held_units + units > MAX_DAILY_GEMINI_CALLS:
                refusal = (f"MAX_DAILY_GEMINI_CALLS={MAX_DAILY_GEMINI_CALLS} would be "
                           f"exceeded today ({d['gemini_calls'] + held_units} + {units})")
        else:
            if job["tts_chars"] + held_job + units > MAX_TTS_CHARS_PER_JOB:
                refusal = (f"MAX_TTS_CHARS_PER_JOB={MAX_TTS_CHARS_PER_JOB} would be "
                           f"exceeded for job '{job_id}' ({job['tts_chars'] + held_job} + {units})")
            elif d["tts_chars"] + held_units + units > MAX_DAILY_TTS_CHARS:
                refusal = (f"MAX_DAILY_TTS_CHARS={MAX_DAILY_TTS_CHARS} would be "
                           f"exceeded today ({d['tts_chars'] + held_units} + {units})")
        cap = daily_cap()
        spent, unsure = d["est_cost_usd"], d.get("uncertain_usd", 0.0)
        if refusal is None and spent + unsure + held_usd + est_cost > cap:
            parts = f"${spent:.4f} spent"
            if unsure:
                parts += f" + ${unsure:.4f} for calls that ended without an answer"
            if held_usd:
                parts += f" + ${held_usd:.4f} held for calls running now"
            refusal = (f"the daily spend limit of ${cap:g} would be exceeded "
                       f"today ({parts} + ${est_cost:.4f} for this call)")
        if refusal is not None:
            if changed or len(held) != len(d["reserved"]):
                _save_counts(d)
            raise UsageCapExceeded(refusal)
        d["reserved"][rid] = {"usd": round(est_cost, 6), "key": ukey, "units": units, "kind": kind, "model": model,
                              "job": job_id, "stage": stage, "project": project,
                              "at": time.time(), "pid": os.getpid()}
        try:
            _save_counts(d)
        except OSError as e:
            # the hold cannot be written, so the cap cannot be enforced: stop
            raise UsageCapExceeded(f"the spend record cannot be written ({e}); paid calls are stopped "
                                   "until it can") from e

    meter = Meter()
    _tier_local.tier = ""
    _tier_local.thoughts = 0
    _tier_local.seen = None
    error = None
    try:
        yield meter  # --- the actual API call happens here, outside the lock ---
    except BaseException as e:
        error = e
        raise
    finally:
        try:
            _settle(rid, kind, units, model, job_id, est_cost, meter, error, stage, project)
        except Exception as e:
            # Never fail a call over its bookkeeping. The hold stays in
            # counters.json and is turned into uncertain spend later, so the
            # money still counts against the cap.
            print(f"[usage] could not settle call {rid} ({type(e).__name__}: {e}); its hold of "
                  f"${est_cost:.4f} stays against the cap", file=sys.stderr)


def _settle(rid, kind, units, model, job_id, held_usd, meter, error, stage, project):
    """Replace the hold with what the call turned out to be."""
    seen = getattr(_tier_local, "seen", None) or {}
    if error is not None and not meter.reported and seen:
        # the provider answered (usage was parsed on this thread) and the
        # caller raised before reporting it: that answer was billed
        meter.tokens(seen.get("prompt"), seen.get("output"), seen.get("cached"))
    # parse_gemini_usage() ran on this thread during the call: pick up the
    # service tier and thinking count it saw (REST callers report tokens only).
    meter.tier = meter.tier or getattr(_tier_local, "tier", "") or ""
    meter.thought_tokens = meter.thought_tokens or getattr(_tier_local, "thoughts", 0) or 0

    if error is None or meter.reported:
        outcome = "ok" if error is None else "failed_after_response"
    elif getattr(meter, "free", False) or isinstance(error, UsageCapExceeded) or answered_with_error(error):
        outcome = "not_billed"
    else:
        outcome = "uncertain"

    cost = held_usd
    # Actual tokens beat the held amount whenever the caller reported them.
    if kind in ("gemini", "claude") and meter.reported:
        cost = token_cost(model, meter.prompt_tokens, meter.output_tokens,
                          meter.cached_tokens if kind == "gemini" else 0,
                          meter.tier if kind == "gemini" else "")
    # Gemini TTS: priced from the audio actually returned (25 tokens/s).
    elif kind == "tts" and (model or "").startswith("gemini") and meter.reported:
        cost = gemini_tts_cost(model, meter.prompt_tokens, meter.output_tokens)
    elif outcome == "ok":
        # succeeded without reporting usage: the model's table estimate (the
        # hold may be larger — it is sized for the worst recent call)
        cost = _est_cost(kind, units, model)

    with _flock():
        d = _load_counts()
        d["reserved"].pop(rid, None)
        job = _job_slot(d, job_id)
        if outcome == "not_billed":
            _save_counts(d)
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(), "rid": rid,
            "job_id": job_id, "stage": stage, "project": project,
            "provider": {"gemini": "gemini", "claude": "anthropic"}.get(kind, "google-tts"),
            "kind": kind, "model": model, "units": units,
            "unit": "chars" if kind == "tts" else "call"}
        if outcome == "uncertain":
            d["uncertain_usd"] = round(d.get("uncertain_usd", 0.0) + held_usd, 6)
            job["uncertain_streak"] = (job.get("uncertain_streak") or 0) + 1
            job["uncertain_at"] = time.time()
            entry.update(est_cost_usd=0.0, uncertain_usd=round(held_usd, 6), outcome="uncertain", metered=False,
                         why=f"{type(error).__name__}: {error}"[:200])
        else:
            job["uncertain_streak"] = 0
            if kind == "claude":
                job["claude_calls"] += units
                d["claude_calls"] += units
            elif kind == "gemini":
                job["gemini_calls"] += units
                d["gemini_calls"] += units
            else:
                job["tts_chars"] += units
                d["tts_chars"] += units
            exact = meter.reported or (kind == "tts" and not (model or "").startswith("gemini"))
            bucket = "confirmed_usd" if exact else "estimated_usd"
            d[bucket] = round(d.get(bucket, 0.0) + cost, 6)
            d["est_cost_usd"] = round(d["est_cost_usd"] + cost, 6)
            if kind != "tts" and meter.reported and units:
                # what this kind of call really costs, for the next hold
                key = _obs_key(kind, model, stage)
                d["observed"][key] = ((d["observed"].get(key) or []) + [round(cost / units, 6)])[-OBSERVED_KEEP:]
            entry.update({
                "est_cost_usd": round(cost, 6),
                "held_usd": round(held_usd, 6),
                "prompt_tokens": meter.prompt_tokens,
                "output_tokens": meter.output_tokens,
                "cached_tokens": meter.cached_tokens,
                "thought_tokens": meter.thought_tokens,
                "service_tier": meter.tier or None,
                "requested_tier": requested_tier() or None,
                "metered": meter.reported,
                "job_totals": {k: job.get(k, 0) for k in _CALL_COUNTERS},
                "daily_totals": {"gemini_calls": d["gemini_calls"],
                                  "claude_calls": d["claude_calls"],
                                  "tts_chars": d["tts_chars"],
                                  "est_cost_usd": d["est_cost_usd"],
                                  "uncertain_usd": d.get("uncertain_usd", 0.0)},
            })
            if error is not None:
                # answered and billed, then the caller failed (bad JSON, a local
                # error): the money was spent, so it is on the books
                entry["failed_after_response"] = f"{type(error).__name__}: {error}"[:200]
        # Money and the ledger line are saved together; only then is the line
        # appended. If the append fails or the process dies here, the line is
        # still in counters.json and is written by a later call.
        d["pending_ledger"].append(entry)
        _save_counts(d)
        if _flush_ledger(d):
            try:
                _save_counts(d)
            except OSError:
                pass        # the line may be appended twice-proofed by its rid next time


def rate_card():
    """What the numbers were priced at, so the UI can show it rather than
    presenting an assumption as a fact."""
    return {
        "defaults": RATES_ARE_DEFAULT,
        # Gemini rates are now Google's published ones (read on this date);
        # 'defaults' only says no env override is set.
        "gemini_rates_published": True,
        "gemini_prices_read": GEMINI_PRICES_READ,
        "tts_per_1k_chars": EST_COST_PER_TTS_1K_CHARS_USD,
        "gemini_per_1m": [
            {"model": p, "input": rin, "output": rout}
            for p, rin, rout in GEMINI_TOKEN_PRICES_PER_1M
        ],
        "claude_per_1m": [
            {"model": p, "input": rin, "output": rout}
            for p, rin, rout in CLAUDE_TOKEN_PRICES_PER_1M
        ],
        # The Gemini numbers are placeholders until someone sets them from a
        # real rate card; the Claude numbers are published list prices. The UI
        # needs to be able to say which is which instead of labelling both
        # "estimated".
        "claude_rates_published": CLAUDE_RATES_ARE_DEFAULT,
    }


def daily_summary():
    with _flock():
        d = _load_counts()
    d["rates"] = rate_card()
    d["reserved_usd"] = round(sum(float(v.get("usd") or 0) for v in (d.get("reserved") or {}).values()), 6)
    d["committed_usd"] = committed_usd(d)       # spent + uncertain + held: what the cap is checked against
    d["cap_usd"] = daily_cap()
    return d
