"""Storage retention: what could be removed, and why each thing that cannot
is kept (2026-11-14). Dry run by default; applying moves things to the trash
(reversible) — only the trash purge deletes, and only when asked by name.

Never listed, whatever its age — the things a later release or an audit needs:
  * any export (.mp4) that is approved, queued, being posted, or whose only
    verified copy is the local one;
  * the newest posted compilation of each series (the base of the next
    cumulative release) and every unposted compilation;
  * evidence and records: exports/_qa, release_evidence.json, critique.json,
    validation.json, reviews.json, publishes.json, publish.json, drive.json,
    project.json, script.txt, segments.json, packaging_revisions.jsonl.

Candidates (each with the rule that makes it one):
  work_files   crops/ pages/ clips/ of a chapter that is posted (itself or
               inside a posted compilation) for WORK_DAYS — they are inputs to
               a render that will not happen again
  archived     the local .mp4 of a posted chapter that has a verified Drive
               copy WITH its sha256 on record, for ARCHIVE_DAYS, and is not the
               newest posted compilation
  revisions    <root>/_revisions entries older than REVISION_DAYS
  tts_cache    <root>/_ttscache entries older than CACHE_DAYS
  split_lab    <root>/_splitlab entries older than CACHE_DAYS
  trash        <root>/_trash entries older than TRASH_DAYS (purge = deletion)

Owner sign-off: these windows are defaults proposed in the audit, not yet
confirmed; nothing runs on a schedule."""
import json
import os
import shutil
import time

DEFAULTS = {"WORK_DAYS": 14, "ARCHIVE_DAYS": 14, "REVISION_DAYS": 30, "CACHE_DAYS": 30, "TRASH_DAYS": 30}
WORK_DIRS = ("crops", "pages", "clips")
ROOT_DIRS = {"revisions": ("_revisions", "REVISION_DAYS"), "tts_cache": ("_ttscache", "CACHE_DAYS"),
             "split_lab": ("_splitlab", "CACHE_DAYS"), "trash": ("_trash", "TRASH_DAYS")}
CATEGORIES = ("work_files", "archived", "revisions", "tts_cache", "split_lab", "trash")
MOVED = "retention"


def windows(environ=None):
    env = os.environ if environ is None else environ
    out = {}
    for k, v in DEFAULTS.items():
        try:
            out[k] = max(1, int(env.get("RETENTION_" + k, v)))
        except (TypeError, ValueError):
            out[k] = v
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for base, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(base, fn))
            except OSError:
                pass
    return total


def _newest(path):
    """Newest modification time inside a folder (its own when empty)."""
    newest = os.path.getmtime(path)
    if os.path.isdir(path):
        for base, _dirs, files in os.walk(path):
            for fn in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(base, fn)))
                except OSError:
                    pass
    return newest


def _trashed_at(path):
    """When this entry went into the trash: the time in its name
    ("<project>__<epoch>" from the studio, "retention-<UTC stamp>" from here),
    else the entry's own modification time — never the age of the files
    inside it, which are as old as before they were deleted."""
    import calendar
    import re
    name = os.path.basename(path.rstrip("/"))
    m = re.search(r"__(\d{9,11})$", name)
    if m:
        return float(m.group(1))
    m = re.match(r"retention-(\d{8}T\d{6}Z)$", name)
    if m:
        return float(calendar.timegm(time.strptime(m.group(1), "%Y%m%dT%H%M%SZ")))
    return os.path.getmtime(path)


def _posted_at(pdir):
    """{export name: time it was published} for this project."""
    out = {}
    for name, rec in _read(os.path.join(pdir, "publishes.json")).items():
        ok = [r for r in (rec or {}).get("results") or [] if isinstance(r, dict) and r.get("status") == "published"]
        if ok:
            out[name] = max(float(r.get("published_at") or 0) for r in ok) or float((rec or {}).get("ended_at") or 0)
    return out


