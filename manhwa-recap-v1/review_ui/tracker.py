"""Chapter tracker — what has been ingested vs what the source has published.

For every series already in the library, derive its series page from a stored
chapter URL, read the chapter numbers it lists, and report the gap. The point
is the NEXT chapter: a series can be 200+ chapters ahead, so listing every
missing one is a wall, not a feature.

Design rules carried over from the scraper bug (Session 26):
  * "checked, nothing new" and "could not check" are DIFFERENT states. A failed
    fetch must never render as an up-to-date series.
  * results are cached on disk with a TTL so opening the tab does not hammer
    the source; refresh is explicit.
"""
import html as _html
import json
import os
import re
import ssl
import time
import urllib.request

CACHE_NAME = "_tracker_cache.json"
TTL_SECONDS = 30 * 60          # re-check a series at most twice an hour
TIMEOUT = 45
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def series_page_url(chapter_url):
    """The series page a chapter URL belongs to, or None.

    'https://site/comics/some-slug-ab12/chapter/7' -> 'https://site/comics/some-slug-ab12/'
    """
    if not chapter_url:
        return None
    m = re.match(r"^(https?://[^\s]+?)/chapter/[\d.]+/?$", chapter_url.strip())
    return (m.group(1).rstrip("/") + "/") if m else None


def _fetch(url):
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=ctx, timeout=TIMEOUT) as r:
        return _html.unescape(r.read().decode("utf-8", "replace"))


def chapter_numbers(series_url, _fetcher=None):
    """Every chapter number the series page links to, ascending.

    Raises on a fetch/parse failure — callers must surface that rather than
    treating it as an empty result.
    """
    page = (_fetcher or _fetch)(series_url)
    nums = set()
    for m in re.findall(r"/chapter/(\d+(?:\.\d+)?)", page):
        try:
            nums.add(float(m))
        except ValueError:
            pass
    if not nums:
        raise ValueError("series page listed no chapter links (layout changed?)")
    return sorted(nums)


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else str(n)


def _load_cache(pdir_root):
    try:
        with open(os.path.join(pdir_root, CACHE_NAME), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(pdir_root, cache):
    try:
        tmp = os.path.join(pdir_root, CACHE_NAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
        os.replace(tmp, os.path.join(pdir_root, CACHE_NAME))
    except OSError:
        pass


def build(projects, projects_root, refresh=False, _fetcher=None, now=None):
    """Group known projects by series and report the gap for each.

    `projects` is ingest.list_projects() output (dicts with id/url/series/chapter).
    Returns {"series": [...], "checked_at": ts, "errors": n}.
    """
    now = now or time.time()
    cache = _load_cache(projects_root)
    groups = {}
    for p in projects:
        url = p.get("url") or ""
        spage = series_page_url(url)
        if not spage:
            continue                      # legacy/hand-made project, nothing to track
        g = groups.setdefault(spage, {
            "series": p.get("series") or p.get("id"),
            "series_url": spage,
            "have": [],                   # (number, project_id)
        })
        try:
            g["have"].append((float(p.get("chapter")), p.get("id")))
        except (TypeError, ValueError):
            pass

    out = []
    errors = 0
    for spage, g in sorted(groups.items(), key=lambda kv: kv[1]["series"].lower()):
        have_nums = sorted(n for n, _ in g["have"])
        entry = {
            "series": g["series"],
            "series_url": spage,
            "have": [_fmt(n) for n in have_nums],
            "have_count": len(have_nums),
            "highest_have": _fmt(have_nums[-1]) if have_nums else None,
            "projects": {_fmt(n): pid for n, pid in g["have"]},
        }

        cached = cache.get(spage) or {}
        fresh = (not refresh
                 and cached.get("checked_at")
                 and now - cached["checked_at"] < TTL_SECONDS
                 and cached.get("chapters"))
        if fresh:
            chapters, err, checked_at = cached["chapters"], None, cached["checked_at"]
        else:
            try:
                chapters = chapter_numbers(spage, _fetcher=_fetcher)
                err, checked_at = None, now
                cache[spage] = {"chapters": chapters, "checked_at": now}
            except Exception as e:
                chapters, err, checked_at = cached.get("chapters") or [], \
                    f"{type(e).__name__}: {e}", cached.get("checked_at")
                errors += 1

        # "New" means published AFTER what we hold — not every chapter we lack.
        # Counting all gaps made a series where we ingested ch 338 of 338 report
        # "338 behind, next -> ch 0", which is true but useless (Session 27 live
        # check). Earlier chapters we skipped are a BACKFILL, reported separately.
        held = set(have_nums)
        top = have_nums[-1] if have_nums else None
        newer = [c for c in chapters if top is None or c > top]
        gaps = [c for c in chapters if top is not None and c < top and c not in held]
        entry.update({
            "latest": _fmt(chapters[-1]) if chapters else None,
            "total_published": len(chapters),
            "behind": len(newer),
            "next": _fmt(newer[0]) if newer else None,
            "next_url": (spage + "chapter/" + _fmt(newer[0])) if newer else None,
            # only ever hand the UI a short list; a series can be 200+ ahead
            "upcoming": [{"chapter": _fmt(c), "url": spage + "chapter/" + _fmt(c)}
                         for c in newer[:25]],
            "backfill_count": len(gaps),
            "backfill": [{"chapter": _fmt(c), "url": spage + "chapter/" + _fmt(c)}
                         for c in gaps[:25]],
            "checked_at": checked_at,
            "stale": bool(err and cached.get("chapters")),
            "error": err,
        })
        out.append(entry)

    _save_cache(projects_root, cache)
    return {"series": out, "checked_at": now, "errors": errors,
            "ttl_seconds": TTL_SECONDS}
