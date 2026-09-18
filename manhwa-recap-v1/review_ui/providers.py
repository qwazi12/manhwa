"""Source providers — one interface, many sites.

The pipeline grew up around one site. Every layer learned the same shape:
`/comics/<slug>/chapter/<n>`. tracker.series_page_url() requires it,
tracker.chapter_numbers() greps for it, ingest.parse_series_chapter() matches
it, and ingest._slug() builds a project id out of it. None of that is wrong —
it is just a site encoded as an architecture.

The cost shows the moment a second site appears. Given a WEBTOON episode URL,
parse_series_chapter returns ('episode-1', 'viewer?title_no=5988&episode_no=1')
and the project id becomes 'episode-1_viewer-title_no-5988-episode_no-1';
series_page_url returns None, so the series cannot be tracked at all. Nothing
raises — it just produces nonsense quietly, which is the worst failure mode
available.

So this module makes the SITE a plug-in rather than an assumption. A provider
answers a small number of questions about one source:

    match_url            is this URL mine?
    normalize_series_url  the canonical series page for it
    discover_chapters     what chapters exist, as (id, url) pairs
    normalize_chapter_id  '007' and '7' are the same chapter
    chapter_url           build the URL for one chapter
    extract_pages         the chapter's page images, in reading order
    filter_noise          drop thumbnails, recommendations, furniture
    quality_check         is this extraction trustworthy?

Adding a site means writing one class, not editing five modules. The generic
provider is the floor: it does what the current scraper does for anything
unrecognised, so an unknown site degrades to today's behaviour instead of
failing.

NOTHING HERE CHANGES THE ASURA PATH'S BEHAVIOUR. AsuraProvider reproduces the
existing regexes exactly, so the site that works today keeps working.
"""

import os
import re
import ssl
import urllib.parse
import urllib.request

TIMEOUT = int(os.environ.get("PROVIDER_TIMEOUT", 45))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# How well a source is supported. Stored per mirror so the UI can say what will
# happen BEFORE the owner spends a chapter's worth of credit on it.
SUPPORTED = "supported"          # discovery + extraction both proven
PARTIAL = "partial"              # one of the two is best-effort
FALLBACK = "fallback_only"       # generic extraction, no real discovery
UNSUPPORTED = "unsupported"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return r.read().decode("utf-8", "replace")


# Image furniture that is never a chapter page, on any site.
_JUNK = ("logo", "avatar", "icon", "theme", "plugin", "ad-", "banner",
         "footer", "header", "widget", "comment", "/covers/", "cover.",
         "thumbnail", "thumb_", "_thumb", "sprite", "placeholder", "profile",
         "badge", "emoji", "favicon")


class Provider:
    """One source. Subclasses override what differs; the rest is shared."""

    name = "generic"
    label = "Generic"
    support = FALLBACK

    # ---- identity ----------------------------------------------------
    def match_url(self, url):
        return False

    def normalize_series_url(self, url):
        """The canonical series page for any URL belonging to this source."""
        return url.rstrip("/") + "/"

    def series_key(self, url):
        """A stable identifier for the SERIES within this source, used to spot
        the same mirror added twice under slightly different URLs."""
        return self.normalize_series_url(url)

    # ---- chapters ----------------------------------------------------
    def normalize_chapter_id(self, cid):
        """'007' and '7' are the same chapter; '12.5' is not 12."""
        s = str(cid).strip()
        try:
            f = float(s)
            return str(int(f)) if f == int(f) else str(f)
        except (TypeError, ValueError):
            return s

    def discover_chapters(self, series_url, _fetcher=None):
        """[(chapter_id, chapter_url)] ascending. Raises on fetch/parse
        failure — a source that could not be checked must never look like a
        source with no chapters."""
        raise NotImplementedError("this source cannot list chapters")

    def chapter_url(self, series_url, chapter_id):
        raise NotImplementedError

    # ---- pages -------------------------------------------------------
    def filter_noise(self, urls):
        return [u for u in urls if not any(j in u.lower() for j in _JUNK)]

    def extract_pages(self, html):
        """Chapter page images in reading order.

        The shared default is the rule the scraper already learned the hard
        way: every page of a chapter is served from the SAME directory and
        there are more of them than of any other image, so take the dominant
        directory rather than trusting filename shape.
        """
        generic = r'https://[^\s"\'>]+?\.(?:webp|jpg|jpeg|png)'
        ordered = {}
        for m in re.finditer(generic, html):
            ordered.setdefault(m.group(0), m.start())
        urls = self.filter_noise(list(ordered))
        if not urls:
            return []
        by_dir = {}
        for u in urls:
            by_dir.setdefault(u.rsplit("/", 1)[0], []).append(u)
        best = max(by_dir.values(), key=len)
        return sorted(best, key=lambda u: ordered[u])

    def quality_check(self, pages):
        """Is this extraction trustworthy enough to spend money on?"""
        if not pages:
            return False, "no page images found"
        if len(pages) < 3:
            return False, f"only {len(pages)} page(s) — likely a bad parse"
        return True, f"{len(pages)} pages"