def _busy(pdir, name, queued):
    """Why this export must stay exactly where it is, or None."""
    rev = (_read(os.path.join(pdir, "reviews.json")).get(name) or {})
    pub = (_read(os.path.join(pdir, "publishes.json")).get(name) or {})
    if (os.path.basename(pdir), name) in queued:
        return "in the posting queue"
    if pub.get("status") == "in_progress" or any(r.get("status") in ("submitted", "unconfirmed")
                                                 for r in pub.get("results") or [] if isinstance(r, dict)):
        return "being posted"
    if rev.get("status") == "approved" and name not in _posted_at(pdir):
        return "approved, not posted yet"
    return None


def survey(root, queued=()):
    """Facts per project, read from its files."""
    projects = {}
    for pid in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        pdir = os.path.join(root, pid)
        if pid.startswith("_") or not os.path.isdir(pdir):
            continue
        meta = _read(os.path.join(pdir, "project.json"))
        posted = _posted_at(pdir)
        is_range = meta.get("kind") == "range"
        try:
            span = (int(meta["chapters_start"]), int(meta["chapters_end"])) if is_range else \
                (int(float(meta.get("chapter"))),) * 2
        except (KeyError, TypeError, ValueError):
            span = None
        projects[pid] = {"dir": pdir, "series": meta.get("series_id") or meta.get("series") or "", "range": is_range,
                         "span": span, "posted": posted, "drive": _read(os.path.join(pdir, "drive.json"))}
    # a chapter counts as posted when it is, or when a posted compilation covers it
    covers = {}
    for pid, p in projects.items():
        if p["posted"] and p["span"]:
            covers.setdefault(p["series"], []).append((p["span"], max(p["posted"].values()), pid))
    newest_comp = {}
    for series, items in covers.items():
        comps = [(span, at, pid) for span, at, pid in items if projects[pid]["range"]]
        if comps:
            newest_comp[series] = max(comps, key=lambda x: (x[0][1], x[1]))[2]
    for pid, p in projects.items():
        p["settled_at"] = None
        if p["posted"]:
            p["settled_at"] = max(p["posted"].values())
        elif p["span"] and not p["range"]:
            hit = [at for span, at, _o in covers.get(p["series"], []) if span[0] <= p["span"][0] <= span[1]]
            if hit:
                p["settled_at"] = min(hit)
        p["base_of_next_release"] = newest_comp.get(p["series"]) == pid
    return projects


