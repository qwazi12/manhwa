"""Glue for story research: which series, which slug(s), merge, save.

series_research.py does the research and the source rules; this file knows
the studio: the watchlist (title, aliases, mirrors, latest chapter), the
autopilot window (which chapters we're recapping), and where ingest looks a
bible up (`series_bible.load_series_bible(<series slug of the chapter URL>)`).
A bible is saved under the slug of EVERY mirror of the series, so whichever
source a chapter comes from finds it.
"""

import os
import threading
import time

_lock = threading.Lock()
STATUS = {}          # series_id -> {"status", "at", "error", "summary"}


def _wl_root():
    import ingest
    return ingest.PROJECTS


def _series(series_id):
    import watchlist
    for s in watchlist.load(_wl_root())["series"]:
        if s["id"] == series_id:
            return s
    return None


def _slugs(series):
    import ingest
    import providers
    out = []
    for m in series.get("mirrors") or []:
        try:
            ch = (m.get("chapters") or ["1"])[-1]
            url = providers.by_name(m["source"]).chapter_url(m["series_url"], ch)
            slug, _ = ingest.parse_series_chapter(url)
            if slug and slug not in out:
                out.append(slug)
        except Exception:
            pass
    return out


def _window(series):
    try:
        import autopilot
        st = autopilot.load(_wl_root())
        start = st["start_from"].get(series["id"])
    except Exception:
        start = None
    latest = None
    for m in series.get("mirrors") or []:
        if m.get("latest"):
            latest = m["latest"]
    if start and latest:
        return f"{start} to {latest}", latest
    return (f"around {latest}" if latest else "the latest chapters"), latest


def existing_bible(series):
    import series_bible
    for slug in _slugs(series):
        b = series_bible.load_series_bible(slug)
        if b:
            return b
    return None


def build(series_id, api_key=None):
    """Research one watchlist series and save its bible. Returns the bible."""
    import series_bible
    import series_research
    api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    s = _series(series_id)
    if s is None:
        raise ValueError(f"no watchlist series '{series_id}'")
    STATUS[series_id] = {"status": "running", "at": time.time()}
    try:
        old = existing_bible(s)
        saved = (old or {}).get("research") or {}
        if old and not old.get("characters") and saved.get("text") and saved.get("claims"):
            # researched before but no cast came out of it: rebuild from the
            # saved research (one cheap call), no new search
            rec = series_research.restructure(s["title"], saved, api_key)
        else:
            window, latest = _window(s)
            mirror = next((m for m in s.get("mirrors") or [] if m.get("series_key") == s.get("preferred_mirror")),
                          (s.get("mirrors") or [{}])[0])
            rec = series_research.research(s["title"], s.get("aliases") or [], mirror.get("series_url", ""),
                                           latest or "unknown", window, api_key)
        new = series_research.to_bible(s["title"], s.get("aliases") or [], rec)
        with _lock:
            merged = series_research.merge(old if (old or {}).get("characters") else None, new)
            merged.setdefault("watchlist_id", series_id)
            for slug in _slugs(s) or [series_id]:
                series_bible.save_series_bible(slug, dict(merged))
        r = merged.get("research") or {}
        tiers = {}
        for x in r.get("sources") or []:
            tiers[x["tier"]] = tiers.get(x["tier"], 0) + 1
        summary = (f"{len(merged.get('characters') or [])} characters, "
                   f"{len(r.get('sources') or [])} sources ({', '.join(f'{v} {k}' for k, v in sorted(tiers.items()))})")
        STATUS[series_id] = {"status": "done", "at": time.time(), "summary": summary}
        return merged
    except Exception as e:
        STATUS[series_id] = {"status": "error", "at": time.time(), "error": str(e)[:300]}
        raise


def needs_research(series):
    """No bible yet, or one with no cast in it."""
    b = existing_bible(series)
    return not b or not b.get("characters")


def build_for_url(url, api_key=None):
    """Ingest's entry: the chapter URL's watchlist series, if it has no bible."""
    import watchlist
    s, _ = watchlist.find_by_mirror(watchlist.load(_wl_root()), url)
    if s is None:
        return None
    return build(s["id"], api_key)


def view(series_id):
    s = _series(series_id)
    if s is None:
        raise ValueError(f"no watchlist series '{series_id}'")
    b = existing_bible(s)
    return {"series_id": series_id, "title": s["title"], "bible": b,
            "status": STATUS.get(series_id), "slugs": _slugs(s)}


def save_owner_edit(series_id, bible):
    """Owner edits win from now on: mark every character as theirs."""
    import series_bible
    s = _series(series_id)
    if s is None:
        raise ValueError(f"no watchlist series '{series_id}'")
    for c in bible.get("characters") or []:
        c["origin"] = "owner"
    bible.setdefault("canonical_title", s["title"])
    with _lock:
        for slug in _slugs(s) or [series_id]:
            series_bible.save_series_bible(slug, dict(bible))
    return bible


def note_suggestions(url, script_text):
    """After a chapter's script exists: names it uses that the bible lacks."""
    import series_bible
    import series_research
    import watchlist
    s, _ = watchlist.find_by_mirror(watchlist.load(_wl_root()), url)
    if s is None:
        return []
    b = existing_bible(s)
    if not b:
        return []
    new = series_research.suggest_from_script(b, script_text)
    if not new:
        return []
    with _lock:
        cur = b.setdefault("suggested_characters", [])
        for n in new:
            if n not in cur:
                cur.append(n)
        for slug in _slugs(s):
            series_bible.save_series_bible(slug, dict(b))
    return new
