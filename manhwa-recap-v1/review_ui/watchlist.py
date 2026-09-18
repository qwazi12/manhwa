"""Watchlist — canonical series, with mirrors.

The tracker was never a watchlist. tracker.build(projects, ...) groups
ALREADY-INGESTED projects by series, so a title only appears once you have
spent money on it. That is a library view: useful for "what is new in what I
own", useless for "what should I make next".

It also assumed one series equals one site. The series identity WAS a URL, so
the same story on two sites was two unrelated rows, and a story on a site the
URL parser did not recognise was no row at all.

This module separates the two ideas that were fused:

    CANONICAL SERIES   one story, once. Title, aliases, tier, rank, keywords,
                       notes. Exists whether or not anything is ingested.
    MIRROR             one place that story can be read. A series may have
                       several; each knows its provider, its support level and
                       what it last saw.

Ingest then becomes: pick a series -> pick a mirror -> pick a chapter. The
site stops being the identity and becomes an implementation detail, which is
what makes the next source additive instead of another rewrite.

Nothing here replaces the existing tracker. tracker.build() still reports the
ingested library exactly as before; this sits alongside it as the planning
layer.
"""

import json
import os
import re
import time

import providers

STORE_NAME = "_watchlist.json"

TIERS = ("greenlight", "high_upside", "watchlist")
TIER_LABEL = {"greenlight": "Greenlight now",
              "high_upside": "High-upside secondary",
              "watchlist": "Watchlist"}


class WatchlistError(RuntimeError):
    """A condition the operator must see."""


# ------------------------------------------------------------------ store
def store_path(root):
    return os.path.join(root, STORE_NAME)


def load(root):
    try:
        with open(store_path(root), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"series": []}
    data.setdefault("series", [])
    return data


def save(root, data):
    os.makedirs(root, exist_ok=True)
    tmp = store_path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, store_path(root))
    return data


def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s or "series"


# ------------------------------------------------------------- canonical
def find(data, series_id):
    return next((s for s in data["series"] if s["id"] == series_id), None)


def find_by_mirror(data, url):
    """The canonical series a URL already belongs to, if any.

    Matching is by the PROVIDER'S series key, not the raw URL — the same
    WEBTOON series reached through m.webtoons.com, www.webtoons.com, a viewer
    URL or a list URL all resolve to `webtoon:<title_no>`, so pasting any of
    them finds the existing entry instead of creating a fifth duplicate.
    """
    key = providers.describe(url)["series_key"]
    for s in data["series"]:
        for m in s.get("mirrors", []):
            if m.get("series_key") == key:
                return s, m
    return None, None


def add_series(root, title, aliases=None, tier="watchlist", rank=None,
               keywords=None, notes="", data=None):
    """Add a canonical series.

    When the caller passes `data` it owns the save (seed() batches them);
    when it does not, this saves, because a caller that never sees the store
    has no way to know it needed to.
    """
    owns = data is None
    data = data if data is not None else load(root)
    sid = slugify(title)
    existing = find(data, sid)
    if existing:
        return existing
    entry = {
        "id": sid, "title": title,
        "aliases": list(aliases or []),
        "tier": tier if tier in TIERS else "watchlist",
        "rank": rank, "keywords": list(keywords or []),
        "notes": notes, "mirrors": [], "preferred_mirror": None,
        "added_at": time.time(),
    }
    data["series"].append(entry)
    if owns:
        save(root, data)
    return entry


