"""Owner, 2026-11-17: Home showed a broken picture for seven series (Omniscient
Reader among them). Their first source (Asura) stored a share card and refused
the studio, so the real cover could not be read from its page, and the backup
source's own cover was never tried. The cover route now tries every source in
turn, and keeps showing an older saved cover when no source answers.
No network. Run: python3 -m pytest test_cover_fallback.py -q"""
import io
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
from PIL import Image  # noqa: E402

ASURA = {"source": "asura", "series_url": "https://asurascans.com/comics/orv-ab39de10/",
         "cover": "https://asurascans.com/og/comics/orv.jpg"}
MGEKO = {"source": "mangayomi", "series_url": "https://www.mgeko.cc/manga/manga-1003",
         "cover": "https://www.mgeko.cc/media/manga_covers/l_30.jpg"}


def _jpeg(size=(300, 430)):
    """A picture with many colours, as cover art has."""
    b = io.BytesIO()
    Image.merge("RGB", [Image.linear_gradient("L").resize(size)] * 3).save(b, "JPEG")
    return b.getvalue()


def _loading_picture():
    """A site's placeholder: one flat colour with a thin ring, as mgeko serves."""
    from PIL import ImageDraw
    im = Image.new("RGB", (600, 800), (42, 42, 42))
    ImageDraw.Draw(im).ellipse((170, 270, 430, 530), outline=(240, 240, 240), width=4)
    b = io.BytesIO()
    im.save(b, "GIF")
    return b.getvalue()


class _Reply:
    def __init__(self, data, mt="image/jpeg"):
        self._d, self._mt = data, mt
        self.headers = self

    def get_content_type(self):
        return self._mt

    def read(self, n=-1):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """The cover route with a two-source series; `web` maps a URL to its reply
    (bytes, a _Reply, or an Exception to raise); `asked` lists what was fetched."""
    import urllib.request
    import providers
    import server
    import watchlist
    wl = tmp_path / "wl"
    (wl / "_covers").mkdir(parents=True)
    srs = {"id": "orv", "mirrors": [dict(ASURA), dict(MGEKO)]}
    monkeypatch.setattr(server, "_wl_root", lambda: str(wl))
    monkeypatch.setattr(watchlist, "load", lambda root: {"series": [srs]})
    monkeypatch.setattr(watchlist, "find", lambda w, sid: srs if sid == "orv" else None)
    web, asked = {}, []

    def reply(url):
        asked.append(url)
        r = web.get(url, OSError("HTTP Error 403: Forbidden"))
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, _Reply) else _Reply(r)

    monkeypatch.setattr(providers, "fetch", lambda url, timeout=None: reply(url).read().decode())
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None, context=None: reply(req.full_url))
    return server, srs, web, asked, wl / "_covers"


def test_the_backup_source_cover_is_used_when_the_first_source_refuses(studio):
    server, _, web, asked, covers = studio
    web[MGEKO["cover"]] = _jpeg()
    r = server.api_watchlist_cover("orv")
    assert r.media_type == "image/jpeg" and r.body == web[MGEKO["cover"]]
    assert asked == [ASURA["series_url"], MGEKO["cover"]]         # the share card itself is never fetched
    assert (covers / "orv.src").read_text() == MGEKO["cover"]
    asked.clear()
    r = server.api_watchlist_cover("orv")                          # second view: the saved file, no site asked
    assert r.path == str(covers / "orv.jpg") and asked == []


def test_the_first_source_cover_still_wins_when_it_answers(studio):
    server, _, web, asked, covers = studio
    real = "https://cdn.asurascans.com/covers/orv.webp"
    web[ASURA["series_url"]] = f'<img data-full-src="{real}">'.encode()
    web[real] = _Reply(_jpeg(), "image/webp")
    web[MGEKO["cover"]] = _jpeg((10, 10))
    r = server.api_watchlist_cover("orv")
    assert r.media_type == "image/webp" and (covers / "orv.src").read_text() == real
    assert MGEKO["cover"] not in asked


