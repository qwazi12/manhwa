"""Speech-bubble detection that does not care whether the page is light or dark.

WHY NOT THE EXISTING DETECTOR. `split_panels._detect_bubbles` takes connected
components of `gray >= 225` and keeps mid-sized ones. On a BLACK page a white
bubble is an isolated island and that works well. On a WHITE page the bubble
interior is the same value as the page itself, so labelling merges them into
one enormous component which then fails the max-area test and is discarded —
zero bubbles found. A blur built on it would look excellent on I Am The Fated
Villain and do nothing on lighter titles, which is worse than not shipping,
because it looks like it works.

THE RULE HERE. A speech bubble is a region of near-uniform fill enclosed by its
own stroke. So:

    fill regions that touch the page border      -> page background
    fill regions that do NOT touch the border    -> enclosed islands

An enclosed island holding dark marks (the lettering) is a bubble. Nothing in
that test mentions light or dark, so one code path serves both polarities.

Limits, stated rather than discovered later:
  * a bubble whose stroke is broken, or which bleeds off the panel edge,
    connects to the background and is missed.
  * a solid enclosed shape with no lettering inside is correctly ignored, but
    so is a bubble whose text failed to register as "dark marks".
"""

import os

import numpy as np

FILL_UNIFORM = float(os.environ.get("BUBBLE_FILL_UNIFORM", 26))
MIN_AREA_FRAC = float(os.environ.get("BUBBLE_MIN_AREA_FRAC", 0.0012))
MAX_AREA_FRAC = float(os.environ.get("BUBBLE_MAX_AREA_FRAC", 0.42))
MIN_INK_FRAC = float(os.environ.get("BUBBLE_MIN_INK", 0.012))
MAX_INK_FRAC = float(os.environ.get("BUBBLE_MAX_INK", 0.55))
INK_DELTA = float(os.environ.get("BUBBLE_INK_DELTA", 55))
BOX_FILL_MIN = float(os.environ.get("BUBBLE_BOX_FILL", 0.42))


def _label(mask):
    try:
        from scipy import ndimage
    except ImportError:
        return None, 0
    return ndimage.label(mask)


def find_bubbles(gray):
    """[(x0, y0, x1, y1, mask), ...] for each detected bubble.

    `mask` is the boolean pixel mask of that bubble within the full image, so a
    caller can blur the bubble's actual shape rather than its bounding box.
    """
    g = gray.astype(np.float32)
    h, w = g.shape
    area = float(h * w)

    # The dominant fill value: on a page it is the paper, inside a panel it is
    # whatever the artist filled with. Bubbles share a value close to it, which
    # is exactly why brightness alone cannot separate them.
    hist, bins = np.histogram(g, bins=64, range=(0, 255))
    fill = float(bins[int(hist.argmax())])
    flat = np.abs(g - fill) <= FILL_UNIFORM

    lbl, n = _label(flat)
    if lbl is None or not n:
        return []

    # Anything touching the border is the page/panel ground, not a bubble.
    edge_ids = set(np.unique(np.concatenate(
        [lbl[0, :], lbl[-1, :], lbl[:, 0], lbl[:, -1]])))
    edge_ids.discard(0)

    out = []
    for i in range(1, n + 1):
        if i in edge_ids:
            continue
        comp = lbl == i
        a = int(comp.sum())
        if a < MIN_AREA_FRAC * area or a > MAX_AREA_FRAC * area:
            continue
        ys, xs = np.where(comp)
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        box = comp[y0:y1, x0:x1]
        if box.mean() < BOX_FILL_MIN:
            continue
        # LETTERING IS THE HOLES, NOT THE FILL. The component is the near-
        # uniform fill; text is not uniform, so it is excluded from the
        # component by construction. Measuring "far from fill AND inside the
        # component" is self-contradictory and returns ~0 for every candidate,
        # which is exactly what it did — zero bubbles on BOTH polarities.
        # The marks are the holes punched through the fill inside its own
        # bounding box.
        sub = g[y0:y1, x0:x1]
        holes = (~box) & (np.abs(sub - fill) > INK_DELTA)
        ink = float(holes.sum() / max(box.size, 1))
        if ink < MIN_INK_FRAC or ink > MAX_INK_FRAC:
            continue
        out.append((x0, y0, x1, y1, comp))
    return out


def bubble_mask(gray, grow=3):
    """One boolean mask covering every bubble, slightly grown to take the
    stroke with it — a blur that stops at the outline looks like a mistake."""
    m = np.zeros(gray.shape, bool)
    for (_, _, _, _, comp) in find_bubbles(gray):
        m |= comp
    if not m.any() or grow <= 0:
        return m
    try:
        from scipy import ndimage
        m = ndimage.binary_dilation(m, iterations=int(grow))
    except ImportError:
        pass
    return m


def coverage(gray):
    """Fraction of the image that reads as speech bubble."""
    m = bubble_mask(gray, grow=0)
    return float(m.mean()) if m.size else 0.0


def blur_bubbles(rgb, gray=None, radius=14):
    """Return a copy of `rgb` with every detected bubble blurred.

    Blur, never inpaint. A slightly-too-large blur is ugly but honest; a fill
    invents panel art that was never drawn and ends up in an export nobody can
    account for.
    """
    from PIL import Image, ImageFilter
    if gray is None:
        gray = np.array(rgb.convert("L"))
    m = bubble_mask(gray)
    if not m.any():
        return rgb, 0.0
    blurred = rgb.filter(ImageFilter.GaussianBlur(radius))
    mask_img = Image.fromarray((m * 255).astype(np.uint8), mode="L")
    out = rgb.copy()
    out.paste(blurred, (0, 0), mask_img)
    return out, float(m.mean())