def add_mirror(root, series_id, url, data=None, save_now=True):
    """Attach a source to a canonical series.

    Refuses to attach the same mirror twice, and refuses to attach a mirror
    that already belongs to a DIFFERENT series — that is the duplicate-identity
    problem the canonical layer exists to prevent, so it is an error rather
    than a silent second copy.
    """
    data = data if data is not None else load(root)
    s = find(data, series_id)
    if s is None:
        raise WatchlistError(f"no series '{series_id}' on the watchlist")
    desc = providers.describe(url)
    owner, _ = find_by_mirror(data, url)
    if owner is not None and owner["id"] != series_id:
        raise WatchlistError(
            f"that source is already attached to '{owner['title']}' — one "
            "story should exist once, so attach it there or remove it first")
    for m in s["mirrors"]:
        if m["series_key"] == desc["series_key"]:
            return m
    mirror = {
        "source": desc["source"], "label": desc["label"],
        "series_url": desc["series_url"], "series_key": desc["series_key"],
        "support": desc["support"],
        "added_at": time.time(),
        "last_checked": None, "chapters": [], "latest": None,
        "chapter_count": 0, "status": "unchecked", "error": None,
    }
    s["mirrors"].append(mirror)
    if s["preferred_mirror"] is None:
        s["preferred_mirror"] = mirror["series_key"]
    if save_now:
        save(root, data)
    return mirror


def remove_series(root, series_id):
    data = load(root)
    before = len(data["series"])
    data["series"] = [s for s in data["series"] if s["id"] != series_id]
    save(root, data)
    return before != len(data["series"])


def set_preferred(root, series_id, series_key):
    """Manual override of which mirror to ingest from."""
    data = load(root)
    s = find(data, series_id)
    if s is None:
        raise WatchlistError(f"no series '{series_id}'")
    if not any(m["series_key"] == series_key for m in s["mirrors"]):
        raise WatchlistError("that mirror is not attached to this series")
    s["preferred_mirror"] = series_key
    save(root, data)
    return s


def update_series(root, series_id, **fields):
    data = load(root)
    s = find(data, series_id)
    if s is None:
        raise WatchlistError(f"no series '{series_id}'")
    for k in ("title", "tier", "rank", "notes"):
        if k in fields and fields[k] is not None:
            s[k] = fields[k]
    for k in ("aliases", "keywords"):
        if k in fields and fields[k] is not None:
            s[k] = list(fields[k])
    if s["tier"] not in TIERS:
        s["tier"] = "watchlist"
    save(root, data)
    return s


# --------------------------------------------------------------- mirrors
def best_mirror(series):
    """The mirror to ingest from: the manual override if set, otherwise the
    best-supported one that actually has chapters."""
    mirrors = series.get("mirrors") or []
    if not mirrors:
        return None
    pref = series.get("preferred_mirror")
    if pref:
        m = next((x for x in mirrors if x["series_key"] == pref), None)
        if m:
            return m
    order = {providers.SUPPORTED: 0, providers.PARTIAL: 1,
             providers.FALLBACK: 2, providers.UNSUPPORTED: 3}
    return sorted(mirrors, key=lambda m: (order.get(m["support"], 9),
                                          -(m.get("chapter_count") or 0)))[0]


def refresh_mirror(root, series_id, series_key, _fetcher=None):
    """Ask one mirror what chapters it has.

    "Could not check" and "has nothing" are kept apart, the same rule the
    tracker learned: a failed fetch must never render as a source with no
    chapters.
    """
    data = load(root)
    s = find(data, series_id)
    if s is None:
        raise WatchlistError(f"no series '{series_id}'")
    m = next((x for x in s["mirrors"] if x["series_key"] == series_key), None)
    if m is None:
        raise WatchlistError("that mirror is not attached to this series")

    prov = providers.by_name(m["source"])
    try:
        found = prov.discover_chapters(m["series_url"], _fetcher=_fetcher)
        m["chapters"] = [c for c, _ in found]
        m["chapter_count"] = len(found)
        m["latest"] = found[-1][0] if found else None
        m["status"] = "ok"
        m["error"] = None
    except Exception as e:
        m["status"] = "error"
        m["error"] = str(e)[:300]
    m["last_checked"] = time.time()
    save(root, data)
    return m


def chapter_url(series, series_key, chapter_id):
    m = next((x for x in series.get("mirrors", [])
              if x["series_key"] == series_key), None)
    if m is None:
        raise WatchlistError("that mirror is not attached to this series")
    prov = providers.by_name(m["source"])
    return prov.chapter_url(m["series_url"], chapter_id)


