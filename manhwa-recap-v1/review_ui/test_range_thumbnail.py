"""A merged (range) video's thumbnail is built from its chapters' panels and
carries the range ("CH 31-32"), never one chapter's number (2026-10-11)."""
import json
import os

from PIL import Image

import range_compile as rc
import thumbnail_studio as t


def _chapter(root, n):
    d = root / f"hound_{n}"
    (d / "crops").mkdir(parents=True)
    descs = []
    for i in range(3):
        Image.new("RGB", (800, 1000), (40 * i + 20 * n, 90, 160)).save(d / "crops" / f"p{i}.png")
        descs.append({"panel_id": f"p{i}", "file": f"p{i}.png", "width": 800, "height": 1000,
                      "visual_description": f"close-up of the hero, panel {i} of chapter {n}"})
    (d / "descriptions.json").write_text(json.dumps(descs))
    (d / "project.json").write_text(json.dumps({"series": "Hound", "chapter": str(n)}))


def test_range_ranks_its_chapters_panels_and_keeps_the_range_number(tmp_path):
    _chapter(tmp_path, 31)
    _chapter(tmp_path, 32)
    rdir, meta = rc.write_project(str(tmp_path), "hound", "Hound", 31, 32)
    panels, report = t.rank_panels_report(rdir, 8)
    assert panels, report                                     # before: none -> no thumbnail
    assert {p["panel_id"].split(":")[0] for p in panels} <= {"ch31", "ch32"}
    assert all(os.path.isabs(p["file"]) and os.path.exists(p["file"]) for p in panels)
    concepts = t.build_concepts(rdir, meta, {}, "Full 1H | Hound Chapter 31-32 | Manhwa Recap")
    assert concepts and all(c["chapter"] == "31-32" for c in concepts)
    # a regenerate skips panels already offered, across chapters
    used = [c["focal_panel"] for c in concepts]
    again, _r = t.rank_panels_report(rdir, 8, exclude=used)
    assert again[0]["panel_id"] not in used or len(again) <= len(set(used))


def test_a_plain_chapter_is_unchanged(tmp_path):
    _chapter(tmp_path, 31)
    panels, _r = t.rank_panels_report(str(tmp_path / "hound_31"), 8)
    assert panels and not any(":" in p["panel_id"] for p in panels)
