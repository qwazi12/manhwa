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


# Flat-band detection: DEFAULT OFF.
# It is verifiably correct on I Am The Fated Villain (4 gutters, each checked
# individually, including a 157-row band at mean 87.6 that a bg=0 test cannot
# see) and over-fires on Murim Psychopath by +19%, outside the +/-15%
# regression band. A recurrence rule was tested as a way to keep both —
# "gutters repeat, one-off art fills do not" — and REJECTED by measurement:
# it deleted 2 of the 4 verified FV gutters, because a real gutter can be
# unique on its page when it separates two panels of differing ground colour.
# So: off globally, on for the titles where it has been measured.
FLAT_STD_DEFAULT = float(os.environ.get("SPLIT_FLAT_STD", 0.0))
FLAT_STD_BY_TITLE = {
    # slug prefix -> flat-band threshold. Measured, not assumed.
    "i-am-the-fated-villain": 6.0,
}


def flat_std_for(slug):
    """Per-title flat-band setting. A title earns this by measurement."""
    env = os.environ.get("SPLIT_FLAT_STD")
    if env is not None:
        return float(env)                       # explicit override wins
    for prefix, v in FLAT_STD_BY_TITLE.items():
        if (slug or "").startswith(prefix):
            return v
    return FLAT_STD_DEFAULT


FLAT_STD = FLAT_STD_DEFAULT


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


# ================================================================ refinement
# A post-pass over the crops the gutter rule produced, for EVERY ingest
# (2026-09-30, ch.44 audit — manhwa-recap-v1/memory.md "checkpoint 2"):
#
#  1. MISSED SPLITS. The rule above cuts only on a band that is background
#     across the FULL width, so it can never cut a gutter a speech bubble
#     crosses — and webtoons do that routinely (ch.44 page008_panel_008: crowd
#     "MORE... MORE!" + protagonist "SHOW ME EVERYTHING…" came out as one
#     crop). Pixels find CANDIDATES (blank at BOTH edges, real art above and
#     below); one vision call per tall crop decides which candidates are real
#     boundaries, picking them BY NUMBER from lines drawn on the image, so it
#     can confirm but never invent a cut. Pixels alone were measured to fail
#     both ways: a strict test missed most, an art-block count chopped
#     continuous splashes (page011_panel_001, one action splash, read as 4).
#     No key, a failure, or the per-chapter cap = no extra cut (the old
#     behaviour), never a guessed one.
#  2. OVER-SPLIT MONOLOGUE. Stylised inner monologue set line by line in
#     white space was cut one line per crop ("HOW DID" / "YEON YOUNGHA KNOW"
#     / "I'D PASS THROUGH" / "HERE?"). Consecutive thin text-only crops are
#     merged back into one card, so one thought is one unit downstream.
REFINE_TALL_PX = int(os.environ.get("SPLIT_REFINE_TALL_PX", 1200))
GUTTER_EDGE_FRAC = 0.06      # both outer 6% of the width must be blank
GUTTER_MIN_ROWS = 12         # a real gutter is at least this tall
ART_ROW_FRAC = 0.55          # a row is ARTWORK when >=55% of it is not blank
ART_MIN_PX = 250             # a panel needs at least this much art height
TEXT_SLIVER_MAX_H = 200      # monologue lines are thin crops...
TEXT_INK_MAX = 0.22          # ...with little ink and no artwork rows
MAX_VISION_CHECKS = int(os.environ.get("SPLIT_MAX_VISION_CHECKS", 60))
VISION_MODEL = os.environ.get("SPLIT_VISION_MODEL",
                              os.environ.get("DESCRIBE_MODEL", "gemini-3.5-flash"))


def _blank_frac(a):
    """Per row: share of pixels that are flat white or flat black (the two
    gutter colours webtoons use)."""
    return np.maximum((a > 238).mean(axis=1), (a < 18).mean(axis=1))