# ------------------------------------------------------------------ view
def view(root, projects=None):
    """The watchlist, enriched with what has actually been ingested.

    `projects` is ingest.list_projects() output. Ingest history is matched by
    the provider's series key, so a chapter ingested from one mirror is
    credited to the canonical series regardless of which site it came from.
    """
    data = load(root)
    have = {}
    for p in (projects or []):
        url = p.get("url") or ""
        if not url:
            continue
        try:
            key = providers.describe(url)["series_key"]
        except Exception:
            continue
        have.setdefault(key, []).append(
            {"chapter": str(p.get("chapter")), "project": p.get("id")})

    out = []
    for s in data["series"]:
        mirrors = []
        ingested = []
        for m in s.get("mirrors", []):
            got = have.get(m["series_key"], [])
            ingested.extend(got)
            mirrors.append(dict(m, ingested=got, ingested_count=len(got)))
        best = best_mirror(s)
        latest = None
        for m in mirrors:
            if m.get("latest") is not None:
                try:
                    latest = max(latest, float(m["latest"])) if latest \
                        else float(m["latest"])
                except (TypeError, ValueError):
                    pass
        done = {c["chapter"] for c in ingested}
        # What is actually MISSING, not how many we have. A count comparison
        # (have 3, latest 3 -> nothing new) hides a gap whenever the chapters
        # held are not the first N: ingesting 1, 2 and 7 would report "up to
        # date" while chapter 3 was never made.
        known = set()
        for m in mirrors:
            known.update(str(c) for c in m.get("chapters") or [])
        unmade = sorted(known - done, key=lambda c: (len(c), c))
        out.append({
            "id": s["id"], "title": s["title"], "aliases": s.get("aliases", []),
            "tier": s.get("tier"), "tier_label": TIER_LABEL.get(s.get("tier"), ""),
            "rank": s.get("rank"), "keywords": s.get("keywords", []),
            "notes": s.get("notes", ""),
            "mirrors": mirrors,
            "mirror_count": len(mirrors),
            "preferred_mirror": s.get("preferred_mirror"),
            "best_mirror": best["series_key"] if best else None,
            "ingestable": any(m["support"] in (providers.SUPPORTED,
                                               providers.PARTIAL)
                              for m in mirrors),
            "support": best["support"] if best else providers.UNSUPPORTED,
            "backlog": int(latest) if latest else 0,
            "ingested_count": len(done),
            "ingested": sorted(done, key=lambda c: (len(c), c)),
            "unmade": unmade,
            "unmade_count": len(unmade),
            # No mirror checked yet means "we do not know", which must not
            # render as "up to date" — the caller shows mirror status for that.
            "checked": bool(known),
            "has_new": bool(unmade),
        })

    order = {"greenlight": 0, "high_upside": 1, "watchlist": 2}
    out.sort(key=lambda s: (order.get(s["tier"], 9), s["rank"] or 999,
                            s["title"].lower()))
    return {"series": out, "tiers": list(TIERS), "tier_labels": TIER_LABEL}


