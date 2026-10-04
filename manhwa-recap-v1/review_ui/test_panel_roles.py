"""Image check for panels (panel_stats.py, 2026-10-04): bubble/text cards and
near-blank fragments stay off the timeline, their dialogue still reaches the
script, real scenes the wording rules dropped come back, and QA flags anything
that slips on screen. Cases are the real ones from Murim 44, Mount Hua 180,
Stellar 129 and Extra's 114. Synthetic images; no network."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def img(path, kind, w=600, h=300):
    from PIL import Image, ImageDraw
    import random
    if kind == "black":
        im = Image.new("RGB", (w, h), (2, 2, 2))
        d = ImageDraw.Draw(im)
        for _ in range(12):                      # a few faint embers
            x, y = random.randint(0, w - 3), random.randint(0, h - 3)
            d.rectangle([x, y, x + 2, y + 2], fill=(120, 30, 10))
    elif kind == "white":
        im = Image.new("RGB", (w, h), (255, 255, 255))
    elif kind == "bubble":
        im = Image.new("RGB", (w, h), (245, 245, 245))
        d = ImageDraw.Draw(im)
        for i in range(0, w, 40):                # burst lines + a few letters
            d.line([(w // 2, h), (i, 0)], fill=(0, 0, 0), width=2)
    else:                                        # art: busy, high contrast
        random.seed(1)
        im = Image.new("RGB", (w, h), (90, 70, 60))
        d = ImageDraw.Draw(im)
        for _ in range(400):
            x, y = random.randint(0, w), random.randint(0, h)
            d.ellipse([x, y, x + 30, y + 30], fill=(random.randint(0, 255),) * 3)
    im.save(path)


def main():
    import panel_stats as ps
    import matcher
    import narrate
    root = tempfile.mkdtemp(prefix="roles_")
    crops = os.path.join(root, "crops")
    os.makedirs(crops)
    panels = [
        ("page002_panel_002", "bubble", "I THOUGHT IT WAS JUST A PERFORMANCE,",
         "Emphasizing an off-screen reaction inside a large spiky burst bubble with radiating lines.", 776, 328),
        ("page002_panel_003", "art", "BUT THOSE THINGS WERE ENEMIES AS WELL...",
         "Grinning menacingly with wide eyes and clenched bared teeth, a wild-haired man.", 900, 844),
        ("page019_panel_020", "black", "", "Floating through pitch-black darkness, small glowing reddish-orange embers.", 779, 369),
        ("page019_panel_018", "bubble", "ELDER,",
         "Displaying stylized dramatic text against a pitch-black background, the word 'ELDER,' appears in jagged white brush strokes.", 742, 215),
        ("page004_panel_001", "art", "", "Descending a steep, dark rocky mountain slope at night below a distant illuminated "
         "cliffside building complex, with dense foliage and rocks extending down the panel.", 900, 3994),
        ("page004_panel_004", "white", "", "Flashing a completely blank white space, serving as an empty transition.", 900, 427),
        ("page021_panel_004", "black", "ADAPTATION/STORYBOARD KIM YEON",
         "Displaying production credits against a pitch-black background.", 348, 85),
        ("page001_panel_052", "bubble", "FLICK", "Flicking its blue-tipped tail, a cute white serpent with large eyes.", 600, 300),
    ]
    descs = []
    for pid, kind, ocr, desc, w, h in panels:
        img(os.path.join(crops, pid + ".png"), kind, min(w, 600), min(h, 600))
        descs.append({"panel_id": pid, "file": pid + ".png", "ocr_text": ocr,
                      "visual_description": desc, "width": w, "height": h, "ok": True})
    dp = os.path.join(root, "descriptions.json")
    json.dump(descs, open(dp, "w"))

    check("the mountain shot WAS dropped by the old wording rule (the bug)",
          matcher.is_junk_panel(dict(descs[4])))
    counts = ps.annotate(dp, crops)
    got = {d["panel_id"]: d["role"] for d in json.load(open(dp))}
    check("spiky burst bubble -> bubble", got["page002_panel_002"] == "bubble")
    check("'ELDER,' text card on black -> bubble (quoted word is not a person)", got["page019_panel_018"] == "bubble")
    check("embers on black -> fragment", got["page019_panel_020"] == "fragment")
    check("blank white spacer -> fragment", got["page004_panel_004"] == "fragment")
    check("production credits -> credits", got["page021_panel_004"] == "credits")
    check("grinning man with dialogue -> art", got["page002_panel_003"] == "art")
    check("mountain establishing shot -> art", got["page004_panel_001"] == "art")
    check("a serpent with a FLICK sound effect is somebody in frame, not a bubble",
          got["page001_panel_052"] != "bubble")
    check("annotate reports counts per role", sum(counts.values()) == len(panels))

    D = {d["panel_id"]: d for d in json.load(open(dp))}
    check("bubble, fragment and credits panels are off the timeline",
          all(matcher.is_junk_panel(D[p]) for p in ("page002_panel_002", "page019_panel_020",
                                                     "page004_panel_004", "page021_panel_004")))
    check("the mountain shot is back on (image evidence beats the wording rule)",
          not matcher.is_junk_panel(D["page004_panel_001"]))
    check("the board says WHY a bubble is left out",
          "dialogue" in (matcher.junk_reason(D["page002_panel_002"]) or ""))
    check("a panel from before the image check is judged exactly as before",
          matcher.is_junk_panel({k: v for k, v in D["page004_panel_001"].items() if k not in ("role", "pix")}))

    # dialogue reaches the script through the next art panel on the page
    live = narrate.load_panels(dp)
    by = {p["panel_id"]: p for p in live}
    check("narration input no longer contains the bubble panel", "page002_panel_002" not in by)
    check("...but its dialogue now leads the next art panel's text",
          by["page002_panel_003"]["ocr_text"].startswith("I THOUGHT IT WAS JUST A PERFORMANCE,")
          and "ENEMIES AS WELL" in by["page002_panel_003"]["ocr_text"])
    check("descriptions.json on disk is not rewritten by the hand-off",
          "PERFORMANCE" not in json.load(open(dp))[1]["ocr_text"])

    # QA rule: anything that still sits on screen is flagged before review
    import validator
    rows = [{"n": 1, "panel_id": "page002_panel_002", "role": "bubble", "ocr": "X", "desc": "d",
             "desc_ok": True, "placement": {"role": "carries", "unit": 0},
             "timing": {"in_video": True, "dur": 7.4, "silent": False, "seg_index": 5}}]
    f = [x for x in validator.rule_findings(rows) if x.get("category") == "image"]
    check("QA flags a bubble that is on screen in the video", len(f) == 1 and f[0]["severity"] == "high")
    rows[0]["timing"]["in_video"] = False
    check("...and stays quiet when it is left out",
          not [x for x in validator.rule_findings(rows) if x.get("category") == "image"])

    src = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    check("ingest runs the image check after describe", "panel_stats.annotate(desc_path, crops)" in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
