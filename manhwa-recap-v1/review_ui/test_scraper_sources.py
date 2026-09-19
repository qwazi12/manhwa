"""Downloading a chapter must not silently return one image.

Observed on WEBTOON episode 129 of The Stellar Swordmaster: the ingest produced
exactly ONE image. Three defects stacked, and each one alone would have been
survivable:

1. The generic extractor found 139 links, 138 of them real strip slices on
   `webtoon-phinf.pstatic.net` and one square thumbnail on a DIFFERENT host,
   `swebtoon-phinf.pstatic.net`.
2. WEBTOON's CDN enforces hotlink protection. The scraper sent only a
   User-Agent, so all 138 real slices returned **403**. The lone thumbnail sat
   on a host with no such protection and downloaded fine.
3. The only download guard was "did EVERYTHING fail". 1 of 139 is not
   everything, so the chapter ingested as a single image and raised nothing.

A provider-specific extractor already existed and was never wired into the
download path. These tests pin all three fixes, with no network access.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


WT = ("https://www.webtoons.com/en/action/the-stellar-swordmaster/"
      "s2-episode-129/viewer?title_no=5988&episode_no=129")
ASURA = "https://asurascans.com/comics/i-am-the-fated-villain-6f7fe6eb/chapter/353"


class FakeResp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    import urllib.request
    import scraper

    # 40 real strip slices + one square thumbnail on the other host
    slices = "".join(
        f'<img class="_images" data-url="https://webtoon-phinf.pstatic.net/'
        f'2026072{i%9}_{i}/strip_{i}.jpg" src="data:image/gif;base64,x">'
        for i in range(1, 41))
    thumb = ('<img src="https://swebtoon-phinf.pstatic.net/2026_238/'
             '21__Thumb_Square_5988.jpg">')
    page = "<html>" + thumb + slices + "</html>"

    seen = {"requests": []}
    real_urlopen = urllib.request.urlopen

    def fake_urlopen(req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        hdrs = {k.lower(): v for k, v in
                (getattr(req, "headers", {}) or {}).items()}
        seen["requests"].append((url, hdrs))
        if url.startswith("https://www.webtoons.com"):
            return FakeResp(page.encode("utf-8"))
        # The CDN's actual behaviour: no Referer, no image.
        if "webtoon-phinf.pstatic.net" in url and "swebtoon" not in url:
            if "referer" not in hdrs:
                raise Exception("HTTP Error 403: Forbidden")
        return FakeResp(b"\x89PNG" + b"\x00" * 900)

    urllib.request.urlopen = fake_urlopen
    try:
        out = os.path.join(tempfile.mkdtemp(prefix="scr_"), "pages")
        paths = scraper.download_chapter(WT, out)

        img_reqs = [(u, h) for u, h in seen["requests"]
                    if "pstatic.net" in u]
        check("the WEBTOON extractor is used, not the generic rule",
              len(paths) == 40)
        check("...so the square thumbnail from the other host is excluded",
              not any("swebtoon" in u for u, _ in img_reqs))
        check("every image request carries a Referer",
              img_reqs and all("referer" in h for _, h in img_reqs))
        check("...and it is the chapter URL, which is what a browser sends",
              all(h.get("referer") == WT for _, h in img_reqs))
        check("all 40 pages land on disk", len(os.listdir(out)) == 40)

        # ---- the partial-failure guard
        def fail_most(req, *a, **kw):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.startswith("https://www.webtoons.com"):
                return FakeResp(page.encode("utf-8"))
            if "strip_1.jpg" in url:
                return FakeResp(b"\x89PNG" + b"\x00" * 900)
            raise Exception("HTTP Error 403: Forbidden")

        urllib.request.urlopen = fail_most
        out2 = os.path.join(tempfile.mkdtemp(prefix="scr2_"), "pages")
        try:
            scraper.download_chapter(WT, out2)
            check("a chapter that loses most of its art is REFUSED", False)
        except RuntimeError as e:
            check("a chapter that loses most of its art is REFUSED", True)
            check("...saying how many of how many arrived",
                  "of 40" in str(e))
            check("...and pointing at request headers as the likely cause",
                  "headers" in str(e).lower())
        except Exception:
            check("a chapter that loses most of its art is REFUSED", False)

        # ---- Asura must be untouched by all of this
        # All pages of a chapter served from ONE directory — that is the
        # signal find_page_urls keys on ("keep the directory holding the
        # most"), and it is how Asura actually serves a chapter. A fixture
        # giving each page its own folder tests the heuristic's failure mode,
        # not Asura.
        asura_html = ("<html>"
                      '<img src="https://gg.asuracomic.net/assets/logo.png">'
                      + "".join(
            f'<img src="https://gg.asuracomic.net/storage/media/8891/'
            f'conversions/{i:03d}-optimized.webp">' for i in range(1, 13))
            + "</html>")

        def asura_urlopen(req, *a, **kw):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            seen["requests"].append((url, {}))
            if url.startswith("https://asurascans.com"):
                return FakeResp(asura_html.encode("utf-8"))
            return FakeResp(b"\x89PNG" + b"\x00" * 900)

        import providers as P
        check("Asura does NOT override extract_pages, so it keeps the "
              "generic path it has always used",
              type(P.ASURA).extract_pages is P.Provider.extract_pages)
        check("...while WEBTOON does override it",
              type(P.WEBTOON).extract_pages is not P.Provider.extract_pages)

        urllib.request.urlopen = asura_urlopen
        out3 = os.path.join(tempfile.mkdtemp(prefix="scr3_"), "pages")
        got = scraper.download_chapter(ASURA, out3)
        check("an Asura chapter still downloads every page", len(got) == 12)
        check("...and its furniture is still dropped by the generic rule",
              not any("logo.png" in u for u, _ in seen["requests"]))
    finally:
        urllib.request.urlopen = real_urlopen

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