# ------------------------------------------------------------------ seed
# The owner's own demand research, as a starting watchlist. Tiers come from
# their three-band model; ranks are their ordering. Keywords are the alt-title
# and character clusters, which is the part that actually feeds video metadata.
#
# NOTE ON THE RANKS: they were produced from two DIFFERENT signals — official
# WEBTOON reads/subscribers for the WEBTOON titles, YouTube recap traffic for
# the Asura ones. Those measure different markets (story popularity vs
# recap-video demand), so `rank` is kept as the owner's ordering rather than
# treated as a single comparable score. Tier is the more honest field.
SEED = [
    ("The Stellar Swordmaster", 1, "greenlight",
     ["Star-Embracing Swordmaster", "Vlad"],
     ["star-embracing swordmaster", "vlad", "swordmaster manhwa recap"],
     "https://www.webtoons.com/en/action/the-stellar-swordmaster/list?title_no=5988"),
    ("The Extra's Academy Survival Guide", 2, "greenlight",
     ["Ed Rothstaylor"],
     ["ed rothstaylor", "academy survival", "villain in a game manhwa"],
     "https://www.webtoons.com/en/fantasy/the-extras-academy-survival-guide/list?title_no=6465"),
    ("The Regressed Mercenary Has a Plan", 3, "greenlight",
     ["The Regressed Mercenary's Machinations", "Cecil Perdium", "Mercenary King"],
     ["regressed mercenary", "cecil perdium", "mercenary king"],
     "https://www.webtoons.com/en/action/the-regressed-mercenary-has-a-plan/list?title_no=7261"),
    ("Return of the Mount Hua Sect", 4, "greenlight",
     ["Return of the Blossoming Blade", "Chung Myung"],
     ["return of the blossoming blade", "chung myung", "mount hua recap"],
     "https://asurascans.com/comics/return-of-the-mount-hua-sect-6f7fe6eb"),
    ("I Am the Fated Villain", 5, "greenlight",
     ["Me, The Heavenly Destined Villain", "Gu Changge"],
     ["heavenly destined villain", "gu changge", "villain system manhua"],
     "https://asurascans.com/comics/i-am-the-fated-villain-6f7fe6eb"),
    ("Revenge of the Iron-Blooded Sword Hound", 6, "greenlight",
     ["Vikir", "Baskerville"],
     ["vikir", "baskerville", "revenge manhwa recap"],
     "https://asurascans.com/comics/revenge-of-the-iron-blooded-sword-hound-6f7fe6eb"),
    ("Fog Land", 7, "high_upside",
     ["Dante Kang"],
     ["dante kang", "prison manhwa", "horror webtoon recap"],
     "https://www.webtoons.com/en/action/fog-land/list?title_no=9299"),
    ("Murim Psychopath", 8, "high_upside",
     ["Dong Bongsu"],
     ["dong bongsu", "psychopath murim", "real murim"],
     "https://asurascans.com/comics/murim-psychopath-6f7fe6eb"),
    ("Return of the Apocalypse-Class Death Knight", 9, "high_upside", [],
     ["apocalypse class death knight", "death knight manhwa recap"],
     "https://asurascans.com/comics/return-of-the-apocalypse-class-death-knight-6f7fe6eb"),
    ("The Former Supreme", 10, "high_upside", [],
     ["former supreme", "murim comeback manhwa"],
     "https://asurascans.com/comics/the-former-supreme-6f7fe6eb"),
    ("The Indomitable Martial King", 11, "watchlist", [],
     ["indomitable martial king", "martial king manhwa recap"],
     "https://asurascans.com/comics/the-indomitable-martial-king-6f7fe6eb"),
    ("The Dark Mage's Return to Enlistment", 12, "watchlist", [],
     ["dark mage return to enlistment", "military return manhwa"],
     "https://asurascans.com/comics/the-dark-mages-return-to-enlistment-6f7fe6eb"),
    ("What a Bountiful Harvest, Demon Lord!", 13, "watchlist", [],
     ["bountiful harvest demon lord", "farming fantasy manhwa"],
     "https://asurascans.com/comics/what-a-bountiful-harvest-demon-lord-6f7fe6eb"),
    ("Too Many Heroes for the Demon Lord", 14, "watchlist", [],
     ["too many heroes demon lord", "demon lord manhwa recap"],
     "https://www.webtoons.com/en/action/too-many-heroes-for-the-demon-lord/list?title_no=9702"),
]


def seed(root, overwrite=False):
    """Load the research into the watchlist. Idempotent: an existing series
    keeps whatever the owner has since changed about it."""
    data = load(root)
    added, skipped = [], []
    for title, rank, tier, aliases, keywords, url in SEED:
        sid = slugify(title)
        existing = find(data, sid)
        if existing and not overwrite:
            skipped.append(title)
        else:
            if existing:
                data["series"] = [s for s in data["series"] if s["id"] != sid]
            add_series(root, title, aliases=aliases, tier=tier, rank=rank,
                       keywords=keywords, data=data)
            added.append(title)
        try:
            add_mirror(root, sid, url, data=data, save_now=False)
        except WatchlistError:
            pass
    save(root, data)
    return {"added": added, "skipped": skipped, "total": len(data["series"])}
