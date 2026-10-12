"""Backup source lookup on MGeko (2026-10-11): found by title with a strict
match, apostrophe variants tried, a different series never taken."""
import mangayomi as my


def fake(pages):
    def get(url):
        for k, v in pages.items():
            if url.startswith(k):
                return v
        raise OSError("404")
    return get


LIST = '<a href="/reader/en/x-chapter-1-eng-li/">chapter-1</a>'


def test_apostrophe_variant_is_tried():
    get = fake({"https://www.mgeko.cc/manga/the-extras-academy-survival-guide/all-chapters/": LIST})
    assert my.find_mgeko_series("The Extra's Academy Survival Guide", ["the-extra-s-academy-survival-guide"],
                                _get_fn=get) == "https://www.mgeko.cc/manga/the-extras-academy-survival-guide"


def test_search_takes_a_tagged_slug_of_the_same_title_only():
    search = ('<a href="/manga/demon-in-mount-hua/">x</a><a href="/manga/mount-hua-sects-greatest-genius/">x</a>'
              '<a href="/manga/return-of-the-mount-hua-sect-mg1/">x</a>')
    get = fake({"https://www.mgeko.cc/search/": search})
    assert my.find_mgeko_series("Return of the Mount Hua Sect", [], _get_fn=get) == \
        "https://www.mgeko.cc/manga/return-of-the-mount-hua-sect-mg1"
    get = fake({"https://www.mgeko.cc/search/": '<a href="/manga/o5dr-the-former-supreme/">x</a>'})
    assert my.find_mgeko_series("The Former Supreme", [], _get_fn=get).endswith("/o5dr-the-former-supreme")


def test_a_different_series_is_never_taken():
    search = ('<a href="/manga/as-a-villain-its-not-too-much-to-overpower-the-protagonist-right/">x</a>'
              '<a href="/manga/me-the-heavenly-destined-villain-mg1/">x</a>')
    get = fake({"https://www.mgeko.cc/search/": search})
    assert my.find_mgeko_series("I Am the Fated Villain", [], _get_fn=get) is None
    assert not my._same_series("the-regressed-mercenarys-machinations", "the-regressed-mercenary-has-a-plan")
