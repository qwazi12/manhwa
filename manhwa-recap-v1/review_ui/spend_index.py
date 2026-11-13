"""Running totals from the usage log, read incrementally.

The log (`_usage/calls.log.jsonl`) is append-only and was 40 MB / 85,000 lines
on 2026-11-13. The Logs and Spend pages used to re-parse ALL of it whenever it
changed — after every paid call — and autopilot's "spent today" re-read it every
30 seconds. This index remembers how far it has read and parses only the lines
appended since, so the cost of a refresh no longer grows with history.

Totals (all US dollars, estimated from real token counts — see usage.py):
  by_job[job_id]                 lifetime per job
  by_day[ET day]                 per day (America/New_York, as the caps are)
  by_kind[(month, provider)]     per month and provider
  by_stage[(month, stage)]       per month and pipeline stage ("unlabelled"
                                 for lines written before 2026-11-13)
  by_job_day[(job_id, ET day)]   what autopilot's daily budget is checked against
  by_project[project]            lifetime per chapter (labelled lines only)
  thinking[(month, stage)]       thinking tokens, to watch the largest cost driver
  tiers[(month, requested, served)]  how often Flex was asked for and given
"""
import json
import os
import threading
from datetime import datetime

_lock = threading.Lock()


def _blank():
    return {"offset": 0, "ident": None, "lines": 0,
            "by_job": {}, "by_day": {}, "by_kind": {}, "by_stage": {},
            "by_job_day": {}, "by_project": {}, "thinking": {}, "tiers": {}}


_STATE = {}          # path -> state


def _add(d, k, v):
    d[k] = d.get(k, 0.0) + v


def _apply(st, e, et_day):
    c = e.get("est_cost_usd") or 0.0
    jid = e.get("job_id") or "?"
    _add(st["by_job"], jid, c)
    if e.get("project"):
        _add(st["by_project"], e["project"], c)
    try:
        day = et_day(datetime.fromisoformat(e["ts"]).timestamp())
    except Exception:
        return
    month = day[:7]
    stage = e.get("stage") or "unlabelled"
    _add(st["by_day"], day, c)
    _add(st["by_kind"], (month, e.get("provider") or e.get("kind") or "?"), c)
    _add(st["by_stage"], (month, stage), c)
    _add(st["by_job_day"], (jid, day), c)
    if e.get("thought_tokens"):
        _add(st["thinking"], (month, stage), e["thought_tokens"])
    if e.get("requested_tier") or e.get("service_tier"):
        _add(st["tiers"], (month, e.get("requested_tier") or "", e.get("service_tier") or ""), 1)


def refresh(path, et_day):
    """Read whatever was appended to `path` since the last call and return the
    totals. A log that shrank or was replaced is read again from the start."""
    with _lock:
        st = _STATE.setdefault(path, _blank())
        try:
            s = os.stat(path)
        except OSError:
            return st
        ident = (s.st_dev, s.st_ino)
        if st["ident"] != ident or s.st_size < st["offset"]:
            st = _STATE[path] = _blank()
            st["ident"] = ident
        if s.st_size == st["offset"]:
            return st
        with open(path, "rb") as f:
            f.seek(st["offset"])
            chunk = f.read(s.st_size - st["offset"])
        end = chunk.rfind(b"\n")
        if end < 0:
            return st                     # a line is still being written
        for raw in chunk[:end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            st["lines"] += 1
            _apply(st, e, et_day)
        st["offset"] += end + 1
        return st


def tail(path, n, block=64 * 1024):
    """The last n parsed lines of the log (oldest first) without reading the
    whole file."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    out, data, pos = [], b"", size
    with open(path, "rb") as f:
        while pos > 0 and data.count(b"\n") <= n:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.split(b"\n")
    if pos > 0:
        lines = lines[1:]                 # first piece may be half a line
    for raw in [ln for ln in lines if ln.strip()][-n:]:
        try:
            out.append(json.loads(raw))
        except ValueError:
            pass
    return out
