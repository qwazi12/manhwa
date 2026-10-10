"""Mangayomi & FMHY provider integration for Manhwa Recap Studio.

Inspired by Mangayomi (github.com/kodjodevf/mangayomi & mangayomi-extensions)
and FreeMediaHeckYeah (FMHY - fmhy.net / readingpiracyguide#manga).

Mangayomi is a cross-platform app with multi-source plugin extensions containing
hundreds of actively maintained scraper modules for anime video streams and
manga/manhwa chapters across dozens of aggregator sites.

This module provides:
1. Curated FMHY Manhwa/Manga source directory with known mirror domains.
2. Mangayomi extension catalog resolver (cached from kodjodevf/mangayomi-extensions).
3. MangayomiProvider: Pluggable provider for sites covered by Mangayomi/FMHY
   (MangaDex, FlameComics, ReaperScans, ComicK, Bato, MangaKakalot, etc.).
"""

import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, 'projects', '_cache')
MANGAYOMI_CACHE = os.path.join(CACHE_DIR, 'mangayomi_extensions.json')
MANGAYOMI_INDEX_URL = 'https://raw.githubusercontent.com/kodjodevf/mangayomi-extensions/main/index.json'

# FMHY Curated Top Sources for Manhwa & Webtoons
FMHY_SOURCES = [
    {
        'id': 'mgeko',
        'name': 'MangaGeko',
        'url': 'https://www.mgeko.cc',
        'domains': ['mgeko.cc', 'mgeko.com'],
        'type': 'aggregator',
        'note': 'Fast, zero-Cloudflare aggregator with huge manhwa archive.',
        'priority': 1,
    },
    {
        'id': 'mangaread',
        'name': 'MangaRead',
        'url': 'https://www.mangaread.org',
        'domains': ['mangaread.org', 'mangaread.co'],
        'type': 'aggregator',
        'note': 'High quality manhwa scans mirror.',
        'priority': 2,
    },
    {
        'id': 'mangadex',
        'name': 'MangaDex',
        'url': 'https://mangadex.org',
        'domains': ['mangadex.org'],
        'api': 'https://api.mangadex.org',
        'type': 'api',
        'note': 'Community standard, completely ad-free, high quality images and translations.',
        'priority': 1,
    },
    {
        'id': 'comick',
        'name': 'ComicK',
        'url': 'https://comick.io',
        'domains': ['comick.io', 'comick.fun', 'comick.app'],
        'api': 'https://api.comick.fun',
        'type': 'aggregator',
        'note': 'Huge library of manhwa with fast release tracking and multiple scan groups.',
        'priority': 2,
    },
    {
        'id': 'flamecomics',
        'name': 'Flame Comics',
        'url': 'https://flamecomics.me',
        'domains': ['flamecomics.me', 'flamecomics.xyz', 'flamescans.org'],
        'type': 'scanlator',
        'note': 'Top scanlator for popular martial arts and fantasy manhwa.',
        'priority': 3,
    },
    {
        'id': 'reaperscans',
        'name': 'Reaper Scans',
        'url': 'https://reaperscans.com',
        'domains': ['reaperscans.com'],
        'type': 'scanlator',
        'note': 'High-quality translations for regression, system, and hunter manhwa.',
        'priority': 4,
    },
    {
        'id': 'bato',
        'name': 'Bato.to',
        'url': 'https://bato.to',
        'domains': ['bato.to', 'mangatoto.com', 'dto.to', 'battwo.com'],
        'type': 'aggregator',
        'note': 'Reliable archive with direct high-resolution manhwa uploads.',
        'priority': 5,
    },
    {
        'id': 'mangakakalot',
        'name': 'MangaKakalot / Manganato',
        'url': 'https://mangakakalot.com',
        'domains': ['mangakakalot.com', 'manganato.com', 'chapmanganato.to'],
        'type': 'aggregator',
        'note': 'Massive legacy catalog across all genres.',
        'priority': 6,
    },
]

DOMAINS_MAP = {dom: s for s in FMHY_SOURCES for dom in s['domains']}


def get_fmhy_sources() -> List[Dict[str, Any]]:
    """Returns curated FMHY Manhwa/Manga sources with mirror metadata."""
    return list(FMHY_SOURCES)


def fetch_mangayomi_extensions(refresh: bool = False) -> List[Dict[str, Any]]:
    """Fetch active extension index from kodjodevf/mangayomi-extensions.
    Cached locally for 24h to avoid rate-limiting."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not refresh and os.path.exists(MANGAYOMI_CACHE):
        try:
            mtime = os.path.getmtime(MANGAYOMI_CACHE)
            if time.time() - mtime < 86400:
                with open(MANGAYOMI_CACHE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass

    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            MANGAYOMI_INDEX_URL,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; ManhwaRecapStudio/1.0)'}
        )
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            with open(MANGAYOMI_CACHE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            return data
    except Exception as e:
        # Fallback to pre-bundled seed sources if network fails
        return [{'name': s['name'], 'lang': 'en', 'baseUrl': s['url']} for s in FMHY_SOURCES]


def is_mangayomi_supported(url: str) -> bool:
    """Check if URL host matches any known Mangayomi or FMHY source."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(':')[0]
        if host.startswith('www.'):
            host = host[4:]
        return any(host == dom or host.endswith('.' + dom) for dom in DOMAINS_MAP)
    except Exception:
        return False


