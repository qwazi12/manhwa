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


GEMINI_TOKEN_PRICES_PER_1M = [
    # (model prefix, input $/1M tokens, output $/1M tokens)
    ("gemini-3.1-pro", _price("PRICE_PRO_IN_PER_1M", 1.25),
                       _price("PRICE_PRO_OUT_PER_1M", 10.0)),
    ("gemini-3-pro",   _price("PRICE_PRO_IN_PER_1M", 1.25),
                       _price("PRICE_PRO_OUT_PER_1M", 10.0)),
    ("gemini-embedding", _price("PRICE_EMBED_IN_PER_1M", 0.15), 0.0),
    ("gemini-3.5-flash", _price("PRICE_FLASH_IN_PER_1M", 0.30),
                         _price("PRICE_FLASH_OUT_PER_1M", 2.50)),
    ("gemini-3.1-flash", _price("PRICE_FLASH_IN_PER_1M", 0.30),
                         _price("PRICE_FLASH_OUT_PER_1M", 2.50)),
]

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
            return rin, rout
    return (_price("PRICE_FLASH_IN_PER_1M", 0.30),
            _price("PRICE_FLASH_OUT_PER_1M", 2.50))


def token_cost(model, prompt_tokens, output_tokens):
    rin, rout = _token_rates(model)
    return (prompt_tokens / 1e6) * rin + (output_tokens / 1e6) * rout


class Meter:
    """Handed to the caller by gate() so it can report what the API actually
    charged for. Without a report the old flat per-call estimate stands, so
    call sites that have not been updated keep working unchanged."""

    __slots__ = ("prompt_tokens", "output_tokens", "cached_tokens", "reported")

    def __init__(self):
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.reported = False

    def tokens(self, prompt=0, output=0, cached=0):
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
            return self.tokens(
                getattr(um, "prompt_token_count", 0) or 0,
                getattr(um, "candidates_token_count", 0) or 0,
                getattr(um, "cached_content_token_count", 0) or 0)
        except Exception:
            return self


def _est_cost(kind, units, model=""):
    if kind == "gemini":
        return units * _gemini_call_cost(model)
    if kind == "claude":
        return units * EST_COST_PER_CLAUDE_CALL_USD
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
        if d["est_cost_usd"] + est_cost > MAX_DAILY_SPEND_USD:
            raise UsageCapExceeded(
                f"MAX_DAILY_SPEND_USD=${MAX_DAILY_SPEND_USD} would be exceeded "
                f"today (${d['est_cost_usd']:.4f} + ${est_cost:.4f} est.)")

    meter = Meter()
    yield meter  # --- the actual API call happens here, outside the lock ---

    # Actual tokens beat the flat per-call guess whenever the caller reported
    # them. TTS is already exact (it is billed per character).
    if kind in ("gemini", "claude") and meter.reported:
        est_cost = token_cost(model, meter.prompt_tokens, meter.output_tokens)

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
