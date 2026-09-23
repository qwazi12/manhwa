"""Split Lab — run the background-registration splitter and LOOK at the result.

The splitter was being evaluated by reading numbers off a terminal and opening
crop folders on the server, which the operator cannot reach. Panel boundaries
are a visual judgement — "did this cut land on a gutter or through a face?" is
not answerable from a count — so the results need to be in the UI.

Two source shapes, one algorithm:

  PAGE format (Asura)   each image is a real page; register and split per page.
  STRIP format (WEBTOON) the CDN serves one continuous scroll chopped into
                         arbitrary ~1280px tiles that can land mid-panel. A
                         tile is not a page: registering per tile gave 376
                         sliver-fragments with backgrounds varying 16.9-254,
                         against 93 sane panels when the scroll is registered
                         ONCE. So tiles are concatenated first and panels are
                         stitched back across tile boundaries.

Nothing here touches the shipped pipeline. This is a preview surface.
"""

import json
import os
import shutil
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(RECAP, ".."))
if os.path.join(ROOT, "panel-split") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "panel-split"))

MIN_PANEL_PX = 60        # minimum panel HEIGHT
MIN_PANEL_W = 40         # ...and WIDTH: the trim could return a 1px column
BREAK_MIN_ROWS = 4
STRIP_MIN_TILES = 8      # fewer images than this is a page set, not a scroll
STRIP_UNIFORM_FRAC = 0.7  # share of images that must have the SAME height


def _root():
    import ingest
    return os.path.join(ingest.PROJECTS, "_splitlab")


def runs_dir(slug):
    return os.path.join(_root(), slug)


