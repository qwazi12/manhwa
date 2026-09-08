r"""Chapter-page URL extraction tests (Session 26).

Why this exists: Doctors Rebirth ch1 ingested as 3 pages instead of 11 and
produced a 66-second "chapter" with no error. The scraper matched a bare
three-digit filename (/\d{3}.webp) and skipped every split page part
(002_p1.webp, 002_p2.webp, ...); the generic fallback that would have caught
them was gated on `if not image_urls`, so a PARTIAL match suppressed it.

Run: python3 test_scraper.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scraper

CDN = "https://cdn.asurascans.com/asura-images"


def _page(urls, extra=()):
    """Minimal chapter HTML: images in document order, plus site furniture."""
    body = "".join(f'<img src="{u}"/>' for u in urls)
    junk = "".join(f'<img src="{u}"/>' for u in extra)
    return f"<html><body>{junk}{body}</body></html>"


def main():
    r = []

    # the real Doctors Rebirth shape: split page parts + a cover
    real = [f"{CDN}/chapters/doctors-rebirth/1/001.webp",
            f"{CDN}/chapters/doctors-rebirth/1/002_p1.webp",
            f"{CDN}/chapters/doctors-rebirth/1/002_p2.webp",
            f"{CDN}/chapters/doctors-rebirth/1/003_p1.webp",
            f"{CDN}/chapters/doctors-rebirth/1/003_p2.webp",
            f"{CDN}/chapters/doctors-rebirth/1/004_p1.webp",
            f"{CDN}/chapters/doctors-rebirth/1/005.webp"]
    furniture = [f"{CDN}/covers/doctors-rebirth.283895.webp",
                 "https://site.example/assets/logo.png",
                 "https://site.example/assets/avatar-user.jpg"]
    got = scraper.find_page_urls(_page(real, furniture))
    r.append(("split page parts (002_p1, 002_p2) are NOT skipped", got == real))
    r.append(("the cover is excluded", all("/covers/" not in u for u in got)))
    r.append(("site furniture is excluded",
              all("logo" not in u and "avatar" not in u for u in got)))
    r.append(("reading order follows the document", got == real))

    # the old bug, stated directly: bare-\d{3} matching must not truncate
    r.append(("all 7 pages found, not just the 3 bare-numbered ones",
              len(got) == 7))

    # majority-directory rule: chapter dir wins over a scattered few
    mixed = _page(real, [f"{CDN}/promo/a.webp", f"{CDN}/promo/b.webp"])
    got2 = scraper.find_page_urls(mixed)
    r.append(("the directory holding the most images wins", got2 == real))

    # gap detection
    gappy = [f"{CDN}/chapters/x/1/001.webp",
             f"{CDN}/chapters/x/1/005.webp",
             f"{CDN}/chapters/x/1/006.webp"]
    w = scraper._sequence_warning(gappy)
    r.append(("missing page numbers raise a loud warning",
              bool(w) and "GAPS" in w and "[2, 3, 4]" in w))
    r.append(("a contiguous chapter warns about nothing",
              scraper._sequence_warning(real) == ""))
    r.append(("split parts do not count as gaps",
              scraper._sequence_warning(
                  [f"{CDN}/c/1/001.webp", f"{CDN}/c/1/002_p1.webp",
                   f"{CDN}/c/1/002_p2.webp", f"{CDN}/c/1/003.webp"]) == ""))

    # a page with no images at all yields nothing (download_chapter raises)
    # the gap/low-count check must be WIRED IN, not just defined — the first
    # cut of this fix left _sequence_warning unreachable from download_chapter
    import inspect
    src = inspect.getsource(scraper.download_chapter)
    r.append(("download_chapter actually CALLS the gap check",
              "scrape_warning(" in src))
    r.append(("download_chapter exposes the warning to callers",
              "LAST_WARNING" in src and hasattr(scraper, "LAST_WARNING")))
    short = [f"{CDN}/c/1/001.webp", f"{CDN}/c/1/002.webp"]
    r.append(("an implausibly short chapter is flagged",
              "incomplete scrape" in scraper.scrape_warning(short)))
    r.append(("a full-length chapter is not flagged as short",
              scraper.scrape_warning(real) == ""))
    r.append(("gaps and shortness are reported together",
              scraper.scrape_warning(gappy).count("—") >= 1
              and "GAPS" in scraper.scrape_warning(gappy)
              and "incomplete scrape" in scraper.scrape_warning(gappy)))

    r.append(("a page with no images yields no urls",
              scraper.find_page_urls("<html><body>nothing</body></html>") == []))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
