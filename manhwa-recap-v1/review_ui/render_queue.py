"""Render queue (owner, 2026-10-05: "like in scrapper could I multi render,
queue? And get progress bars?").

Chapters render ONE at a time — a render reads the open chapter clip by clip
(server._render_lock) — so "render several" means a queue, worked through in
order by server._rq_worker. The list lives on the volume next to the
projects, so it survives a restart; a chapter that was mid-render when the
server restarted goes back to waiting (at most MAX_ATTEMPTS times).

Item: {id, project, status, keep_voice, added, started, ended, job, error, attempts}
status: waiting -> rendering -> done | error
"""
import json
import os
import threading
import time
import uuid

FILE = "_render_queue.json"
MAX_ATTEMPTS = 2
KEEP_FINISHED = 40          # finished rows kept for the list, newest
_LOCK = threading.Lock()


def _path(root):
    return os.path.join(root, FILE)


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d.get("items"), list) else {"items": []}
    except (OSError, ValueError, AttributeError):
        return {"items": []}


def _save(root, d):
    fin = [i for i in d["items"] if i["status"] in ("done", "error")]
    if len(fin) > KEEP_FINISHED:
        drop = {i["id"] for i in sorted(fin, key=lambda i: i.get("ended") or 0)[:len(fin) - KEEP_FINISHED]}
        d["items"] = [i for i in d["items"] if i["id"] not in drop]
    os.makedirs(root, exist_ok=True)
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _path(root))


def add(root, project, keep_voice=True):
    """Queue one chapter. Returns (item, None) or (None, reason)."""
    with _LOCK:
        d = load(root)
        if any(i["project"] == project and i["status"] in ("waiting", "rendering") for i in d["items"]):
            return None, "already in the render queue"
        it = {"id": uuid.uuid4().hex[:10], "project": project, "status": "waiting",
              "keep_voice": bool(keep_voice), "added": time.time(), "started": None,
              "ended": None, "job": None, "error": None, "attempts": 0}
        d["items"].append(it)
        _save(root, d)
        return it, None


def update(root, item_id, **f):
    with _LOCK:
        d = load(root)
        for i in d["items"]:
            if i["id"] == item_id:
                i.update(f)
                _save(root, d)
                return i
    return None


def remove(root, item_id):
    """Take a WAITING chapter out (a rendering one is stopped with its job's Stop)."""
    with _LOCK:
        d = load(root)
        it = next((i for i in d["items"] if i["id"] == item_id), None)
        if not it:
            return False, "not in the queue"
        if it["status"] == "rendering":
            return False, "it's rendering now — use Stop on the render instead"
        d["items"] = [i for i in d["items"] if i["id"] != item_id]
        _save(root, d)
        return True, None


def clear_finished(root):
    with _LOCK:
        d = load(root)
        n = len(d["items"])
        d["items"] = [i for i in d["items"] if i["status"] in ("waiting", "rendering")]
        _save(root, d)
        return n - len(d["items"])


def next_waiting(root):
    return next((i for i in load(root)["items"] if i["status"] == "waiting"), None)


def waiting(root):
    return [i for i in load(root)["items"] if i["status"] == "waiting"]


def position(root, project):
    """{'item', 'place' (1 = next), 'status'} for a chapter waiting or rendering, else None."""
    w = 0
    for i in load(root)["items"]:
        if i["status"] == "waiting":
            w += 1
        if i["project"] == project and i["status"] in ("waiting", "rendering"):
            return {"item": i["id"], "status": i["status"], "place": w if i["status"] == "waiting" else 0}
    return None


def recover(root, job_alive):
    """After a restart: a row left 'rendering' whose job is no longer alive
    goes back to waiting (bounded), or fails with the reason."""
    with _LOCK:
        d = load(root)
        changed = False
        for i in d["items"]:
            if i["status"] == "rendering" and not job_alive(i.get("job")):
                changed = True
                if i.get("attempts", 0) < MAX_ATTEMPTS:
                    i.update(status="waiting", job=None)
                else:
                    i.update(status="error", ended=time.time(),
                             error=i.get("error") or "interrupted (server restarted) too many times")
        if changed:
            _save(root, d)
