"""Owner, 2026-10-06: ch.32's page showed ch.31's pictures while ch.31 rendered.
Media URLs now name their chapter; the QA catches a chapter whose pictures or
pages aren't its own. Run: python3 -m pytest test_chapter_media.py -q"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)


def _chapter(root, n, colour):
    from PIL import Image
    pid = f"a-regressors-tale-of-cultivation_{n}"
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "crops"))
    os.makedirs(os.path.join(d, "pages"))
    p = os.path.join(d, "crops", "page001_panel_001.png")       # same panel id in both chapters
    Image.new("RGB", (400, 600), colour).save(p)
    Image.new("RGB", (400, 600), colour).save(os.path.join(d, "pages", "001.png"))
    json.dump([{"seg_index": 0, "panel_id": "page001_panel_001", "panel_file": p, "dur": 3.0,
                "user_included": True, "beats": []}], open(os.path.join(d, "segments.json"), "w"))
    json.dump({"series": "A Regressors Tale Of Cultivation", "chapter": str(n),
               "url": f"https://asurascans.com/comics/a-regressors-tale-of-cultivation-1a2b3c4d/chapter/{n}"},
              open(os.path.join(d, "project.json"), "w"))
    return pid, d


def test_media_routes_serve_the_named_chapter_not_the_active_one(tmp_path):
    from PIL import Image
    import ingest as ing
    import server as srv
    ing.PROJECTS = str(tmp_path)
    p31, d31 = _chapter(str(tmp_path), 31, (200, 20, 20))      # red: the one "rendering"
    p32, d32 = _chapter(str(tmp_path), 32, (20, 20, 200))      # blue: the one being viewed
    srv.active_project_dir = lambda: d31
    for resp in (srv.panelimg("page001_panel_001", 0, p32), srv.thumb(0, p32), srv.segimg(0, 1, p32)):
        im = Image.open(resp.path).convert("RGB")
        assert im.getpixel((im.width // 2, im.height // 2))[2] > 150, resp.path   # blue = ch.32
    assert srv.thumb(0, p32).path.startswith(d32)
    # without a project the old behaviour (active chapter) is unchanged
    im = Image.open(srv.panelimg("page001_panel_001", 0, "").path).convert("RGB")
    assert im.getpixel((10, 10))[0] > 150


def test_qa_flags_a_chapter_that_is_another_chapters_copy(tmp_path):
    import chapter_qa as q
    import ingest as ing
    _chapter(str(tmp_path), 31, (200, 20, 20))
    p32, d32 = _chapter(str(tmp_path), 32, (20, 20, 200))
    ok = q.check(str(tmp_path), p32, ing.project_id(
        "https://asurascans.com/comics/a-regressors-tale-of-cultivation-1a2b3c4d/chapter/32"))
    assert ok["status"] == "ok", ok
    # ch.32 got ch.31's pages and pictures (wrong scrape / copied folder)
    for sub in ("crops", "pages"):
        shutil.rmtree(os.path.join(d32, sub))
        shutil.copytree(os.path.join(str(tmp_path), "a-regressors-tale-of-cultivation_31", sub), os.path.join(d32, sub))
    bad = q.check(str(tmp_path), p32, None, {})
    assert bad["status"] == "bad" and any("identical to ch.31" in i for i in bad["issues"]), bad
    # and a segment pointing into ch.31's folder is caught
    segs = json.load(open(os.path.join(d32, "segments.json")))
    segs[0]["panel_file"] = os.path.join(str(tmp_path), "a-regressors-tale-of-cultivation_31", "crops", "page001_panel_001.png")
    json.dump(segs, open(os.path.join(d32, "segments.json"), "w"))
    r = q.check(str(tmp_path), p32, None, {})
    assert any("another chapter's folder: a-regressors-tale-of-cultivation_31" in i for i in r["issues"]), r
