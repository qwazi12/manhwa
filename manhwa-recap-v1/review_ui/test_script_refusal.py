"""Owner review, 2026-11-17. Two script faults found in the chapters made on
2026-11-16:

1. A Regressor's Tale ch.37: for one unit the model refused ("I cannot write a
   line-by-line or panel-by-panel script for this work, but I can provide a
   concise summary...") and that answer was cut into six spoken lines. The
   chapter still arrived at "Check the board". A refusal is now asked again
   and, if it stays a refusal, fails the script step.
2. Five chapters opened "X, also known as X": the alternate title was the same
   title spelled another way.

No network. Run: python3 -m pytest test_script_refusal.py -q"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.dirname(HERE)
for p in (HERE, RECAP):
    if p not in sys.path:
        sys.path.insert(0, p)
import narrate  # noqa: E402
import narration_guard  # noqa: E402

REFUSAL = ("I cannot write a line-by-line or panel-by-panel script for this work, but I can provide a concise "
           "summary of the chapter. In this chapter, Seo Eunhyun awakens to his sixth regression cycle alongside "
           "his stranded coworkers. Would you like a summary of the next chapter?")
GOOD = ("[ID: page001_panel_062] His face radiant with joy, he looked up at the bright sky.\n"
        "[ID: page001_panel_063] Floating above the clouds, he rose through the open air.")


@pytest.mark.parametrize("text", [
    "I cannot write a line-by-line or panel-by-panel script for this work, but I can provide a concise summary.",
    "I can't provide a narration for this chapter.",
    "I am unable to write the recap, but I can offer a brief overview instead.",
    "Here is a panel-by-panel recap of the fight.",
])
def test_a_refusal_is_recognised(text):
    assert narration_guard.is_assistant_text(text)


@pytest.mark.parametrize("text", [
    "He could not write the letter, so he burned it instead.",
    "\"I cannot continue,\" the knight gasped, dropping to one knee.",
    "\"I won't help you,\" she said, turning her back on the demon lord.",
    "\"I can't provide what you ask,\" the elder admitted, \"but I can offer you shelter.\"",
    "Line by line, the ancient script on the wall began to glow.",
    "The summary execution was carried out at dawn.",
])
def test_story_lines_are_not_mistaken_for_a_refusal(text):
    assert not narration_guard.is_assistant_text(text)


def test_a_refused_unit_is_asked_again_and_the_good_answer_is_used():
    answers = [REFUSAL, GOOD]
    asked = []

    def call():
        asked.append(1)
        return answers.pop(0)

    assert narrate.draft_or_fail(call, 7) == GOOD and len(asked) == 2


def test_a_unit_that_stays_a_refusal_fails_the_script_step():
    asked = []
    with pytest.raises(ValueError) as e:
        narrate.draft_or_fail(lambda: asked.append(1) or REFUSAL, 7)
    assert len(asked) == narrate.DRAFT_TRIES
    assert "unit 7" in str(e.value) and "I cannot write" in str(e.value)


def test_a_normal_draft_costs_one_request():
    asked = []
    assert narrate.draft_or_fail(lambda: asked.append(1) or GOOD, 1) == GOOD and len(asked) == 1


def test_a_tagged_draft_with_a_stray_assistant_line_is_kept_for_the_render_check():
    stray = GOOD + "\n[ID: page001_panel_064] Would you like a summary of the next chapter?"
    asked = []
    assert narrate.draft_or_fail(lambda: asked.append(1) or stray, 3) == stray
    assert len(asked) == narrate.DRAFT_TRIES


@pytest.mark.parametrize("title, alias", [
    ("Omniscient Reader’s Viewpoint", "Omniscient Reader's Viewpoint"),
    ("Omniscient Reader’s Viewpoint", "Omniscient Readers Viewpoint"),
    ("A Regressor’s Tale of Cultivation", "A Regressors Tale Of Cultivation"),
])
def test_the_same_title_spelled_another_way_is_not_read_out_twice(title, alias):
    rule = narrate.opening_rule(title, alias)
    assert "also known as" not in rule and f'exactly once as "{title}"' in rule


def test_a_real_alternate_title_is_still_read_out():
    rule = narrate.opening_rule("Revenge of the Iron-Blooded Sword Hound", "Revenge of the Baskerville Bloodhound")
    assert "Revenge of the Iron-Blooded Sword Hound, also known as Revenge of the Baskerville Bloodhound" in rule
    assert narrate.opening_rule("", "x") == "" and narrate.opening_rule("x", "") == ""
