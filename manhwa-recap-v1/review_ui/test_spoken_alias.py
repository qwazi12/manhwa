"""The narration's opening alias is a real other title (live 2026-11-19 bug)."""
import series_pack as sp


def test_site_id_and_character_names_are_never_spoken():
    pack = {"title_en": "The Stellar Swordmaster",
            "aliases": ["The Stellar Swordmaster 5988", "Vlad"],
            "characters": [{"name": "Vlad", "aliases": []}]}
    assert {sp.spoken_alias(pack, n) for n in range(1, 10)} == {""}


def test_cleaned_alt_titles_are_used_when_the_pack_has_them():
    pack = {"title_en": "Murim Psychopath", "series_name_en": "Murim Psychopath",
            "aliases": ["Dong Bongsu", "Psychopath of the Murim"],
            "series_name_alt": ["Psychopath of the Murim", "Crazy Demon"],
            "characters": [{"name": "Dong Bongsu"}]}
    spoken = {sp.spoken_alias(pack, n) for n in range(1, 10)}
    assert spoken == {"Psychopath of the Murim", "Crazy Demon"}


def test_respelled_own_title_and_character_alias_filtered_without_alt_list():
    pack = {"title_en": "Magic Academy's Genius Blinker",
            "aliases": ["Magic Academy's Geniu's Blinker", "Blink: Master of the Magic Academy", "Mercenary King"],
            "characters": [{"name": "Cecil", "aliases": ["Mercenary King"]}]}
    assert {sp.spoken_alias(pack, n) for n in range(1, 10)} == {"Blink: Master of the Magic Academy"}


def test_empty_pack():
    assert sp.spoken_alias(None, 5) == "" and sp.spoken_alias({}, "12") == ""
