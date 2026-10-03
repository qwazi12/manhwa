"""Archive a chapter once it has been reviewed and published.

Owner request (2026-10-03): "archive after review and publishing". A project
whose video went out (any publish record with status "published") is moved to
Archived: hidden from the main Projects list, listed under the Archived filter
with the date its folder will be deleted. ARCHIVE_DAYS later the folder is
deleted to free the volume, unless the owner pressed Keep. Unarchive puts it
back in the main list and cancels the delete.

Nothing is deleted without a visible date first, the open project is never
deleted, and the autopilot ledger keeps the chapter marked as made, so an
archived-then-deleted chapter is never re-ingested.
"""

import json
import math
import os
import time

ARCHIVE_NAME = "archive.json"
ARCHIVE_DAYS = 14
DAY = 86400


def _path(pdir):
    return os.path.join(pdir, ARCHIVE_NAME)


def read(pdir):
    try:
        with open(_path(pdir), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(pdir, rec):
    with open(_path(pdir), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)


def archive(pdir, reason, now=None, days=ARCHIVE_DAYS):
    now = time.time() if now is None else now
    rec = {"archived_at": now, "reason": reason, "keep": False,
           "delete_after": now + days * DAY}
    _write(pdir, rec)
    return rec


def keep(pdir):
    rec = read(pdir)
    if rec is None:
        raise ValueError("project is not archived")
    rec["keep"] = True
    rec["delete_after"] = None
    _write(pdir, rec)
    return rec


def unarchive(pdir):
    try:
        os.remove(_path(pdir))
    except FileNotFoundError:
        pass
    return None


def is_published(publishes):
    """publishes = server.load_publishes(pdir): {export_name: record}."""
    for rec in (publishes or {}).values():
        for r in rec.get("results") or []:
            if r.get("status") == "published":
                return True
    return False


def view(pdir, now=None):
    """What the Projects list shows for this project's archive state."""
    now = time.time() if now is None else now
    rec = read(pdir)
    if rec is None:
        return None
    left = None
    if rec.get("delete_after") and not rec.get("keep"):
        left = max(0, math.ceil((rec["delete_after"] - now) / DAY))
    return {"archived_at": rec.get("archived_at"), "reason": rec.get("reason"),
            "keep": bool(rec.get("keep")), "delete_after": rec.get("delete_after"),
            "days_left": left}


def due(pdir, now=None):
    now = time.time() if now is None else now
    rec = read(pdir)
    return bool(rec and not rec.get("keep") and rec.get("delete_after")
                and now >= rec["delete_after"])