def get_source_for_url(url: str) -> Optional[Dict[str, Any]]:
    """Resolve matched FMHY/Mangayomi source config for a URL."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(':')[0]
        if host.startswith('www.'):
            host = host[4:]
        for dom, s in DOMAINS_MAP.items():
            if host == dom or host.endswith('.' + dom):
                return s
    except Exception:
        pass
    return None


def create_mangayomi_provider(base_provider_cls):
    """Factory to create MangayomiProvider deriving from Provider."""
    class MangayomiProvider(base_provider_cls):
        name = 'mangayomi'
        label = 'Mangayomi / FMHY Mirror'
        support = 'supported'

        def match_url(self, url):
            return is_mangayomi_supported(url)

        def normalize_series_url(self, url):
            s = get_source_for_url(url)
            p = urllib.parse.urlparse(url)
            path = p.path.rstrip('/')
            # Remove /chapter/\d+ or /episode-\d+
            path = re.sub(r'/(?:chapter|ch|episode|ep)[/-]?\d+.*$', '', path, flags=re.I)
            return f"{p.scheme}://{p.netloc}{path}"

        def series_key(self, url):
            s = get_source_for_url(url)
            sid = s['id'] if s else 'mirror'
            p = urllib.parse.urlparse(url)
            norm = self.normalize_series_url(url)
            slug = norm.split('/')[-1] or norm.split('/')[-2] if '/' in norm else 'series'
            return f"mangayomi:{sid}:{slug}"

        def discover_chapters(self, series_url, _fetcher=None):
            # For mgeko, all chapters are at /all-chapters/
            fetch_url = series_url
            if 'mgeko.cc' in series_url and not series_url.rstrip('/').endswith('/all-chapters'):
                fetch_url = series_url.rstrip('/') + '/all-chapters/'
            html = _fetcher(fetch_url) if _fetcher else ''
            if not html:
                try:
                    ctx = ssl._create_unverified_context()
                    req = urllib.request.Request(fetch_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                        html = resp.read().decode('utf-8', errors='ignore')
                except Exception as e:
                    # try base URL if all-chapters failed
                    if fetch_url != series_url:
                        try:
                            req = urllib.request.Request(series_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                                html = resp.read().decode('utf-8', errors='ignore')
                        except Exception:
                            raise ValueError(f"failed to fetch series page: {e}")
                    else:
                        raise ValueError(f"failed to fetch series page: {e}")

            # Extract chapter URLs and numbers
            pattern = re.compile(r'href=["\']([^"\']*(?:chapter|ch|episode|ep)[/-]?(\d+(?:\.\d+)?)[^"\']*)["\']', re.I)
            found = {}
            for m in pattern.finditer(html):
                href, c_num = m.group(1), m.group(2)
                full_url = urllib.parse.urljoin(series_url, href)
                c_norm = self.normalize_chapter_id(c_num)
                if c_norm not in found:
                    found[c_norm] = full_url

            if not found:
                raise ValueError("no chapters discovered on series page")

            # Sort ascending by chapter float
            def _key(c):
                try:
                    return float(c[0])
                except Exception:
                    return 0.0

            return sorted(list(found.items()), key=_key)

        def chapter_url(self, series_url, chapter_id):
            c = self.normalize_chapter_id(chapter_id)
            if 'mgeko.cc' in series_url:
                try:
                    # check all-chapters to get exact slug
                    all_url = series_url.rstrip('/') + '/all-chapters/'
                    ctx = ssl._create_unverified_context()
                    req = urllib.request.Request(all_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                        h = resp.read().decode('utf-8', errors='ignore')
                    pat = re.compile('href=["\x27]([^"\x27]*(?:chapter|ch|episode|ep)[/-]?(\d+(?:\.\d+)?)[^"\x27]*)["\x27]', re.I)
                    for m in pat.finditer(h):
                        if self.normalize_chapter_id(m.group(2)) == c:
                            return urllib.parse.urljoin(all_url, m.group(1))
                except Exception:
                    pass
            return f"{series_url.rstrip('/')}/chapter/{c}"

        def extract_pages(self, html):
            pages = super().extract_pages(html)
            return pages

        def quality_check(self, pages):
            if not pages or len(pages) < 2:
                return False, 'fewer than 2 pages found'
            return True, f"{len(pages)} pages extracted"

    return MangayomiProvider()