def list_runs():
    out = []
    try:
        names = sorted(os.listdir(_root()))
    except OSError:
        return out
    for n in names:
        meta = os.path.join(_root(), n, "meta.json")
        try:
            with open(meta, encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            continue
    out.sort(key=lambda m: m.get("ts", 0), reverse=True)
    return out


def _profile(gray):
    g = gray.astype(np.float32)
    h = g.shape[0]
    rows = g.mean(axis=1)
    dx = np.abs(np.diff(g, axis=1)).mean(axis=1)
    dy = np.zeros(h, np.float32)
    if h > 1:
        d = np.abs(np.diff(g, axis=0)).mean(axis=1)
        dy[:-1] = d
        dy[-1] = d[-1] if d.size else 0.0
    return rows, dx + dy


def register(rows, edge):
    """The modal row-mean is the gutter colour; tolerance is that cluster's
    own spread. Deriving both from the page means a dark page, a light page
    and a bleed page all take the same code path."""
    hist, bins = np.histogram(rows, bins=256, range=(0, 255))
    bg = float(bins[int(hist.argmax())])
    near = np.abs(rows - bg) <= 6.0
    if near.sum() < 3:
        near = np.abs(rows - bg) <= 12.0
    tol = float(rows[near].std()) * 2 if near.sum() >= 3 else 0.0
    tol = max(tol, 14.0)
    base = float(np.median(edge[near])) if near.sum() >= 3 else float(np.median(edge))
    return bg, tol, max(base * 3.0, 1.0)


FLAT_STD = float(os.environ.get("SPLIT_FLAT_STD", 6.0))


def flat_rows(gray):
    """Rows that are uniform ACROSS their width, whatever colour they are.

    Closes a real hole: registration picks ONE background per page, so on a
    black-background page a WHITE gutter band fails the brightness test and the
    break is missed entirely. A gutter is not "the page's colour" — it is a
    FLAT row.

    Calibrated, not guessed: rows the page-level test already accepts as gutter
    have a row-wise std of median 0.00, p90 0.05, max 5.33 across Fated Villain
    ch.358. FLAT_STD = 6 sits just above that observed ceiling.

    A band must also be flat VERTICALLY (uniform down its whole height) before
    it counts, or a smooth sky or colour fill inside a panel would read as a
    gutter and cut straight through the art.
    """
    g = gray.astype(np.float32)
    return g.std(axis=1) <= FLAT_STD


def breaks(rows, edge, bg, tol, ethr, gray=None):
    mask = (np.abs(rows - bg) <= tol) & (edge <= ethr)
    if gray is not None:
        flat = flat_rows(gray) & (edge <= ethr)
        # Accept a flat band only where the band is uniform down its height
        # too: one colour throughout is a gutter, a vertical gradient is art.
        extra = np.zeros_like(mask)
        st = None
        for i, v in enumerate(list(flat) + [False]):
            if v and st is None:
                st = i
            elif not v and st is not None:
                if i - st >= BREAK_MIN_ROWS:
                    band = gray[st:i].astype(np.float32)
                    if band.std() <= FLAT_STD:
                        extra[st:i] = True
                st = None
        mask = mask | extra
    runs, st = [], None
    for i, v in enumerate(mask):
        if v and st is None:
            st = i
        elif not v and st is not None:
            if i - st >= BREAK_MIN_ROWS:
                runs.append((st, i))
            st = None
    if st is not None and len(mask) - st >= BREAK_MIN_ROWS:
        runs.append((st, len(mask)))
    return runs


def _spans(runs, h):
    cuts = [0] + [(a + b) // 2 for a, b in runs] + [h]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if (b - a) >= MIN_PANEL_PX]


def is_strip(pages):
    """Is this a scroll chopped into tiles, or a set of real pages?

    NOT aspect ratio — that reads exactly backwards on the two live sources.
    Asura serves very TALL page images (900x16000, AR ~17) while WEBTOON
    serves modest tiles (800x1280, AR 1.6), so an "is it tall?" test calls
    Asura a scroll and WEBTOON a set of pages. Measured: that mislabelled
    Stellar Swordmaster ep129 as page format and produced 413 sliver panels
    at median AR 0.26.

    The real signature is UNIFORMITY. A CDN slicing one scroll emits tiles of
    identical height; genuine pages vary (ch.358 ran 504, 1024, 11128 ...
    16000). So: many images, most sharing one exact height.
    """
    if len(pages) < STRIP_MIN_TILES:
        return False
    heights = []
    for p in pages:
        try:
            heights.append(Image.open(p).size[1])
        except Exception:
            continue
    if len(heights) < STRIP_MIN_TILES:
        return False
    common = max(set(heights), key=heights.count)
    return heights.count(common) / len(heights) >= STRIP_UNIFORM_FRAC


def _save_panel(img, path, blur):
    """Write a panel, plus a bubble-blurred twin when asked.

    The blur is DISPLAY-SIDE: the unblurred crop is always written, so OCR,
    re-reads and the render are untouched. Blur, never inpaint — a slightly
    over-large blur is ugly but honest, while a generated fill puts art in the
    export that was never drawn.
    """
    img.save(path)
    if not blur:
        return None
    try:
        import bubbles as _b
        out, cov = _b.blur_bubbles(img)
        if cov > 0:
            out.save(path[:-4] + "_blur.png")
        return cov
    except Exception:
        return None


def run(slug, pages, on_progress=None, blur=False):
    """Split `pages` and write preview crops. Returns the metadata dict."""
    d = runs_dir(slug)
    os.makedirs(d, exist_ok=True)
    # Clear the PREVIOUS crops only. rmtree on the run directory also deleted
    # `_pages/` — the freshly scraped source images this run is about to read —
    # so every URL-sourced run died with "No such file or directory" on its
    # first page. Project-sourced runs survived because their pages live
    # outside this directory, which is what made the bug look source-specific.
    for f in os.listdir(d):
        fp = os.path.join(d, f)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
            except OSError:
                pass

    def prog(m):
        if on_progress:
            on_progress(m)

    strip = is_strip(pages)
    panels, stats = [], []

    if strip:
        prog(f"assembling {len(pages)} tiles into one scroll")
        means, edges, offs, hs, W = [], [], [], [], None
        y = 0
        for p in pages:
            g = np.array(Image.open(p).convert("L"))
            if W is None:
                W = g.shape[1]
            r, e = _profile(g)
            means.append(r)
            edges.append(e)
            offs.append(y)
            hs.append(g.shape[0])
            y += g.shape[0]
        rows = np.concatenate(means)
        edge = np.concatenate(edges)
        bg, tol, ethr = register(rows, edge)
        rn = breaks(rows, edge, bg, tol, ethr)
        sp = _spans(rn, rows.size)
        stats.append({"page": "(continuous scroll)", "h": int(rows.size),
                      "bg": round(bg, 1), "tol": round(tol, 1),
                      "gaps": len(rn), "panels": len(sp)})
        prog(f"{len(rn)} gaps -> {len(sp)} panels")
        for i, (a, b) in enumerate(sp, 1):
            parts = []
            for p, o, hh in zip(pages, offs, hs):
                if o + hh <= a or o >= b:
                    continue
                im = Image.open(p).convert("RGB")
                parts.append(im.crop((0, max(0, a - o), W, min(hh, b - o))))
            if not parts:
                continue
            tot = sum(x.height for x in parts)
            canvas = Image.new("RGB", (W, tot))
            yy = 0
            for x in parts:
                canvas.paste(x, (0, yy))
                yy += x.height
            name = "p%03d.png" % i
            cov = _save_panel(canvas, os.path.join(d, name), blur)
            panels.append({"name": name, "w": W, "h": tot,
                           "ar": round(tot / max(W, 1), 2), "page": "scroll",
                           "bubble_frac": round(cov, 4) if cov else 0.0,
                           "blurred": bool(cov)})
    else:
        for p in pages:
            base = os.path.splitext(os.path.basename(p))[0]
            im = Image.open(p).convert("RGB")
            g = np.array(im.convert("L"))
            rows, edge = _profile(g)
            bg, tol, ethr = register(rows, edge)
            rn = breaks(rows, edge, bg, tol, ethr, gray=g)
            sp = _spans(rn, g.shape[0])
            stats.append({"page": os.path.basename(p), "h": int(g.shape[0]),
                          "bg": round(bg, 1), "tol": round(tol, 1),
                          "gaps": len(rn), "panels": len(sp)})
            prog(f"{base}: {len(rn)} gaps -> {len(sp)} panels")
            for i, (a, b) in enumerate(sp, 1):
                band = g[a:b]
                nb = np.abs(band.astype(np.int16) - bg) > tol
                rs = np.where(nb.sum(axis=1) > 0)[0]
                cs = np.where(nb.sum(axis=0) > 0)[0]
                if rs.size and cs.size:
                    y0, y1 = a + int(rs[0]), a + int(rs[-1]) + 1
                    x0, x1 = int(cs[0]), int(cs[-1]) + 1
                else:
                    y0, y1, x0, x1 = a, b, 0, g.shape[1]
                # The trim measures the content box, and a band whose only
                # content is a thin vertical line trims to a 1px column — one
                # shipped as 1x86, aspect ratio 86. A panel needs width as
                # well as height, and a too-narrow trim means the trim was
                # wrong, not that the panel is a sliver: keep the full width.
                if (x1 - x0) < MIN_PANEL_W:
                    x0, x1 = 0, g.shape[1]
                    if (y1 - y0) < MIN_PANEL_PX:
                        continue
                name = "%s_p%02d.png" % (base, i)
                cov = _save_panel(im.crop((x0, y0, x1, y1)),
                                  os.path.join(d, name), blur)
                panels.append({"name": name, "w": x1 - x0, "h": y1 - y0,
                               "ar": round((y1 - y0) / max(x1 - x0, 1), 2),
                               "page": os.path.basename(p),
                               "bubble_frac": round(cov, 4) if cov else 0.0,
                               "blurred": bool(cov)})

    ars = [p["ar"] for p in panels] or [0]
    blurred = sum(1 for p in panels if p.get("blurred"))
    bcov = [p.get("bubble_frac", 0.0) for p in panels]
    meta = {"slug": slug, "ts": time.time(), "format": "strip" if strip else "page",
            "blur": bool(blur), "blurred_panels": blurred,
            "bubble_cov_mean": round(float(np.mean(bcov)) if bcov else 0.0, 4),
            "pages": len(pages), "panels": len(panels), "stats": stats,
            "median_ar": float(np.median(ars)),
            "over_3": int(sum(1 for a in ars if a > 3)),
            "panel_list": panels}
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta
