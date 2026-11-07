"""Covers are the series art, never a site's generated share card (owner,
2026-11-07: Library showed Asura's og card with title/rating). No network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers as P  # noqa: E402
import thumbnail_studio as T  # noqa: E402

ASURA = ('<meta property="og:image" content="https://asurascans.com/og/comics/murim-login.jpg">'
         '<script type="application/ld+json">{"url":"https://asurascans.com/comics/murim-login-ab39de10",'
         '"image":{"@type":"ImageObject","url":"https://cdn.asurascans.com/asura-images/covers/murim-login.626801.webp","width":720}}</script>'
         '<img src="https://cdn.asurascans.com/asura-images/covers/murim-login.626801-400.webp" '
         'data-full-src="https://cdn.asurascans.com/asura-images/covers/murim-login.626801.webp">')
REAL = "https://cdn.asurascans.com/asura-images/covers/murim-login.626801.webp"


def test_asura_share_card_is_skipped_for_the_full_cover():
    assert P.is_share_card("https://asurascans.com/og/comics/murim-login.jpg")
    assert not P.is_share_card(REAL)
    assert P.cover_from_html(ASURA) == REAL
    assert P.release_info(P.by_name("asura"), ASURA)["cover"] == REAL
    assert T._pick_cover_url(ASURA, "https://asurascans.com/comics/murim-login-ab39de10/") == REAL


def test_json_ld_alone_and_card_only_pages():
    only_ld = ASURA.split("<img")[0]
    assert P.cover_from_html(only_ld) == REAL
    card_only = '<meta property="og:image" content="https://asurascans.com/og/comics/x.jpg">'
    assert P.cover_from_html(card_only) is None
    assert T._pick_cover_url(card_only, "https://asurascans.com/comics/x/") == ""


def test_webtoon_og_image_still_used():
    wt = '<meta property="og:image" content="https://swebtoon-phinf.pstatic.net/x/poster.jpg?type=crop540_540">'
    assert P.cover_from_html(wt).startswith("https://swebtoon-phinf.pstatic.net/")