def plan(root, now=None, queued=(), environ=None):
    """{"candidates": [...], "kept": [...], "totals": {...}} — changes nothing."""
    now = now or time.time()
    win = windows(environ)
    queued = set(tuple(x) for x in queued)
    cands, kept = [], []

    def cand(category, path, why, **kw):
        cands.append({"category": category, "path": os.path.relpath(path, root), "bytes": _size(path), "why": why, **kw})

    def keep(path, why):
        kept.append({"path": os.path.relpath(path, root), "bytes": _size(path), "why": why})

    for pid, p in survey(root, queued).items():
        pdir, settled = p["dir"], p["settled_at"]
        age = (now - settled) / 86400.0 if settled else None
        exports = os.path.join(pdir, "exports")
        names = [n for n in sorted(os.listdir(exports)) if n.endswith(".mp4")] if os.path.isdir(exports) else []
        busy = {n: _busy(pdir, n, queued) for n in names}
        for sub in WORK_DIRS:
            path = os.path.join(pdir, sub)
            if not os.path.isdir(path) or not os.listdir(path):
                continue
            if any(busy.values()):
                keep(path, "this chapter has a video " + next(v for v in busy.values() if v))
            elif age is None:
                keep(path, "not posted yet — still needed to render or re-edit")
            elif age < win["WORK_DAYS"]:
                keep(path, f"posted {age:.0f} day(s) ago — kept for {win['WORK_DAYS']}")
            else:
                cand("work_files", path, f"posted {age:.0f} days ago; render inputs are not used again", project=pid)
        for n in names:
            path = os.path.join(exports, n)
            d = p["drive"]
            archived = (d.get("video") == n and d.get("verified") is True and bool(d.get("sha256"))
                        and bool(((d.get("files") or {}).get("video") or {}).get("id")))
            if busy[n]:
                keep(path, busy[n])
            elif n not in p["posted"]:
                keep(path, "not posted — the only finished copy of this video" if not p["range"] else
                     "compilation not posted yet")
            elif p["base_of_next_release"]:
                keep(path, "newest posted compilation of the series — the next release is built on it")
            elif not archived:
                keep(path, "no verified archive copy with its checksum on record — this is the only safe copy")
            elif (now - p["posted"][n]) / 86400.0 < win["ARCHIVE_DAYS"]:
                keep(path, f"posted {(now - p['posted'][n]) / 86400.0:.0f} day(s) ago — kept for {win['ARCHIVE_DAYS']}")
            else:
                cand("archived", path, "posted, with a verified Drive copy (checksum on record)", project=pid)
    for category, (folder, key) in ROOT_DIRS.items():
        base = os.path.join(root, folder)
        for entry in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            path = os.path.join(base, entry)
            if category == "trash" and entry == "retention.log.jsonl":
                continue                       # the record of what was cleaned stays
            try:
                days = (now - (_trashed_at(path) if category == "trash" else _newest(path))) / 86400.0
            except OSError:
                continue
            if days >= win[key]:
                cand(category, path, (f"in the trash for {days:.0f} days" if category == "trash" else
                                      f"untouched for {days:.0f} days") + f" (limit {win[key]})")
    totals = {c: {"items": 0, "bytes": 0} for c in CATEGORIES}
    for c in cands:
        totals[c["category"]]["items"] += 1
        totals[c["category"]]["bytes"] += c["bytes"]
    return {"generated_at": now, "windows": win, "candidates": cands, "kept": kept, "totals": totals,
            "reclaimable_bytes": sum(c["bytes"] for c in cands),
            "kept_bytes": sum(k["bytes"] for k in kept),
            "note": "dry run — nothing was changed; windows are proposed defaults awaiting the owner's confirmation"}


def apply(root, categories, confirm="", max_bytes=5 * 1024 ** 3, now=None, queued=(), environ=None):
    """Act on a FRESH plan, for the named categories only, up to max_bytes.
    Everything is MOVED into <root>/_trash/retention-<date>/ (undo: move it
    back) — except category "trash", which deletes and needs confirm="purge"."""
    now = now or time.time()
    bad = sorted(set(categories) - set(CATEGORIES))
    if bad or not categories:
        raise ValueError("name the categories to clean: " + ", ".join(CATEGORIES))
    if "trash" in categories and confirm != "purge":
        raise ValueError('emptying the trash deletes for good — pass confirm="purge"')
    p = plan(root, now, queued, environ)
    dest = os.path.join(root, "_trash", f"{MOVED}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}")
    done, freed, skipped = [], 0, []
    for c in p["candidates"]:
        if c["category"] not in categories:
            continue
        if freed + c["bytes"] > max_bytes:
            skipped.append({**c, "skipped": "over this run's size limit"})
            continue
        src = os.path.join(root, c["path"])
        try:
            if c["category"] == "trash":
                shutil.rmtree(src) if os.path.isdir(src) else os.remove(src)
                action = "deleted"
            else:
                target = os.path.join(dest, c["path"])
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(src, target)
                action = "moved to trash"
        except OSError as e:
            skipped.append({**c, "skipped": str(e)[:160]})
            continue
        freed += c["bytes"]
        done.append({**c, "action": action})
    log = {"at": now, "categories": sorted(categories), "freed_bytes": freed, "items": done, "skipped": skipped,
           "undo": f"move the folders under _trash/{os.path.basename(dest)}/ back to where `path` says"}
    if done:
        try:
            os.makedirs(os.path.join(root, "_trash"), exist_ok=True)
            with open(os.path.join(root, "_trash", "retention.log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({k: v for k, v in log.items() if k != "items"} | {"paths": [d["path"] for d in done]}) + "\n")
        except OSError:
            pass
    return log
