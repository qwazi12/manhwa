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



def has_unposted_earlier_chapter(item, items):
    """Chapter Sequential Guard: returns (True, earlier_item) if an earlier chapter
    of the same series exists in the queue and has not been posted yet."""
    ser, ch = series_chapter(item.get("project"))
    if not ser or ch is None:
        return False, None
    for other in items:
        if other.get("id") == item.get("id"):
            continue
        o_ser, o_ch = series_chapter(other.get("project"))
        if o_ser == ser and o_ch is not None and o_ch < ch:
            if other.get("status") in ("queued", "posting", "failed", "paused"):
                return True, other
    return False, None

def series_chapter(project):
    """('series-slug', 81.0) from a project id like 'series-slug_81' (versions
    like '_81-v2' count as 81); (None, None) when it isn't a chapter id."""
    import re
    m = re.match(r"^(.*)_(\d+(?:\.\d+)?)(?:-v\d+)?$", project or "")
    return (m.group(1), float(m.group(2))) if m else (None, None)


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
            row = {"id": uuid.uuid4().hex[:10], "project": e["project"], "name": e["name"],
                   "status": "queued", "added_at": now, "updated_at": now, "job": None, "error": None}
            # Owner, 2026-10-07: a series posts in chapter order. A chapter goes
            # in just before any LATER chapter of the same series already waiting
            # (approving ch.81 after ch.93 still posts ch.81 first).
            ser, ch = series_chapter(e["project"])
            at = next((i for i, x in enumerate(d["items"]) if x["status"] == "queued" and ser
                       and series_chapter(x["project"])[0] == ser
                       and (series_chapter(x["project"])[1] or 0) > (ch or 0)), None)
            if at is None:
                d["items"].append(row)
            else:
                d["items"].insert(at, row)
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
        if it["status"] in ("queued", "failed"):
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


def requeue(root, item_id):
    """Scrapper's "back to the line": a failed video goes back in the queue
    (status queued, error cleared) and takes the next free slot."""
    with _lock:
        d = load(root)
        it = _find(d, item_id)
        if not it or it["status"] not in ("failed", "removed"):
            return None
        it.update(status="queued", error=None, job=None, updated_at=time.time())
        _save(root, d)
        return it


def drop_stale_failures(root, latest_export):
    """A failed row for a video that was re-rendered since (the chapter has a
    newer export) is closed, so the chapter isn't in Errors AND Needs review at
    once. latest_export(project) -> the newest export's name or None."""
    n = 0
    with _lock:
        d = load(root)
        for it in d["items"]:
            if it["status"] == "failed":
                newest = latest_export(it["project"])
                if newest and newest != it["name"]:
                    it.update(status="removed", error=(it.get("error") or "") + " [replaced by a new render]",
                              updated_at=time.time())
                    n += 1
        if n:
            _save(root, d)
    return n


def ensure_posting(root, project, name, job, day=None):
    """Every publish goes through a queue row (owner, 2026-10-04: a video posted
    from Video review stayed 'queued' and the schedule tried it again). Marks
    the video's open row as posting, or creates one."""
    with _lock:
        d = load(root)
        it = next((x for x in d["items"] if (x["project"], x["name"]) == (project, name)
                   and x["status"] in ("queued", "failed", "posting")), None)
        now = time.time()
        if it is None:
            it = {"id": uuid.uuid4().hex[:10], "project": project, "name": name, "added_at": now}
            d["items"].append(it)
        it.update(status="posting", job=job, error=None, updated_at=now)
        if day:
            it["posted_day"] = day
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
            if it["status"] not in ("posting", "queued", "failed"):
                continue
            try:
                rec = publish_status(it["project"], it["name"]) or {}
            except Exception:
                continue
            st = rec.get("status")
            if it["status"] != "posting" and st != "published":
                continue            # only a confirmed post settles a waiting row
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


# ------------------------------------------------ posting schedule (step 5)
# Owner, 2026-10-04: built but OFF by default; when on, each daily time slot
# (operator timezone, Eastern by default) posts the next queued video, never
# more than `per_channel_per_day` posts to any one channel in a local day.

def _local(now, tz):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(now, ZoneInfo(tz or "America/New_York"))


