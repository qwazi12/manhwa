"""📺 Publishing Studio's posting queue (owner plan, 2026-10-04, step 4).

An ordered list of approved exports waiting to be posted. Posting itself is
the existing publish job (`/api/publishing/publish`): the queue only remembers
WHAT goes next, for which channels, and what happened. Until the posting
schedule (step 5) exists and is switched on, every post is a button press.

A queued export is exempt from the 7-day export cleanup until it is posted or
removed, or a later post would find its file gone (Scrapper's "exempt from
retention" lesson).

One JSON file on the volume: `projects/_post_queue.json`.
"""

import json
import os
import threading
import time
import uuid

NAME = "_post_queue.json"
ACTIVE = ("queued", "posting")
_lock = threading.Lock()


def _path(root):
    return os.path.join(root, NAME)


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("items"), list):
            return d
    except (OSError, ValueError, AttributeError):
        pass
    return {"items": []}


def _save(root, d):
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _path(root))


def _find(d, item_id):
    return next((x for x in d["items"] if x["id"] == item_id), None)


def add(root, entries):
    """Queue exports. Idempotent: one already queued, posting or posted is skipped.
    Returns (added, skipped) lists of {project, name}."""
    added, skipped = [], []
    with _lock:
        d = load(root)
        for e in entries:
            key = (e["project"], e["name"])
            if any((x["project"], x["name"]) == key and x["status"] in ACTIVE + ("posted",)
                   for x in d["items"]):
                skipped.append({"project": e["project"], "name": e["name"]})
                continue
            now = time.time()
            d["items"].append({"id": uuid.uuid4().hex[:10], "project": e["project"], "name": e["name"],
                               "status": "queued", "added_at": now, "updated_at": now,
                               "job": None, "error": None})
            added.append({"project": e["project"], "name": e["name"]})
        _save(root, d)
    return added, skipped


def remove(root, item_id):
    """Take an item off the queue (kept in the file as 'removed' for the record)."""
    with _lock:
        d = load(root)
        it = _find(d, item_id)
        if it is None:
            raise KeyError(item_id)
        if it["status"] == "posting":
            raise ValueError("this video is being posted right now — stop it from the jobs bar")
        if it["status"] == "queued":
            it.update(status="removed", updated_at=time.time())
            _save(root, d)
        return it


def reorder(root, ids):
    """Put queued items in the given order; anything not listed keeps its place after them."""
    with _lock:
        d = load(root)
        queued = [x for x in d["items"] if x["status"] == "queued"]
        pos = {i: n for n, i in enumerate(ids)}
        was = {x["id"]: n for n, x in enumerate(queued)}
        queued.sort(key=lambda x: pos.get(x["id"], len(ids) + was[x["id"]]))
        rest = [x for x in d["items"] if x["status"] != "queued"]
        d["items"] = queued + rest
        _save(root, d)
        return [x["id"] for x in queued]


def mark(root, item_id, **fields):
    with _lock:
        d = load(root)
        it = _find(d, item_id)
        if it is None:
            raise KeyError(item_id)
        it.update(fields, updated_at=time.time())
        _save(root, d)
        return it


def protected(root):
    """(project, name) pairs the export cleanup must not delete."""
    return {(x["project"], x["name"]) for x in load(root)["items"] if x["status"] in ACTIVE}


def sync(root, publish_status):
    """Settle 'posting' items from the publish record.
    publish_status(project, name) -> the publishes.json record or None."""
    changed = False
    with _lock:
        d = load(root)
        for it in d["items"]:
            if it["status"] != "posting":
                continue
            try:
                rec = publish_status(it["project"], it["name"]) or {}
            except Exception:
                continue
            st = rec.get("status")
            if st == "published":
                it.update(status="posted", posted_at=rec.get("ended_at") or time.time(),
                          urls=[r.get("url") for r in rec.get("results") or [] if r.get("url")])
            elif st in ("failed", "cancelled", "partial"):
                it.update(status="failed" if st != "partial" else "posted",
                          error=rec.get("error") or "; ".join(
                              r.get("error") or "" for r in rec.get("results") or [] if r.get("error")) or st)
            else:
                continue
            it["updated_at"] = time.time()
            changed = True
        if changed:
            _save(root, d)
    return d
