"""abtest.py: which thumbnails B/C a video tests, title vs thumbnail test, ledger."""
import json
import os

import pytest

import abtest as ab

REC = {"chosen": {"concept_id": "c0", "composition": "cover-badge", "focal_panel": "p1"},
       "concepts": [{"id": "c0", "composition": "cover-badge", "focal_panel": "p1"},
                    {"id": "c1", "composition": "cover-center", "focal_panel": "p1"},
                    {"id": "c2", "composition": "split", "focal_panel": "p1"},
                    {"id": "c3", "composition": "panel-hero", "focal_panel": "p2"},
                    {"id": "c4", "composition": "chapter-card", "focal_panel": "p1"}]}


def test_variants_are_the_least_like_the_posted_thumbnail():
    got = [c["id"] for c in ab.thumb_variants(REC)]
    # another design AND another panel first; the cover-only twin of A never
    assert got == ["c3", "c2"]


def test_variants_one_per_design_and_none_without_concepts():
    rec = {"chosen": {"concept_id": "c0"}, "concepts": [{"id": "c0", "composition": "split"},
                                                         {"id": "c1", "composition": "split"}]}
    assert ab.thumb_variants(rec) == []
    assert ab.thumb_variants(None) == [] and ab.thumb_variants({}) == []


def test_no_chosen_concept_still_gives_two():
    rec = {"concepts": REC["concepts"]}
    assert len(ab.thumb_variants(rec)) == 2


def test_plan_title_test_when_series_has_title_b():
    p = ab.plan("[1] A | S", "[1] B | S", ["c3", "c2"])
    assert p["type"] == "title" and p["titles"] == ["[1] A | S", "[1] B | S"] and p["thumbnails"] == []


def test_plan_thumbnail_test_otherwise():
    p = ab.plan("[1] A | S", None, ["c3", "c2", "c4"], ["title B: too long"])
    assert p["type"] == "thumbnail" and p["thumbnails"] == ["c3", "c2"] and "too long" in p["why"]
    assert ab.plan("[1] A | S", "[1] A | S", [])["type"] is None       # B same as A: no test


def test_ledger_keeps_history(tmp_path):
    root = str(tmp_path)
    ab.mark(root, "abcdefghijk", "running", actor="agent", test_type="thumbnail", variants=["A", "B"])
    e = ab.mark(root, "abcdefghijk", "done", actor="owner", note="B won")
    assert e["status"] == "done" and e["test_type"] == "thumbnail" and e["history"][0]["status"] == "running"
    assert json.load(open(os.path.join(root, ab.LEDGER)))["abcdefghijk"]["note"] == "B won"
    with pytest.raises(ValueError):
        ab.mark(root, "abcdefghijk", "won")
    with pytest.raises(ValueError):
        ab.mark(root, "short", "running")
