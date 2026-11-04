import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import beat_segmenter as b


def test_number_abbreviation_does_not_end_sentence():
    out = b.split_sentences("In the brutal depths of Fog Land, No. 182 sprinted across the platform. Then he ran.")
    assert out == ["In the brutal depths of Fog Land, No. 182 sprinted across the platform.", "Then he ran."]


def test_plain_no_still_ends_sentence():
    assert b.split_sentences("He said no. Then he ran.") == ["He said no.", "Then he ran."]


def test_other_number_abbreviations():
    out = b.split_sentences("He reached Lv. 99 before Ch. 5 began. It was fast.")
    assert out == ["He reached Lv. 99 before Ch. 5 began.", "It was fast."]
