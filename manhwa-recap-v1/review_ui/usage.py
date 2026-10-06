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
import threading
import time
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
    ("gemini-3.5-flash",  _envf("EST_COST_FLASH35_CALL_USD", 0.012)),
    ("gemini-3.8-flash",  _envf("EST_COST_FLASH38_CALL_USD", 0.005)),
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


def carry(fn):
    """Wrap fn so it runs under the CALLER's job id in a worker thread.

    The job id is thread-local; ThreadPoolExecutor workers start without it,
    so every voice line recorded 4-at-a-time landed in one shared "unknown"
    job. Its per-job cap (MAX_TTS_CHARS_PER_JOB) then filled up across a day's
    chapters and paused every new one as if the daily budget were spent
    (owner, 2026-10-05: "why the works keep pausing")."""
    jid = getattr(_local, "job_id", None)

    def run(*a, **k):
        prev = getattr(_local, "job_id", None)
        if jid:
            _local.job_id = jid
        try:
            return fn(*a, **k)
        finally:
            _local.job_id = prev
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
        d = dict(_zero_counts(), date=_today(), est_cost_usd=0.0,
                 jobs={}, lifetime=life)
    d.setdefault("jobs", {})
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
        return out
    except Exception:
        return {}


class Meter:
    """Handed to the caller by gate() so it can report what the API actually
    charged for. Without a report the old flat per-call estimate stands, so
    call sites that have not been updated keep working unchanged."""

    __slots__ = ("prompt_tokens", "output_tokens", "cached_tokens", "reported",
                 "thought_tokens", "tier")

    def __init__(self):
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.thought_tokens = 0
        self.tier = ""
        self.reported = False

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


@contextlib.contextmanager
def gate(kind, units, model=""):
    """kind: 'gemini' or 'claude' (units = number of calls, usually 1), or
    'tts' (units = character count). Raises UsageCapExceeded BEFORE the wrapped
    call if it would breach a per-job or daily cap; commits usage + logs
    only if the wrapped call completes without raising."""
    job_id = get_job_id()
    est_cost = _est_cost(kind, units, model)

    with _flock():
        d = _load_counts()
        job = _job_slot(d, job_id)
        if kind == "claude":
            if job["claude_calls"] + units > MAX_CLAUDE_CALLS_PER_JOB:
                raise UsageCapExceeded(
                    f"MAX_CLAUDE_CALLS_PER_JOB={MAX_CLAUDE_CALLS_PER_JOB} would be "
                    f"exceeded for job '{job_id}' ({job['claude_calls']} + {units})")
            if d["claude_calls"] + units > MAX_DAILY_CLAUDE_CALLS:
                raise UsageCapExceeded(
                    f"MAX_DAILY_CLAUDE_CALLS={MAX_DAILY_CLAUDE_CALLS} would be "
                    f"exceeded today ({d['claude_calls']} + {units})")
        elif kind == "gemini":
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
        cap = daily_cap()
        if d["est_cost_usd"] + est_cost > cap:
            raise UsageCapExceeded(
                f"the daily spend limit of ${cap:g} would be exceeded "
                f"today (${d['est_cost_usd']:.4f} + ${est_cost:.4f} est.)")

    meter = Meter()
    _tier_local.tier = ""
    _tier_local.thoughts = 0
    yield meter  # --- the actual API call happens here, outside the lock ---
    # parse_gemini_usage() ran on this thread during the call: pick up the
    # service tier and thinking count it saw (REST callers report tokens only).
    meter.tier = meter.tier or getattr(_tier_local, "tier", "") or ""
    meter.thought_tokens = meter.thought_tokens or getattr(_tier_local, "thoughts", 0) or 0

    # Actual tokens beat the flat per-call guess whenever the caller reported
    # them. TTS is already exact (it is billed per character).
    if kind in ("gemini", "claude") and meter.reported:
        est_cost = token_cost(model, meter.prompt_tokens, meter.output_tokens,
                              meter.cached_tokens if kind == "gemini" else 0,
                              meter.tier if kind == "gemini" else "")
    # Gemini TTS: priced from the audio actually returned (25 tokens/s).
    if kind == "tts" and (model or "").startswith("gemini") and meter.reported:
        est_cost = gemini_tts_cost(model, meter.prompt_tokens, meter.output_tokens)

    with _flock():
        d = _load_counts()
        job = _job_slot(d, job_id)
        if kind == "claude":
            job["claude_calls"] += units
            d["claude_calls"] += units
        elif kind == "gemini":
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
            "provider": {"gemini": "gemini", "claude": "anthropic"}.get(
                kind, "google-tts"),
            "kind": kind, "model": model, "units": units,
            "unit": "chars" if kind == "tts" else "call",
            "est_cost_usd": round(est_cost, 6),
            "prompt_tokens": meter.prompt_tokens,
            "output_tokens": meter.output_tokens,
            "cached_tokens": meter.cached_tokens,
            "thought_tokens": meter.thought_tokens,
            "service_tier": meter.tier or None,
            "metered": meter.reported,
            "job_totals": dict(job),
            "daily_totals": {"gemini_calls": d["gemini_calls"],
                              "claude_calls": d["claude_calls"],
                              "tts_chars": d["tts_chars"],
                              "est_cost_usd": d["est_cost_usd"]},
        })


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
    return d