def decide(sched, d, now, targets_of):
    """What the schedule should do right now. Pure: no writes.

    sched: {"enabled", "times": ["12:00", ...], "tz", "per_channel_per_day"}
    d: the queue file; targets_of(item) -> its channel ids.
    Returns {"action": "off"|"wait"|"post"|"skip", "slot", "item", "why", "day"}.
    """
    if not (sched or {}).get("enabled"):
        return {"action": "off", "why": "the posting schedule is off"}
    loc = _local(now, sched.get("tz"))
    day, hm = loc.strftime("%Y-%m-%d"), loc.strftime("%H:%M")
    done = set((d.get("slots_done") or {}).get(day, {}))
    # slots that had already passed when the schedule was switched on don't
    # fire that day (owner, 2026-10-04: switching on at 15:51 fired 11:00)
    since = ""
    if sched.get("enabled_at"):
        on = _local(sched["enabled_at"], sched.get("tz"))
        if on.strftime("%Y-%m-%d") == day:
            since = on.strftime("%H:%M")
    due = [t for t in sorted(sched.get("times") or []) if t <= hm and t not in done and t >= since]
    if not due:
        return {"action": "wait", "day": day, "why": "no posting time is due"}
    slot = due[0]
    cap = int(sched.get("per_channel_per_day") or 1)
    used = {}
    for x in d["items"]:
        if x["status"] in ("posted", "posting") and x.get("posted_day") == day:
            for t in targets_of(x):
                used[t] = used.get(t, 0) + 1
    queued = [x for x in d["items"] if x["status"] == "queued"]
    if not queued:
        return {"action": "skip", "slot": slot, "day": day, "why": "the queue is empty"}
    for x in queued:
        # Chapter Sequential Guard: Chapter 25 cannot be posted before Chapter 23 or 24
        blocked, earlier = has_unposted_earlier_chapter(x, d["items"])
        if blocked:
            continue
        tg = targets_of(x)
        if tg and all(used.get(t, 0) < cap for t in tg):
            return {"action": "post", "slot": slot, "day": day, "item": x,
                    "why": f"{slot} slot: next in the queue"}
    return {"action": "skip", "slot": slot, "day": day,
            "why": f"every queued video would go over {cap} post(s) per channel today"}


def slot_done(root, day, slot, result):
    """Record that a slot was used (posted or skipped) so it never fires twice."""
    with _lock:
        d = load(root)
        sd = d.setdefault("slots_done", {})
        sd.setdefault(day, {})[slot] = result
        for k in sorted(sd)[:-7]:          # keep a week
            sd.pop(k, None)
        _save(root, d)


def next_slot(sched, now):
    """The next posting time as a local 'Mon 12:00' string, or None."""
    from datetime import timedelta
    if not (sched or {}).get("enabled") or not sched.get("times"):
        return None
    loc = _local(now, sched.get("tz"))
    for add in (0, 1):
        day = loc + timedelta(days=add)
        for t in sorted(sched["times"]):
            if add or t > loc.strftime("%H:%M"):
                return day.strftime("%a ") + t
    return None


# ------------------------------------------------ SocialPilot-style tools
# Owner, 2026-10-06: the Posting schedule should work like Scrapper's
# SocialPilot — each video's planned post time, Mix & Shuffle with Undo,
# "Post next".

