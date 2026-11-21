"""Chapter title cards showed the source's number after the series name
("Doom Breaker 3197", "The Extras Academy Survival Guide 6465"); owner,
2026-11-20. The card drops it; project ids are unchanged."""
import ingest
import server


def test_site_number_is_dropped_from_the_card():
    assert server._card_series_name("doom-breaker-3197") == "Doom Breaker"
    assert server._card_series_name("the-extras-academy-survival-guide-6465") == "The Extras Academy Survival Guide"
    assert server._card_series_name("too-many-heroes-for-the-demon-lord-9702") == "Too Many Heroes For The Demon Lord"


def test_names_without_a_number_are_unchanged():
    assert server._card_series_name("murim-login") == "Murim Login"
    assert server._card_series_name("swordmasters-youngest-son-f886a8af") == "Swordmasters Youngest Son"
    assert server._card_series_name("") == ""


def test_project_ids_keep_the_number():
    assert ingest.clean_series_slug("doom-breaker-3197") == "doom-breaker-3197"
