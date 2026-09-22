"""Split Lab must not delete the pages it is about to read.

Every URL-sourced run failed with
  No such file or directory: .../_splitlab/<slug>/_pages/001.webp
because `_splitlab_pages` scrapes into `<run_dir>/_pages/` and `run()` then
opened with `shutil.rmtree(run_dir)` — wiping the source images it had just
downloaded. Project-sourced runs survived, because their pages live outside
that directory, which made the bug look source-specific when it was not.
"""
import os
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def page(path, h=900, w=400, bands=3):
    """A synthetic page: art blocks separated by full-width background."""
    a = np.full((h, w), 250, np.uint8)
    step = h // bands
    for i in range(bands):
        y0 = i * step + 30
        y1 = min(y0 + step - 60, h)
        a[y0:y1, 20:w - 20] = 40
    Image.fromarray(a).save(path)
    return path


def main():
    import splitlab as SL

    root = tempfile.mkdtemp(prefix="sl_")
    SL._root = lambda: root

    slug = "demo"
    pages_dir = os.path.join(SL.runs_dir(slug), "_pages")
    os.makedirs(pages_dir, exist_ok=True)
    pages = [page(os.path.join(pages_dir, "%03d.png" % i)) for i in range(1, 4)]

    meta = SL.run(slug, pages)

    check("the run completes instead of dying on a missing page",
          meta and meta.get("panels", 0) > 0)
    check("the source pages SURVIVE the run",
          all(os.path.exists(p) for p in pages))
    check("...all three of them", len(os.listdir(pages_dir)) == 3)
    check("panels were written", meta["panels"] >= 3)
    check("each panel is a real file",
          all(os.path.exists(os.path.join(SL.runs_dir(slug), p["name"]))
              for p in meta["panel_list"]))
    check("page format is detected for page-shaped input",
          meta["format"] == "page")
    check("per-page stats are recorded", len(meta["stats"]) == 3)

    # a second run must replace the crops, not accumulate them
    n1 = meta["panels"]
    meta2 = SL.run(slug, pages)
    pngs = [f for f in os.listdir(SL.runs_dir(slug)) if f.endswith(".png")]
    check("re-running replaces the old crops rather than piling up",
          len(pngs) == meta2["panels"] == n1)
    check("...and the source pages are STILL there",
          len(os.listdir(pages_dir)) == 3)

    # strip detection keys off shape, not site name
    tall = tempfile.mkdtemp(prefix="sltall_")
    tp = [page(os.path.join(tall, "%03d.png" % i), h=2400, w=400)
          for i in range(1, 6)]
    check("tall narrow tiles are recognised as a scroll", SL.is_strip(tp))
    check("page-shaped images are not", not SL.is_strip(pages))

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