def planned(sched, d, now, targets_of, days=21):
    """{item_id: {"at": epoch, "label": "Tue 11:00"}} for every queued video:
    the slot the schedule will give it, in queue order, honouring the
    per-channel-per-day cap and slots already used. Same rules as decide()."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    if not (sched or {}).get("enabled") or not sched.get("times"):
        return {}
    tz = ZoneInfo(sched.get("tz") or "America/New_York")
    loc = _local(now, sched.get("tz"))
    cap = int(sched.get("per_channel_per_day") or 1)
    used = {}
    for x in d["items"]:
        if x["status"] in ("posted", "posting") and x.get("posted_day"):
            for t in targets_of(x):
                used[(x["posted_day"], t)] = used.get((x["posted_day"], t), 0) + 1
    since = ""
    if sched.get("enabled_at"):
        on = _local(sched["enabled_at"], sched.get("tz"))
        if on.strftime("%Y-%m-%d") == loc.strftime("%Y-%m-%d"):
            since = on.strftime("%H:%M")
    remaining = [x for x in d["items"] if x["status"] == "queued"]
    tg_cache = {x["id"]: targets_of(x) for x in remaining}
    out = {}
    for add in range(days):
        day = (loc + timedelta(days=add)).date()
        dstr = day.strftime("%Y-%m-%d")
        done = set((d.get("slots_done") or {}).get(dstr, {}))
        for t in sorted(sched["times"]):
            if t in done or (add == 0 and t < since):
                continue
            for x in remaining:
                # Chapter Sequential Guard
                blocked, _ = has_unposted_earlier_chapter(x, remaining)
                if blocked:
                    continue
                tg = tg_cache[x["id"]]
                if tg and all(used.get((dstr, c), 0) < cap for c in tg):
                    h, m = (int(v) for v in t.split(":"))
                    at = datetime(day.year, day.month, day.day, h, m, tzinfo=tz).timestamp()
                    hh = h % 12 or 12
                    nice = f"{day.strftime('%a, %b')} {day.day} at {hh}:{m:02d} {'AM' if h < 12 else 'PM'} ET"
                    out[x["id"]] = {"at": max(at, now), "label": nice if at >= now
                                    else f"at the next check ({t} slot)"}
                    for c in tg:
                        used[(dstr, c)] = used.get((dstr, c), 0) + 1
                    remaining.remove(x)
                    break
            if not remaining:
                return out
    return out


def _set_order(d, ids):
    queued = [x for x in d["items"] if x["status"] == "queued"]
    by = {x["id"]: x for x in queued}
    new = [by[i] for i in ids if i in by] + [x for x in queued if x["id"] not in ids]
    d["items"] = new + [x for x in d["items"] if x["status"] != "queued"]


def undo_label(d):
    return d.get("prev_label") if d.get("prev_order") else None


def shuffle(root, mode, series_of, rnd=None):
    """Mix & Shuffle the queued videos (Scrapper's modes, by SERIES instead of
    channel): round_robin = one of each series in turn, each series in chapter
    order; by_series = each series together; random = shuffled, each series
    still in chapter order. Saves the previous order for Undo."""
    import random
    r = rnd or random.Random()
    with _lock:
        d = load(root)
        queued = [x for x in d["items"] if x["status"] == "queued"]
        d["prev_order"] = [x["id"] for x in queued]
        d["prev_label"] = "Mix: " + mode.replace("_", "-")
        groups = {}
        for x in queued:
            groups.setdefault(series_of(x), []).append(x)
        keys = list(groups)
        r.shuffle(keys)
        if mode == "by_series":
            ids = [x["id"] for k in keys for x in groups[k]]
        elif mode == "random":
            slots = [series_of(x) for x in queued]
            r.shuffle(slots)
            it = {k: iter(v) for k, v in groups.items()}
            ids = [next(it[k])["id"] for k in slots]
        elif mode == "round_robin":
            ids, i = [], 0
            while len(ids) < len(queued):
                for k in keys:
                    if i < len(groups[k]):
                        ids.append(groups[k][i]["id"])
                i += 1
        else:
            raise ValueError("mode must be round_robin, by_series or random")
        _set_order(d, ids)
        _save(root, d)
        return ids


def undo_order(root):
    with _lock:
        d = load(root)
        prev = d.get("prev_order")
        if not prev:
            raise ValueError("nothing to undo")
        d["prev_order"] = [x["id"] for x in d["items"] if x["status"] == "queued"]
        d["prev_label"] = "Redo: " + (d.get("prev_label") or "last change").replace("Redo: ", "")
        _set_order(d, prev)
        _save(root, d)
        return prev


def to_top(root, item_id, label=""):
    """⏫ Post next: first in line, so it takes the next posting time."""
    with _lock:
        d = load(root)
        queued = [x["id"] for x in d["items"] if x["status"] == "queued"]
        if item_id not in queued:
            raise ValueError("only a queued video can be moved")
        d["prev_order"] = queued
        d["prev_label"] = "Post next" + (f": {label}" if label else "")
        _set_order(d, [item_id] + [i for i in queued if i != item_id])
        _save(root, d)