class AsuraProvider(Provider):
    """The site the pipeline grew up on. Reproduces the existing regexes
    EXACTLY so nothing about today's working path changes."""

    name = "asura"
    label = "Asura"
    support = SUPPORTED

    _SERIES = re.compile(r"^(https?://[^\s]+?/comics/[^/]+)/?$", re.I)
    _CHAPTER = re.compile(r"^(https?://[^\s]+?)/chapter/([\d.]+)/?$", re.I)

    def match_url(self, url):
        u = (url or "").lower()
        return "/comics/" in u or bool(self._CHAPTER.match(url or ""))

    def normalize_series_url(self, url):
        u = (url or "").strip()
        m = self._CHAPTER.match(u)
        if m:                                   # same rule as tracker's
            return m.group(1).rstrip("/") + "/"
        m = self._SERIES.match(u)
        if m:
            return m.group(1).rstrip("/") + "/"
        return u.rstrip("/") + "/"

    def discover_chapters(self, series_url, _fetcher=None):
        page = (_fetcher or fetch)(series_url)
        found = {}
        for m in re.findall(r"/chapter/(\d+(?:\.\d+)?)", page):
            cid = self.normalize_chapter_id(m)
            found[cid] = self.chapter_url(series_url, m)
        if not found:
            raise ValueError("series page listed no chapter links "
                             "(layout changed?)")
        return sorted(found.items(), key=lambda kv: float(kv[0]))

    def chapter_url(self, series_url, chapter_id):
        return series_url.rstrip("/") + "/chapter/" + str(chapter_id)