def candidate_gutters(gray):
    """Bands where a gutter may hide: blank at BOTH edges for at least
    GUTTER_MIN_ROWS rows (a bubble may cross the middle), with at least
    ART_MIN_PX rows of real artwork above AND below. Returns [(a, b)]."""
    a = gray.astype(np.int16)
    h, w = a.shape
    e = max(4, int(w * GUTTER_EDGE_FRAC))
    edge_blank = (_blank_frac(a[:, :e]) >= 0.95) & (_blank_frac(a[:, -e:]) >= 0.95)
    art = (1.0 - _blank_frac(a)) >= ART_ROW_FRAC
    art_cum = np.concatenate([[0], np.cumsum(art)])
    bands, st = [], None
    for i, v in enumerate(list(edge_blank) + [False]):
        if v and st is None:
            st = i
        elif not v and st is not None:
            if i - st >= GUTTER_MIN_ROWS:
                bands.append((st, i))
            st = None
    out, last = [], 0
    for b0, b1 in bands:
        above = art_cum[b0] - art_cum[last]
        below = art_cum[h] - art_cum[b1]
        if above >= ART_MIN_PX and below >= ART_MIN_PX:
            out.append((b0, b1))
            last = b1
    return out


def cut_row(gray, band):
    """Where to cut inside a candidate band so a crossing bubble stays WHOLE.
    A fully blank row wins; otherwise cut in the larger blank margin beside
    the bubble, so the bubble goes intact with one panel."""
    b0, b1 = band
    seg = gray[b0:b1].astype(np.int16)
    ink = seg.shape[1] - np.round(_blank_frac(seg) * seg.shape[1])
    zero = np.where(ink == 0)[0]
    if zero.size:
        runs = np.split(zero, np.where(np.diff(zero) > 1)[0] + 1)
        best = max(runs, key=len)
        return b0 + int(best[len(best) // 2])
    inked = np.where(ink > 0)[0]
    top, bot = int(inked[0]), int(inked[-1])
    above, below = top, (b1 - b0 - 1) - bot
    if above >= below:
        return b0 + max(0, above // 2)
    return b0 + bot + 1 + below // 2


_CUT_PROMPT = """This is one crop from a webtoon/manhwa chapter. Red numbered lines mark places where it MIGHT be split into separate panels.

A line is a real boundary ONLY if the artwork above it and the artwork below it are two DIFFERENT panels — a different shot, moment or framing, separated by a gutter. A line that runs through ONE continuous image (the same scene continuing, a splash, a fade to black, a background that simply continues) is NOT a boundary. Speech bubbles may cross a real boundary; that alone does not make it one.

Answer with JSON only: {"boundaries": [<the line numbers that ARE boundaries>]}"""


def _marked_png(img, ys):
    """The crop, downscaled for the model, with numbered red lines at ys."""
    from PIL import ImageDraw
    w, h = img.size
    s = min(1.0, 3000.0 / h)
    im = img.convert("RGB").resize((max(1, int(w * s)), max(1, int(h * s))))
    d = ImageDraw.Draw(im)
    for n, y in enumerate(ys, 1):
        yy = int(y * s)
        d.line([(0, yy), (im.width, yy)], fill=(255, 0, 0), width=4)
        d.rectangle([4, yy - 16, 44, yy + 16], fill=(255, 255, 255), outline=(255, 0, 0))
        d.text((14, yy - 8), str(n), fill=(255, 0, 0))
    import io
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def confirm_cuts(img, ys, ask):
    """ask(png_bytes, prompt) -> text. Returns the subset of ys the model
    confirms, chosen BY NUMBER. Anything unparseable confirms nothing."""
    if not ys:
        return []
    raw = ask(_marked_png(img, ys), _CUT_PROMPT) or ""
    import re as _re
    m = _re.search(r"\{.*\}", raw, _re.S)
    try:
        nums = json.loads(m.group(0)).get("boundaries", []) if m else []
    except (ValueError, AttributeError):
        return []
    keep = set()
    for n in nums if isinstance(nums, list) else []:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(ys):
            keep.add(n)
    return [ys[n - 1] for n in sorted(keep)]


def _default_ask():
    """The production vision client (describe.ask_image, usage-gated), or
    None when no key is configured — in which case no extra cut is made."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or os.environ.get("SPLIT_VISION_CUTS", "1").lower() in ("0", "false", "off"):
        return None
    pd = os.path.join(ROOT, "panel-describe")
    if pd not in sys.path:
        sys.path.insert(0, pd)
    import describe as _d
    return lambda png, prompt: _d.ask_image(png, "image/png", prompt, key, VISION_MODEL)


TEXT_MIDTONE_MAX = 0.18     # text is two-tone: few pixels between dark and light
TEXT_CHROMA_MAX = 30        # ...and colourless (max-min channel spread)


def _is_text_sliver(img):
    """A thin crop of lettering, not art. Measured on ch.44's monologue
    ("HOW DID", 82 px): bold webp lettering trimmed tight has anti-aliased
    strokes over most of each row, so an "is the row mostly blank?" test
    wrongly called it artwork. The reliable signal is TONE: lettering is
    two-tone and colourless, artwork has colour and mid-tones."""
    if img.height > TEXT_SLIVER_MAX_H:
        return False
    rgb = np.array(img.convert("RGB"), dtype=np.int16)
    g = rgb.mean(axis=2)
    midtone = ((g > 60) & (g < 200)).mean()
    chroma = np.percentile(rgb.max(axis=2) - rgb.min(axis=2), 95)
    return midtone <= TEXT_MIDTONE_MAX and chroma <= TEXT_CHROMA_MAX


def _vstack(imgs):
    """Stack text strips into one card, padded with the PAGE background.
    The background is read from the strips' corners, not their average: bold
    black lettering drags the average dark (ch.44's monologue card came out
    padded black on a white page)."""
    w = max(i.width for i in imgs)
    corners = []
    for i in imgs:
        a = np.array(i.convert("L"))
        corners += [a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]]
    fill = (0, 0, 0) if float(np.median(corners)) < 128 else (255, 255, 255)
    out = Image.new("RGB", (w, sum(i.height for i in imgs)), fill)
    y = 0
    for i in imgs:
        out.paste(i.convert("RGB"), ((w - i.width) // 2, y))
        y += i.height
    return out


def refine_crops(crops, ask="default", on_progress=None, budget=None):
    """The post-pass. crops: PIL images in reading order. Returns
    (refined_crops, stats). ask="default" uses the production client (or
    none without a key); tests pass their own."""
    if ask == "default":
        ask = _default_ask()
    budget = MAX_VISION_CHECKS if budget is None else budget
    st = {"text_slivers_merged": 0, "tall_checked": 0, "candidates": 0,
          "cuts": 0, "no_vision": 0, "vision_errors": 0, "capped": 0}
    # 1. merge runs of consecutive thin text-only crops
    merged, run = [], []
    for c in crops + [None]:
        if c is not None and _is_text_sliver(c):
            run.append(c)
            continue
        if len(run) >= 2:
            merged.append(_vstack(run))
            st["text_slivers_merged"] += len(run)
        else:
            merged.extend(run)
        run = []
        if c is not None:
            merged.append(c)
    # 2. split tall crops at confirmed gutters
    out = []
    for c in merged:
        if c.height < REFINE_TALL_PX:
            out.append(c)
            continue
        g = np.array(c.convert("L"))
        bands = candidate_gutters(g)
        if not bands:
            out.append(c)
            continue
        st["candidates"] += len(bands)
        ys = [cut_row(g, b) for b in bands]
        if ask is None:
            st["no_vision"] += 1
            out.append(c)
            continue
        if st["tall_checked"] >= budget:
            st["capped"] += 1
            out.append(c)
            continue
        st["tall_checked"] += 1
        try:
            keep = confirm_cuts(c, ys, ask)
        except Exception as e:
            try:
                import usage as _u
                if isinstance(e, _u.UsageCapExceeded):
                    raise
            except ImportError:
                pass
            st["vision_errors"] += 1
            keep = []
        cuts = [0] + keep + [c.height]
        parts = [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= MIN_PANEL_PX]
        st["cuts"] += max(0, len(parts) - 1)
        for a, b in parts:
            out.append(c.crop((0, a, c.width, b)))
        if on_progress and keep:
            on_progress(f"split a {c.height}px crop into {len(parts)} panels")
    return out, st


def split_into(pages, crops_dir, slug="", on_progress=None):
    """Split `pages` into `crops_dir` using PRODUCTION's filename convention.

    This is the same algorithm the Split Lab preview uses, writing
    `pageNNN_panel_NN.png` so `describe`, `match` and the board keep working
    unchanged — the pipeline downstream derives a panel_id from the filename,
    so the naming is a contract, not a cosmetic.

    Returns (n_crops, stats) where stats mirrors what the legacy splitter's
    panels.json carried, so coverage reporting does not go dark.
    """
    global FLAT_STD
    FLAT_STD = flat_std_for(slug)
    strip = is_strip(pages)
    made, per_page = 0, []
    ask = _default_ask()            # None without a key -> no extra cuts
    refine_total = {}

    def _refine(crops):
        """Run the post-pass; accumulate its stats for the whole chapter."""
        budget = MAX_VISION_CHECKS - refine_total.get("tall_checked", 0)
        out, stt = refine_crops(crops, ask=ask, on_progress=on_progress,
                                budget=max(0, budget))
        for k, v in stt.items():
            refine_total[k] = refine_total.get(k, 0) + v
        return out

    if strip:
        # A scroll is one continuous image chopped into CDN tiles; register
        # once and stitch panels back across tile seams.
        if on_progress:
            on_progress(f"assembling {len(pages)} tiles into one scroll")
        means, edges, offs, hs, W = [], [], [], [], None
        y = 0
        for p in pages:
            g = np.array(Image.open(p).convert("L"))
            if W is None:
                W = g.shape[1]
            r, e = _profile(g)
            means.append(r); edges.append(e); offs.append(y); hs.append(g.shape[0])
            y += g.shape[0]
        rows = np.concatenate(means); edge = np.concatenate(edges)
        bg, tol, ethr = register(rows, edge)
        rn = breaks(rows, edge, bg, tol, ethr)
        sp = _spans(rn, rows.size)
        canvases = []
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
                canvas.paste(x, (0, yy)); yy += x.height
            canvases.append(canvas)
        for i, canvas in enumerate(_refine(canvases), 1):
            canvas.save(os.path.join(crops_dir, "page001_panel_%03d.png" % i))
            made += 1
        per_page.append({"page": "(scroll)", "bg": round(bg, 1),
                         "gaps": len(rn), "panels": made})
    else:
        for pi, p in enumerate(pages, 1):
            im = Image.open(p).convert("RGB")
            g = np.array(im.convert("L"))
            rows, edge = _profile(g)
            bg, tol, ethr = register(rows, edge)
            rn = breaks(rows, edge, bg, tol, ethr,
                        gray=g if FLAT_STD > 0 else None)
            sp = _spans(rn, g.shape[0])
            n_here = 0
            page_crops = []
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
                if (x1 - x0) < MIN_PANEL_W:
                    x0, x1 = 0, g.shape[1]
                    if (y1 - y0) < MIN_PANEL_PX:
                        continue
                page_crops.append(im.crop((x0, y0, x1, y1)))
            for c in _refine(page_crops):
                n_here += 1
                c.save(os.path.join(crops_dir,
                                    "page%03d_panel_%03d.png" % (pi, n_here)))
                made += 1
            per_page.append({"page": os.path.basename(p), "bg": round(bg, 1),
                             "gaps": len(rn), "panels": n_here})
            if on_progress:
                on_progress(f"{os.path.basename(p)}: {n_here} panels")
    return made, {"format": "strip" if strip else "page",
                  "pages": len(pages), "panels": made, "per_page": per_page,
                  "refine": refine_total}


def run(slug, pages, on_progress=None, blur=False):
    """Split `pages` and write preview crops. Returns the metadata dict."""
    global FLAT_STD
    FLAT_STD = flat_std_for(slug)
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
            rn = breaks(rows, edge, bg, tol, ethr,
                        gray=g if FLAT_STD > 0 else None)
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
