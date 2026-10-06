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