class WebtoonProvider(Provider):
    """WEBTOON.

    Different in every way that matters: episodes are `episode_no=` query
    parameters rather than path segments, the series page is a `/list` URL
    keyed by `title_no`, and the viewer serves the strip from a CDN alongside
    a large amount of recommendation furniture — a raw extraction on one
    episode returned 247 images whose first entry was a square thumbnail.
    """

    name = "webtoon"
    label = "WEBTOON"
    support = SUPPORTED

    _TITLE_NO = re.compile(r"[?&]title_no=(\d+)", re.I)
    _EPISODE_NO = re.compile(r"[?&]episode_no=(\d+)", re.I)
    _PATH = re.compile(r"webtoons\.com/([a-z]{2})/([^/]+)/([^/]+)/", re.I)

    def match_url(self, url):
        return "webtoons.com" in (url or "").lower()

    def _parts(self, url):
        m = self._PATH.search(url or "")
        lang, genre, slug = (m.group(1), m.group(2), m.group(3)) if m \
            else ("en", "action", "series")
        tno = self._TITLE_NO.search(url or "")
        return lang, genre, slug, (tno.group(1) if tno else None)

    def normalize_series_url(self, url):
        lang, genre, slug, tno = self._parts(url)
        if not tno:
            return (url or "").rstrip("/")
        # Always the desktop list page: it is the one URL that identifies a
        # WEBTOON series unambiguously, and title_no is the real identity —
        # the genre and slug in the path can both change.
        return (f"https://www.webtoons.com/{lang}/{genre}/{slug}/list"
                f"?title_no={tno}")

    def series_key(self, url):
        _, _, _, tno = self._parts(url)
        return f"webtoon:{tno}" if tno else self.normalize_series_url(url)

    def discover_chapters(self, series_url, _fetcher=None):
        """Episodes 1..latest.

        WEBTOON paginates its episode list, so the page only ever shows the
        most recent handful. What it DOES reliably carry is the highest
        episode_no, and WEBTOON episodes are sequential integers from 1 — so
        the latest number is the count. Stated plainly because it is an
        inference, not a listing: a series with missing or unpublished
        episodes in the middle would be over-reported.
        """
        page = (_fetcher or fetch)(series_url)
        nums = {int(n) for n in self._EPISODE_NO.findall(page)}
        if not nums:
            raise ValueError("no episodes found on the WEBTOON list page "
                             "(layout changed, or the title_no is wrong)")
        latest = max(nums)
        return [(str(i), self.chapter_url(series_url, i))
                for i in range(1, latest + 1)]

    def chapter_url(self, series_url, chapter_id):
        lang, genre, slug, tno = self._parts(series_url)
        return (f"https://www.webtoons.com/{lang}/{genre}/{slug}/episode-"
                f"{chapter_id}/viewer?title_no={tno}&episode_no={chapter_id}")

    def filter_noise(self, urls):
        urls = super().filter_noise(urls)
        out = []
        for u in urls:
            low = u.lower()
            # WEBTOON's own furniture: square thumbs on the recommendation
            # rail, author avatars, and the title card. These sit on the same
            # CDN as the strip, so the host alone cannot separate them.
            if "_square" in low or "thumb" in low or "/thumbnail" in low:
                continue
            if "daily_pass" in low or "recommend" in low or "banner" in low:
                continue
            out.append(u)
        return out

    # The viewer marks each strip slice with class="_images" and lazy-loads the
    # real file from data-url. `src` holds a transparent placeholder.
    _SLICE = re.compile(
        r'<img[^>]*class="[^"]*_images[^"]*"[^>]*data-url="([^"]+)"', re.I)
    _SLICE_ALT = re.compile(
        r'<img[^>]*data-url="([^"]+)"[^>]*class="[^"]*_images[^"]*"', re.I)

    def extract_pages(self, html):
        """WEBTOON breaks the rule every other source follows.

        The shared extractor keeps the directory holding the most images,
        because on a normal chapter page every page is served from one folder.
        WEBTOON does the opposite: each slice sits in its OWN dated CDN folder,
        while 500 UI assets share a single static directory. So the dominant-
        directory vote picks the furniture and throws the comic away — measured
        on episode 1 of The Stellar Swordmaster, 1027 raw images collapsed to
        ONE page from the static-asset host.

        The reliable signal is the viewer's own markup: every strip slice is an
        <img class="_images"> lazy-loading its real file from data-url, while
        `src` holds a transparent placeholder. That yields 242 slices with no
        thumbnails at all, and it does not depend on folder layout.
        """
        urls = self._SLICE.findall(html) or self._SLICE_ALT.findall(html)
        urls = [u for u in urls if u.startswith("http")]
        if urls:
            seen, out = set(), []
            for u in self.filter_noise(urls):
                if u not in seen:
                    seen.add(u)
                    out.append(u)          # document order IS reading order
            return out
        # Markup changed: fall back to the shared rule rather than returning
        # nothing, and let quality_check decide whether it is usable.
        return super().extract_pages(html)

    def quality_check(self, pages):
        ok, why = super().quality_check(pages)
        if not ok:
            return ok, why
        hosts = {u.split("/")[2] for u in pages}
        if not any("pstatic.net" in h or "webtoon" in h for h in hosts):
            return False, f"pages are not on a WEBTOON CDN ({', '.join(hosts)})"
        return True, why


class GenericProvider(Provider):
    """The floor. Anything unrecognised still extracts pages the way the
    current scraper does, so an unknown site degrades to today's behaviour
    rather than failing outright. It cannot list chapters, which is exactly
    why its support level says so."""

    name = "generic"
    label = "Other site"
    support = FALLBACK

    def match_url(self, url):
        return bool(url)

    def discover_chapters(self, series_url, _fetcher=None):
        raise ValueError("this source cannot list chapters automatically — "
                         "paste a chapter URL directly")


ASURA = AsuraProvider()
WEBTOON = WebtoonProvider()
GENERIC = GenericProvider()

# Order matters: the generic provider matches everything, so it is last.
REGISTRY = [WEBTOON, ASURA, GENERIC]
BY_NAME = {p.name: p for p in REGISTRY}


def for_url(url):
    for p in REGISTRY:
        if p.match_url(url):
            return p
    return GENERIC


def by_name(name):
    return BY_NAME.get(name, GENERIC)


def describe(url):
    """What the system can do with this URL, without fetching anything."""
    p = for_url(url)
    return {"source": p.name, "label": p.label, "support": p.support,
            "series_url": p.normalize_series_url(url),
            "series_key": p.series_key(url)}
