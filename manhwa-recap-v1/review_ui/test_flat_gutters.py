"""Splitter: a gutter must be flat across the width (owner, 2026-10-04 —
A Regressor's Tale ch.30 rows 82-96: a full-bleed page was cut into 14 strips
and speech bubbles into lines). Synthetic pages only (scraped art is never
committed); the real-page measurements are in memory.md."""
import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def split(img):
    import splitlab
    d = tempfile.mkdtemp(prefix="fg_")
    p = os.path.join(d, "001.png")
    img.save(p)
    out = os.path.join(d, "crops")
    os.makedirs(out)
    n, st = splitlab.split_into([p], out)
    return n, st["per_page"][0]["gaps"]


def textured(w, h, seed, base=(40, 90, 160)):
    rng = np.random.default_rng(seed)
    a = np.zeros((h, w, 3), np.uint8)
    x = np.linspace(0, 1, w)[None, :, None]
    a[:] = np.clip(np.array(base) + 60 * np.sin(x * 9 + seed) + rng.normal(0, 25, (h, w, 3)), 0, 255)
    return Image.fromarray(a)


def main():
    os.environ.pop("GEMINI_API_KEY", None)   # no vision pass in tests
    import splitlab
    # 1. two art panels separated by real white gutters -> cut there
    page = Image.new("RGB", (900, 2200), (255, 255, 255))
    page.paste(textured(900, 900, 1), (0, 100))
    page.paste(textured(900, 900, 2), (0, 1150))
    n, gaps = split(page)
    check("real white gutters still cut (2 panels)", n == 2)

    # 2. one full-bleed picture whose rows often match the page colour -> NOT cut into strips
    art = textured(900, 3000, 3, base=(118, 118, 118))
    d = ImageDraw.Draw(art)
    for y in range(200, 2900, 180):             # bands of near-uniform mid-tone with texture across
        d.rectangle([0, y, 899, y + 30], fill=(118, 118, 118))
        for x in range(0, 900, 37):
            d.line([x, y, x + 20, y + 30], fill=(20, 20, 20), width=3)
    n, gaps = split(art)
    check("a full-bleed picture is not sliced into strips", n == 1)

    # 3. a speech bubble on white: rows inside the bubble are not gutters
    page = Image.new("RGB", (900, 1400), (255, 255, 255))
    d = ImageDraw.Draw(page)
    d.ellipse([150, 200, 750, 700], outline=(0, 0, 0), width=6)
    for i, y in enumerate(range(330, 600, 60)):
        d.rectangle([300, y, 600, y + 22], fill=(0, 0, 0))
    page.paste(textured(900, 500, 4), (0, 850))
    n, gaps = split(page)
    check("a speech bubble stays whole (no cuts through its lines)", n == 2)

    # 4. switch
    splitlab.GUTTER_ROW_STD = 0
    n_off, _ = split(art)
    splitlab.GUTTER_ROW_STD = 6.0
    check("the rule is what prevents the strips (off -> strips come back)", n_off > 1)

    import panel_stats
    pix = {"ink": 0.5, "std": 60.0, "luma": 100.0}
    check("a thin full-width strip of art is a fragment (never its own line)",
          panel_stats.classify({"panel_id": "page013_panel_008", "width": 900, "height": 78,
                                "visual_description": "white impact lines slash across"}, pix) == "fragment")
    check("...a normal wide panel is still art",
          panel_stats.classify({"panel_id": "page013_panel_009", "width": 900, "height": 562,
                                "visual_description": "a man stands in the rain"}, pix) == "art")


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
