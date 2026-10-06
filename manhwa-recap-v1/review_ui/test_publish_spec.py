"""Spec 06 Part B (docs/audit/06_REVISION_perChapter_and_Thumbnails.md) — pytest.

Per the work order every test renders a real 1280x720 PNG and asserts on its
pixels. The publishing rules themselves are string/number rules; the PNG each
test renders is the chapter thumbnail built from that title's hook, checked on
its pixels, so title and thumbnail are verified to tell the same story.
Run: python3 -m pytest manhwa-recap-v1/review_ui/test_publish_spec.py -q
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)
import chapter_title as ct  # noqa: E402
import thumbnail_studio as ts  # noqa: E402

ART = os.environ.get("THUMB_ARTIFACTS") or tempfile.mkdtemp(prefix="pubspec_")
os.makedirs(ART, exist_ok=True)
YELLOW = (255, 212, 0)
SERIES = "I Am The Fated Villain"


def _thumb_from_title(tmp_path, title, out_name):
    """Render the chapter thumbnail whose hook comes from `title`; return
    (PIL image, concept)."""
    from PIL import Image
    src = tmp_path / "panel.png"
    if not src.exists():
        Image.new("RGB", (1600, 1000), (40, 50, 70)).save(src)
    c = {"composition": "panel-hero", "focal_file": str(src), "chapter": "362",
         "overlay_text": ts.hook_from_title(title), "palette": {"accent": [200, 30, 30]}}
    out = os.path.join(ART, out_name)
    ts.render_concept(str(tmp_path), c, {}, out)
    return Image.open(out).convert("RGB"), c


def _yellow_bbox(im, box=(0, 400, 1280, 720)):
    px = im.load()
    xs, ys = [], []
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2], 2):
            if px[x, y] == YELLOW:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def test_chapter_title_rejects_old_pipe_pattern(tmp_path):
    old = "P362 | I Am The Fated Villain | Me, The Heavenly Destined Villain #manhwa"
    probs = ct.problems(old, SERIES, "362")
    assert any("old" in p for p in probs), probs
    assert ct.problems(f"{SERIES} Chapter 362 Manhwa Recap", SERIES, "362"), "no hook must be rejected"
    new = ct.build("He Finally Kills The Heavenly Son", SERIES, "362")
    assert new == "He Finally Kills The Heavenly Son - I Am The Fated Villain Chapter 362 Manhwa Recap"
    assert ct.problems(new, SERIES, "362") == []
    assert any("already" in p for p in ct.problems(new, SERIES, "362", others=[new.upper()]))
    # the thumbnail made from the new title carries ITS hook, drawn big
    im, c = _thumb_from_title(tmp_path, new, "b1_title_hook_thumb.png")
    assert im.size == (1280, 720)
    bb = _yellow_bbox(im)
    assert bb and bb[3] - bb[1] >= ts.CAP_FLOOR - 4
    # the hook is the title's lead, not its series words (weak words like
    # "he"/"the" are dropped by hook_from_title, as on every thumbnail)
    drawn = " ".join(c["_hook_layout"]["lines"])
    assert drawn.startswith("FINALLY KILLS") and "VILLAIN" not in drawn


def test_chapter_title_under_100_chars(tmp_path):
    long_series = "The Return Of The Disaster-Class Hero Who Was Betrayed By Everyone He Trusted"
    hook = "He Walks Back Into The Capital And Every Single Noble Family Finally Kneels Before Him"
    t = ct.build(hook, long_series, "1125")
    assert len(t) <= 100, len(t)
    assert t.endswith(" Chapter 1125")                  # the chapter number is never cut
    assert len(t.split(" - ")[0]) >= ct.HOOK_MIN_ROOM - 2   # the hook keeps its room
    short = ct.build(hook, SERIES, "362")
    assert short.endswith(SERIES + " Chapter 362 Manhwa Recap") and len(short) <= 100
    assert all(len(ct.build(h, SERIES, "362")) <= 100 for h in (hook, hook * 3, "X"))
    im, c = _thumb_from_title(tmp_path, t, "b1_long_title_thumb.png")
    bb = _yellow_bbox(im)
    # the hook drawn from a long title is cut to words, never shrunk below 84 px
    first = [y for y in range(bb[1], bb[3] + 1)]
    assert bb and c["_hook_layout"]["cap"] >= ts.CAP_FLOOR
    assert bb[2] <= ts.ZONES["text"][2] + 2 and len(first) >= ts.CAP_FLOOR - 4


# ------------------------------------------------------------------ B2
def test_block2_similarity_flag_fires(tmp_path):
    import json
    import ingest as ing
    import server as srv
    import description_blocks as db
    ing.PROJECTS = str(tmp_path)
    url = "https://asurascans.com/comics/i-am-the-fated-villain-1a2b3c4d/chapter/%d"
    summ_361 = ("Jin Ye-seong ambushes the Heavenly Son at the Moon Gate and breaks his sword, "
                "then lets him flee so the sect will blame the elders for the attack.")
    for n in (361, 362):
        d = tmp_path / f"i-am-the-fated-villain_{n}"
        (d / "exports").mkdir(parents=True)
        (d / "project.json").write_text(json.dumps({"series": SERIES, "chapter": str(n), "url": url % n}))
    (tmp_path / "i-am-the-fated-villain_361" / "publish.json").write_text(json.dumps(
        {"final_361.mp4": {"title": "x", "summary_block": summ_361, "summary_at": 1}}))
    pd = str(tmp_path / "i-am-the-fated-villain_362")
    base = {"title": ct.build("He Breaks The Heavenly Son", SERIES, "362"), "privacy": "private",
            "category_id": "1", "tags": []}
    templ = summ_361.replace("Moon Gate", "Sun Gate")          # the same sentence, one word swapped
    fresh = ("The elders turn on each other over the Moon Gate attack while Jin Ye-seong quietly "
             "claims the vacant seat of the inner court and names his first disciple.")
    assert db.block2_overlap(templ, summ_361) > 0.8 >= db.block2_overlap(fresh, summ_361)
    flagged = srv.validate_publish({**base, "summary_block": templ}, pd, "final_362.mp4")
    clean = srv.validate_publish({**base, "summary_block": fresh}, pd, "final_362.mp4")
    assert any("block 2" in p for p in flagged), flagged
    assert not any("block 2" in p for p in clean), clean
    # the full description keeps the fixed block order (1 first, hashtags last)
    text = db.build(hook="He Breaks The Heavenly Son", series=SERIES, chapter=362, summary=fresh,
                    pack={"title_en": SERIES, "aliases": ["Me, The Heavenly Destined Villain"]},
                    footer="FOOTER", hashtags=["#manhwa", "#manhwarecap", "#fatedvillain"])
    blocks = text.split("\n\n")
    assert blocks[0] == "He Breaks The Heavenly Son | I Am The Fated Villain Chapter 362" and len(blocks[0]) <= 150
    assert blocks[1] == fresh and "Also known as: Me, The Heavenly Destined Villain" in blocks[2]
    assert blocks[-2] == "FOOTER" and blocks[-1].startswith("#manhwa")
    im, c = _thumb_from_title(tmp_path, base["title"], "b2_chapter_thumb.png")
    bb = _yellow_bbox(im)
    assert bb and bb[3] - bb[1] >= ts.CAP_FLOOR - 4


# ------------------------------------------------------------------ B4
def test_series_pack_aliases_reach_tags_description_and_narration(tmp_path):
    import series_pack as sp
    import description_blocks as db
    import narrate
    wf = {"english_title": SERIES, "korean_title": "운명의 악역",
          "search_names": ["Me, The Heavenly Destined Villain", "The Fated Villain", "i am the fated villain"],
          "genres": ["Action", "Fantasy"], "status": "Ongoing", "author": "A", "artist": "B"}
    pack = sp.build("i-am-the-fated-villain", series=SERIES, web_facts=wf,
                    bible={"aliases": ["Fated Villain"], "characters": [{"name": "Jin Ye-seong", "role": "protagonist"}]},
                    watch={"aliases": ["Heavenly Destined Villain"]}, slug_aliases=["I Am The Fated Villain"],
                    prev={"aliases_manual": ["IATFV"]})
    al = pack["aliases"]
    assert al[0] == "IATFV" and "운명의 악역" in al and "Heavenly Destined Villain" in al
    assert not any(a.lower() == SERIES.lower() for a in al)            # the title itself is not an alias
    assert len({a.lower() for a in al}) == len(al)                      # deduped, case-insensitive
    tags = sp.tags_with_aliases(["manhwa recap", "fated villain"], al)
    tag_form = lambda a: " ".join(a.replace(",", " ").split())
    assert all(tag_form(a) in tags for a in al if a.lower() != "fated villain")
    assert sum(len(t) for t in tags) + len(tags) - 1 <= 500
    assert "Also known as: " + ", ".join(al) in db.block5(pack)
    spoken = {sp.spoken_alias(pack, n) for n in range(1, 30)}
    assert len(spoken) > 3 and "운명의 악역" not in spoken                 # rotates; only speakable names
    prompt = narrate.build_prompt([], opening=narrate.opening_rule(SERIES, sp.spoken_alias(pack, 362)))
    assert f"{SERIES}, also known as {sp.spoken_alias(pack, 362)}" in prompt
    assert "also known as" not in narrate.build_prompt([])
    im, c = _thumb_from_title(tmp_path, ct.build("He Breaks The Heavenly Son", SERIES, "362"), "b4_thumb.png")
    assert _yellow_bbox(im)


# ------------------------------------------------------------------ B3
def test_range_is_stored_as_two_integers(tmp_path):
    """A real range build: three chapter videos (solid red/green/blue, 1 s
    each, approved) are stitched into a NEW range project. The range lives in
    project.json as two JSON integers and the title is built from them; a
    1280x720 frame grabbed inside each chapter's slot of the stitched video
    has that chapter's colour (order verified on pixels)."""
    import json
    import subprocess
    import time as _t
    import ingest as ing
    import server as srv
    import range_compile as rc
    from PIL import Image
    ing.PROJECTS = str(tmp_path)
    sid = "i-am-the-fated-villain"
    url = "https://asurascans.com/comics/i-am-the-fated-villain-1a2b3c4d/chapter/%d"
    colours = {1: "red", 2: "lime", 3: "blue"}
    for n, col in colours.items():
        d = tmp_path / f"{sid}_{n}"
        (d / "exports").mkdir(parents=True)
        (d / "project.json").write_text(json.dumps({"series": SERIES, "chapter": str(n), "url": url % n}))
        out = d / "exports" / f"final_{n}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={col}:s=1280x720:r=30:d=1",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
        (d / "reviews.json").write_text(json.dumps({f"final_{n}.mp4": {
            "status": "approved", "cut_signature": srv.cut_signature(pdir=str(d))}}))
    plan = srv.api_range_plan(srv.RangeIn(project=f"{sid}_1", chapters_start=1, chapters_end=3))
    assert plan["missing"] == [] and plan["chapters"] == [1, 2, 3]
    assert len(plan["title"]) <= 100 and "Chapter 1-3" in plan["title"]
    r = srv.api_range_build(srv.RangeIn(project=f"{sid}_1", chapters_start=1, chapters_end=3))
    rdir = tmp_path / r["project"]
    for _ in range(100):
        meta = json.loads((rdir / "project.json").read_text())
        if (meta.get("build") or {}).get("status"):
            break
        _t.sleep(0.2)
    assert meta["build"]["status"] == "done", meta.get("build")
    raw = (rdir / "project.json").read_text()
    assert '"chapters_start": 1' in raw and '"chapters_end": 3' in raw     # JSON integers, not strings
    assert type(meta["chapters_start"]) is int and type(meta["chapters_end"]) is int
    title = json.loads((rdir / "publish.json").read_text())[r["export"]]["title"]
    assert title == rc.title(SERIES, meta["chapters_start"], meta["chapters_end"], 3)[0]
    assert srv.validate_publish({"title": title, "privacy": "private", "category_id": "1", "tags": []},
                                str(rdir), r["export"]) == []
    # never a chapter in the Library's lists
    assert r["project"] not in [m.get("id") for m in ing.list_projects()]
    # pixels: one frame inside each chapter's slot, at 1280x720
    want = {1: (254, 0, 0), 2: (0, 255, 1), 3: (0, 0, 254)}
    for n, t in ((1, 0.5), (2, 1.5), (3, 2.5)):
        png = os.path.join(ART, f"b3_range_frame_ch{n}.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(rdir / "exports" / r["export"]),
                        "-frames:v", "1", png], check=True)
        im = Image.open(png).convert("RGB")
        assert im.size == (1280, 720)
        px = im.getpixel((640, 360))
        assert sum(abs(a - b) for a, b in zip(px, want[n])) < 40, (n, px)
    # overflow: genre/year are dropped (year first) to stay under 100
    long_t, dropped = rc.title("The Return Of The Disaster-Class Hero", 1, 120, 9 * 3600, "Action Fantasy", 2026)
    assert len(long_t) <= 100 and dropped and dropped[0].startswith("Best Manhwa")