def test_a_block_page_is_not_saved_as_a_cover(studio):
    server, srs, web, asked, covers = studio
    srs["mirrors"][0]["cover"] = "https://asurascans.com/covers/orv.jpg"       # a real cover link, blocked today
    web[srs["mirrors"][0]["cover"]] = _Reply(b"<html>Just a moment...</html>", "text/html")
    web[MGEKO["cover"]] = _jpeg()
    r = server.api_watchlist_cover("orv")
    assert r.body == web[MGEKO["cover"]] and (covers / "orv.src").read_text() == MGEKO["cover"]


def test_an_old_saved_cover_is_shown_when_no_source_answers(studio):
    server, _, web, asked, covers = studio
    (covers / "orv.jpg").write_bytes(_jpeg())
    (covers / "orv.src").write_text("https://cdn.asurascans.com/covers/orv.webp")
    old = time.time() - 30 * 86400
    os.utime(covers / "orv.jpg", (old, old))
    r = server.api_watchlist_cover("orv")                          # every source refuses
    assert r.path == str(covers / "orv.jpg")


def test_no_cover_anywhere_is_still_an_error(studio):
    server, srs, web, asked, covers = studio
    with pytest.raises(server.HTTPException) as e:
        server.api_watchlist_cover("orv")
    assert e.value.status_code == 502
    srs["mirrors"][1]["cover"] = None
    with pytest.raises(server.HTTPException) as e:
        server.api_watchlist_cover("orv")
    assert e.value.status_code == 404
    with pytest.raises(server.HTTPException) as e:
        server.api_watchlist_cover("unknown-series")
    assert e.value.status_code == 404


def test_a_sites_loading_picture_is_replaced_by_the_cover_its_page_shows(studio):
    """Damn Reincarnation, 2026-11-17: mgeko answered the stored cover link with
    its loading animation; the real cover is the same file on its picture server."""
    server, _, web, asked, covers = studio
    shown = "https://imgsrv5.com/avatar/288x412/media/manga_covers/l_30.jpg"
    web[MGEKO["cover"]] = _Reply(_loading_picture(), "image/gif")
    web[MGEKO["series_url"]] = (f'<meta property="og:image" content="{MGEKO["cover"]}">'
                                f'<img src="https://imgsrv5.com/avatar/288x412/media/manga_covers/default-placeholder.png" '
                                f'data-src="{shown}"><a href="https://evil.example/x.jpg">x</a>').encode()
    web[shown] = _jpeg((288, 412))
    r = server.api_watchlist_cover("orv")
    assert r.body == web[shown]
    assert asked == [ASURA["series_url"], MGEKO["cover"], MGEKO["series_url"], shown]
    asked.clear()
    r = server.api_watchlist_cover("orv")                          # second view: the saved file, no site asked
    assert r.path == str(covers / "orv.jpg") and asked == []


def test_a_loading_picture_is_never_shown_as_the_cover(studio):
    server, _, web, asked, covers = studio
    web[MGEKO["cover"]] = _Reply(_loading_picture(), "image/gif")
    web[MGEKO["series_url"]] = b"<html>no other picture</html>"
    with pytest.raises(server.HTTPException) as e:
        server.api_watchlist_cover("orv")
    assert e.value.status_code == 404 and not list(covers.glob("orv.*"))
    (covers / "orv.jpg").write_bytes(_loading_picture())           # one saved by the earlier version
    (covers / "orv.src").write_text(MGEKO["cover"])
    with pytest.raises(server.HTTPException):
        server.api_watchlist_cover("orv")
    web[MGEKO["cover"]] = _jpeg()                                  # the site has a real cover again
    assert server.api_watchlist_cover("orv").body == web[MGEKO["cover"]]


def test_real_covers_are_not_mistaken_for_a_loading_picture():
    import server
    assert not server._flat_picture(_jpeg()) and server._flat_picture(_loading_picture())
    dark = Image.new("RGB", (400, 600), (5, 5, 5))                 # a dark cover: 45% black, the rest art
    dark.paste(Image.merge("RGB", [Image.linear_gradient("L").resize((400, 330))] * 3), (0, 270))
    b = io.BytesIO()
    dark.save(b, "PNG")
    assert not server._flat_picture(b.getvalue())
    assert not server._flat_picture(b"not a picture")
