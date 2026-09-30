"""The cutter's refinement pass (splitlab.refine_crops), for every ingest.

Pins, with synthetic images and a STUBBED vision client (rule 12 — no real
service in a test):
  * a gutter crossed by a speech bubble becomes a CANDIDATE, and a confirmed
    cut keeps the bubble whole in one crop (ch.44 page008_panel_008);
  * continuous art makes no candidate and costs no vision call;
  * the model can only confirm numbered candidates, never invent a cut;
    garbage, an error, no key or the cap all mean NO extra cut;
  * consecutive thin text strips merge into one card padded with the page
    background (ch.44 monologue); an art strip or a lone strip never merges.

Run: python3 test_cut_refine.py
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..")]
import splitlab as SL

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def art(w, h, seed):
    rng = np.random.default_rng(seed)
    a = rng.integers(40, 200, (h, w, 3), dtype=np.uint8)   # busy, coloured art
    return Image.fromarray(a, "RGB")


def two_panels_bubble_across():
    """Art 900x700, a 160px white gutter, art 900x700; a white bubble with a
    black outline crosses the gutter's middle (not its edges)."""
    W, top, gap, bot = 900, 700, 160, 700
    im = Image.new("RGB", (W, top + gap + bot), (255, 255, 255))
    im.paste(art(W, top, 1), (0, 0))
    im.paste(art(W, bot, 2), (0, top + gap))
    d = ImageDraw.Draw(im)
    d.ellipse([300, top + 20, 600, top + 140], fill=(255, 255, 255), outline=(0, 0, 0), width=6)
    d.text((380, top + 70), "SHOW ME", fill=(0, 0, 0))
    return im, top, gap


def text_strip(txt, w=600, h=80):
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for dx in range(0, 3):     # bold-ish lettering
        d.text((20 + dx, 25), txt * 3, fill=(0, 0, 0))
    return im


def main():
    # ------------------------------------------------ candidates + cut row
    im, top, gap = two_panels_bubble_across()
    g = np.array(im.convert("L"))
    bands = SL.candidate_gutters(g)
    check("a bubble-crossed gutter is found as a candidate", len(bands) == 1
          and bands[0][0] >= top - 2 and bands[0][1] <= top + gap + 2)
    y = SL.cut_row(g, bands[0])
    check("the cut avoids the bubble (it lands in a blank margin beside it)",
          y < top + 20 or y > top + 140)
    check("a full-width rule would NOT have cut here (the old blind spot)",
          not any((g[r] > 238).all() for r in range(top + 20, top + 140)))
    cont = art(900, 1600, 3)
    check("continuous art makes no candidate",
          SL.candidate_gutters(np.array(cont.convert("L"))) == [])

    # ------------------------------------------------ confirm by number only
    calls = []
    yes = lambda png, prompt: calls.append(prompt) or '{"boundaries": [1]}'
    out, st = SL.refine_crops([im], ask=yes)
    check("a confirmed cut splits the crop into two panels", len(out) == 2 and st["cuts"] == 1)
    check("...and asked the model exactly once", len(calls) == 1 and "numbered lines" in calls[0])
    top_has = (np.array(out[0].convert("L"))[-40:, 300:600] < 30).any()
    bot_has = (np.array(out[1].convert("L"))[:40, 300:600] < 30).any()
    check("the bubble stays WHOLE in one crop, never sliced across two",
          not (top_has and bot_has))
    for reply, name in (('{"boundaries": [99]}', "an invented line number"),
                        ("I think it should be cut at y=500", "prose instead of JSON"),
                        ('{"boundaries": []}', "an explicit no")):
        o, s = SL.refine_crops([im], ask=lambda png, p, r=reply: r)
        check(f"{name} means no cut", len(o) == 1 and s["cuts"] == 0)
    def boom(png, p):
        raise RuntimeError("network down")
    o, s = SL.refine_crops([im], ask=boom)
    check("a vision failure means no cut, and is counted", len(o) == 1 and s["vision_errors"] == 1)
    o, s = SL.refine_crops([im], ask=None)
    check("no key means no cut (today's behaviour), and is counted",
          len(o) == 1 and s["no_vision"] == 1)
    calls.clear()
    o, s = SL.refine_crops([cont], ask=yes)
    check("continuous art costs no vision call", calls == [] and len(o) == 1)
    o, s = SL.refine_crops([im, im, im], ask=yes, budget=1)
    check("the per-chapter cap is honoured", s["tall_checked"] == 1 and s["capped"] == 2)

    # ------------------------------------------------ text-strip merging
    strips = [text_strip("HOW DID "), text_strip("YEON KNOW "), text_strip("HERE? ")]
    o, s = SL.refine_crops(strips, ask=None)
    check("consecutive text strips merge into ONE card", len(o) == 1 and s["text_slivers_merged"] == 3)
    card = np.array(o[0].convert("L"))
    check("the card keeps every line, top to bottom", o[0].height == sum(x.height for x in strips))
    check("the card is padded with the PAGE background (white), not black",
          card[:, :3].mean() > 200)
    colour = art(900, 90, 9)
    o, s = SL.refine_crops([text_strip("A "), colour, text_strip("B ")], ask=None)
    check("an art strip between text strips breaks the run (nothing merges)",
          len(o) == 3 and s["text_slivers_merged"] == 0)
    o, s = SL.refine_crops([text_strip("ALONE ")], ask=None)
    check("a lone text strip is left alone", len(o) == 1 and s["text_slivers_merged"] == 0)
    check("a thin strip of coloured art is not text", not SL._is_text_sliver(colour))

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print(f"\n{n}/{len(R)} passed")
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
