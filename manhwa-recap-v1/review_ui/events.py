"""One live activity feed for the studio (Logs → Live).

Owner, 2026-10-04: make Logs as clean as Scrapper's — one line per event,
`time [kind] message`, coloured by level. Everything that does work writes
here: ingest (stage changes, image check, research, finish with cost),
autopilot decisions, approve/render/export, research jobs, archive, scheduler.

Stored as JSON lines on the volume (`projects/_events.jsonl`), newest
MAX_LINES kept. Readers ask for lines after an id they already have, every few
seconds — plain polling, no open connections (Scrapper's site-wide outage came
from too many held-open connections).
"""

import json
import os
import threading
import time

NAME = "_events.jsonl"
MAX_LINES = 5000
_lock = threading.Lock()
_writes = [0]
_last_id = [0]
LEVELS = ("info", "ok", "warn", "error")


def _path():
    import ingest
    return os.path.join(ingest.PROJECTS, NAME)


def emit(kind, msg, level="info", **fields):
    """Append one event. Never raises: the feed must not break the work."""
    try:
        with _lock:
            eid = max(time.time_ns() // 1000, _last_id[0] + 1)
            _last_id[0] = eid
            rec = {"id": eid, "ts": time.time(), "level": level if level in LEVELS else "info",
                   "kind": kind, "msg": str(msg)[:400]}
            rec.update({k: v for k, v in fields.items() if v is not None})
            path = _path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            _writes[0] += 1
            if _writes[0] % 200 == 0:
                _trim(path)
        return rec
    except Exception:
        return None


def _trim(path):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LINES * 1.2:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LINES:])
            os.replace(tmp, path)
    except OSError:
        pass


def since(after=0, limit=300, kind=None):
    """Events with id > after, oldest first, at most `limit` (the newest ones)."""
    out = []
    try:
        with open(_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("id", 0) > after and (not kind or e.get("kind") == kind):
                    out.append(e)
    except FileNotFoundError:
        pass
    return out[-limit:]
