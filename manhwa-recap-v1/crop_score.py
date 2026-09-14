"""Composition-aware crop scoring — the gate that did not exist.

Until now a crop was validated on GEOMETRY ALONE: normalise it, clamp it,
reject it if it kept less than CROP_MIN_AREA of the panel. Nothing ever asked
whether the box actually contained the subject, or whether it was mostly a
speech bubble, or mostly empty background, or a thin sliver glued to one edge.
And there was no comparison against simply using the whole panel — the only
full-frame fallback was the area floor, so any box above 12% shipped.

This module supplies the missing half. It measures a candidate box against the
panel's own pixels and answers two questions geometry cannot:

    1. Is this box a good frame?     (composition score)
    2. Is it better than no crop?    (explicit full-frame comparison)

A crop is used ONLY when it beats the full-frame baseline by a real margin AND
survives linting. Otherwise the whole panel wins. That is the opposite of the
old default, and it is the right one: a full panel is never wrong, it is only
ever less tight, while a bad crop actively destroys the shot.

WHAT IS DELIBERATELY NOT USED AS A GATE: the model's own `focus_confidence`.
shot_planner.py:64 already records why — in the Martial Genius audit the two
WORST boxes in the chapter both carried confidence 1.0. A self-reported score
that is uncorrelated with quality is worse than no score, because it looks like
one. It is stored for humans to read; it never decides anything here.

NO ML. Every measurement comes from the panel's own pixels plus the speech
bubble detector production's splitter already ships (`_detect_bubbles` — light
connected blobs containing dark marks). Reused read-only; nothing here calls
into the splitter's pipeline.
"""

import os
import re

# ONE definition of the area floor, imported rather than re-declared, so this
# module and the crop contract can never disagree about what "too small" means.
try:
    from shot_planner import CROP_MIN_AREA, normalize_crop
except ImportError:                      # standalone use / tests
    CROP_MIN_AREA = 0.12

    def normalize_crop(box):
        try:
            x0, y0, x1, y1 = [float(v) for v in box]
        except (TypeError, ValueError):
            return None
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            return None
        return [x0, y0, x1, y1]


# ---------------------------------------------------------------- thresholds
# A crop must beat the full-frame baseline by THIS much to be worth taking.
# Deliberately not zero: a crop that is merely tied with the whole panel is a
# gamble with no upside, and every crop risks cutting something the reader
# needed.
CROP_MARGIN = float(os.environ.get("CROP_MARGIN", 0.08))

# Lint limits. Each is a reason to fall back to the whole panel.
MAX_TEXT_COVERAGE = float(os.environ.get("CROP_MAX_TEXT", 0.35))
MAX_BLANK_COVERAGE = float(os.environ.get("CROP_MAX_BLANK", 0.82))
# "Did the crop land on the subject?" cannot be answered by the raw fraction of
# panel ink it keeps: on a densely drawn page ink is EVERYWHERE, so a crop that
# keeps 20% of the area keeps roughly 20% of the ink no matter where it sits,
# and a raw-coverage floor would refuse almost every crop on exactly the
# detailed art that most wants one. What separates a subject from background is
# ink CONCENTRATION — so the gate is efficiency: ink kept per unit of area,
# relative to the panel's own average. 1.0 is average ground; above 1 means the
# box found something denser than its surroundings.
MIN_INK_EFFICIENCY = float(os.environ.get("CROP_MIN_EFFICIENCY", 0.75))
# An absolute floor as well, to catch a box on genuinely empty space.
MIN_SUBJECT_COVERAGE = float(os.environ.get("CROP_MIN_SUBJECT", 0.08))
MIN_ASPECT = float(os.environ.get("CROP_MIN_ASPECT", 0.28))
MAX_ASPECT = float(os.environ.get("CROP_MAX_ASPECT", 3.6))
EDGE_TOL = float(os.environ.get("CROP_EDGE_TOL", 0.015))

# Background tolerance, matching the splitter's own constant.
BG_TOL = 10


# ------------------------------------------------------------ beat intent
# The OLD trigger list included "bubble", "caption" and "text", so a beat about
# someone SPEAKING invited a crop onto the speech bubble. That is exactly
# backwards: a line of dialogue is carried by the narration audio, and the
# picture should be showing who said it, not a magnified word balloon.
DETAIL_KEYWORDS = [
    "face", "eyes", "eye", "glare", "expression", "gaze", "stare", "shocked",
    "angry", "smile", "grin", "tears", "tear", "weeping",
    "hand", "finger", "fist", "grip", "clasp", "grab", "hold", "gesture",
    "sword", "blade", "dagger", "weapon", "spear", "shield", "saber", "bow",
    "chain", "chains", "lock", "wound", "blood", "cut", "slash", "scar",
    "ring", "amulet", "badge", "crest", "mark", "seal",
]

# A beat that is ABOUT a piece of written text — a sign, a letter, a screen —
# legitimately wants that text framed. This is narrow on purpose: it is about
# something the viewer must READ, not about someone talking.
_READS_TEXT = re.compile(
    r"\b(sign|signboard|notice|note|letter|scroll|inscription|plaque|screen|"
    r"message|headline|banner|label|map|contract|list|menu)\b|"
    r"\b(read|reads|reading|written|inscribed|printed|scrawled)\b",
    re.IGNORECASE)

# Reported speech. These beats are carried by the audio; the frame should show
# the speaker or the scene, not the balloon.
_SPEECH_ONLY = re.compile(
    r"\b(said|says|asked|replied|answered|demanded|muttered|shouted|yelled|"
    r"whispered|called|told|warned|insisted|explained|admitted|snapped)\b",
    re.IGNORECASE)


def beat_is_about_text(text):
    """True when the narration is about something the viewer must READ."""
    return bool(_READS_TEXT.search(text or ""))


def beat_is_speech_only(text):
    """True when the beat only reports what somebody said.

    The negative gate. A speech beat on its own is not a reason to crop: the
    words are already in the narration track, so magnifying the bubble adds
    nothing and removes the person saying it.
    """
    t = text or ""
    if beat_is_about_text(t):
        return False
    if not _SPEECH_ONLY.search(t):
        return False
    return not any(k in t.lower() for k in DETAIL_KEYWORDS)


def should_crop_close(text):
    """Does this beat call for a detail crop at all?

    Same shape as production's gate, minus the text keywords that were inviting
    crops onto speech bubbles, plus a negative gate for speech-only beats.
    """
    t = (text or "").lower()
    if beat_is_speech_only(t):
        return False
    if beat_is_about_text(t):
        return True
    return any(w in t for w in DETAIL_KEYWORDS)


# --------------------------------------------------------------- measuring
def _splitter():
    """Production's splitter, imported READ-ONLY for its measurement helpers.
    Returns None when unavailable, and every caller degrades rather than
    guessing."""
    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ps = os.path.join(root, "panel-split")
        if os.path.isdir(ps) and ps not in sys.path:
            sys.path.insert(0, ps)
        import split_panels as SP
        return SP
    except Exception:
        return None


def panel_metrics(path):
    """Everything the scorer needs about one panel, measured once.

    Returns None when the panel cannot be read — callers treat that as "cannot
    judge", which means full frame, never a guessed crop.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            gray = np.array(im.convert("L"))
    except (OSError, ValueError):
        return None
    if gray.size == 0:
        return None

    h, w = gray.shape

    SP = _splitter()
    if SP is not None:
        # Production's estimator, not a mode. A mode breaks exactly where comic
        # pages live: a textured or screentoned page spreads across many values
        # while a flat black figure sits on ONE, so the mode picks the FIGURE
        # as background and every subsequent measurement inverts. Production's
        # version is explicitly "robust against large flat-color panel fills" —
        # it picks white or black, whichever has more pixels in tolerance.
        bg = SP._estimate_background_color(gray)
    else:
        bg = 255 if int((gray >= 245).sum()) >= int((gray <= 10).sum()) else 0
    ink = np.abs(gray.astype(np.int16) - bg) > BG_TOL
    total_ink = float(ink.sum())

    bubbles = []
    try:                                  # reused read-only from the splitter
        bubbles = SP._detect_bubbles(gray, bg) or [] if SP else []
    except Exception:
        # No scipy, no detector, no splitter — text coverage becomes unknown
        # and is simply not scored. Better than inventing a number.
        bubbles = []

    return {"h": h, "w": w, "bg": bg, "ink": ink, "total_ink": total_ink,
            "bubbles": bubbles, "has_text_map": bool(bubbles)}


def _px(box, w, h):
    x0, y0, x1, y1 = box
    return (max(0, int(x0 * w)), max(0, int(y0 * h)),
            min(w, int(x1 * w)), min(h, int(y1 * h)))


def measure(box, m):
    """Composition measurements for one candidate box."""
    import numpy as np
    c = normalize_crop(box)
    if c is None or m is None:
        return None
    x0, y0, x1, y1 = c
    px0, py0, px1, py1 = _px(c, m["w"], m["h"])
    if px1 - px0 < 2 or py1 - py0 < 2:
        return None

    sub = m["ink"][py0:py1, px0:px1]
    area = (x1 - x0) * (y1 - y0)
    kept_ink = float(sub.sum())

    # How much of the PANEL'S subject survived the crop. This is the measure
    # that matters most and the one geometry can never see: a 40% box that
    # keeps 90% of the art is a good frame; a 40% box that keeps 8% is a
    # magnified patch of background.
    subject_coverage = (kept_ink / m["total_ink"]) if m["total_ink"] else 0.0
    ink_density = kept_ink / float(sub.size) if sub.size else 0.0
    blank_coverage = 1.0 - ink_density

    # Speech-bubble area falling inside the box, as a fraction of the box.
    text_coverage = 0.0
    if m["bubbles"]:
        bx = 0.0
        for bx0, by0, bx1, by1 in m["bubbles"]:
            ox0, oy0 = max(px0, bx0), max(py0, by0)
            ox1, oy1 = min(px1, bx1), min(py1, by1)
            if ox1 > ox0 and oy1 > oy0:
                bx += (ox1 - ox0) * (oy1 - oy0)
        box_px_area = float((px1 - px0) * (py1 - py0))
        text_coverage = bx / box_px_area if box_px_area else 0.0

    bw, bh = x1 - x0, y1 - y0
    aspect = bw / bh if bh else 999.0
    # "Edge hugging": the box is pinned against a panel edge but does NOT span
    # it — the signature of a box that slid off the subject.
    touches = sum(1 for v in (x0 <= EDGE_TOL, y0 <= EDGE_TOL,
                              x1 >= 1 - EDGE_TOL, y1 >= 1 - EDGE_TOL) if v)
    edge_hug = touches >= 3 and area < 0.6

    # Did the crop slice through the subject? Compare ink density just inside
    # each cut edge against the panel's own average — a cut through a figure
    # leaves dense ink pressed against the border.
    cut_edges = 0
    avg = float(m["ink"].mean()) or 1e-6
    band = max(2, int(0.02 * max(m["w"], m["h"])))
    for edge, inside in (
            (x0 > EDGE_TOL, m["ink"][py0:py1, px0:min(px0 + band, px1)]),
            (x1 < 1 - EDGE_TOL, m["ink"][py0:py1, max(px1 - band, px0):px1]),
            (y0 > EDGE_TOL, m["ink"][py0:min(py0 + band, py1), px0:px1]),
            (y1 < 1 - EDGE_TOL, m["ink"][max(py1 - band, py0):py1, px0:px1])):
        if edge and inside.size and float(inside.mean()) > avg * 1.6:
            cut_edges += 1

    # Ink kept per unit of area, against the panel's own average density.
    panel_density = m["total_ink"] / float(m["ink"].size) if m["ink"].size else 0.0
    efficiency = (ink_density / panel_density) if panel_density else 0.0

    return {"area": area, "subject_coverage": subject_coverage,
            "efficiency": efficiency,
            "ink_density": ink_density, "blank_coverage": blank_coverage,
            "text_coverage": text_coverage, "aspect": aspect,
            "edge_hug": edge_hug, "cut_edges": cut_edges,
            "has_text_map": m["has_text_map"]}


# ----------------------------------------------------------------- scoring
def score(meas, beat_text=""):
    """Composition score, roughly 0..1. Higher is a better frame."""
    if not meas:
        return 0.0
    about_text = beat_is_about_text(beat_text)

    # Landing on the subject is the point of a crop, and concentration is what
    # identifies a subject on a densely drawn page.
    s = 0.45 * min(1.0, meas["efficiency"] / 1.3)
    # Still reward keeping a real share of the art, just not as the gate.
    s += 0.15 * min(1.0, meas["subject_coverage"] / 0.6)
    # Tightening is worth something, but only on top of landing well.
    s += 0.20 * (1.0 - meas["area"])
    # A frame that is mostly nothing is a bad frame.
    s += 0.15 * min(1.0, meas["ink_density"] / 0.28)

    if not about_text and meas["has_text_map"]:
        s -= 0.45 * max(0.0, meas["text_coverage"] - 0.15)
    elif about_text:
        s += 0.10 * min(1.0, meas["text_coverage"] / 0.4)

    if meas["blank_coverage"] > MAX_BLANK_COVERAGE:
        s -= 0.30
    if meas["edge_hug"]:
        s -= 0.20
    s -= 0.12 * meas["cut_edges"]
    a = meas["aspect"]
    if a < MIN_ASPECT or a > MAX_ASPECT:
        s -= 0.25
    return max(0.0, min(1.0, s))


def lint(meas, beat_text=""):
    """Hard reasons to refuse a crop outright, regardless of score."""
    if not meas:
        return ["unmeasurable"]
    out = []
    about_text = beat_is_about_text(beat_text)
    if meas["area"] < CROP_MIN_AREA:
        out.append(f"below the {CROP_MIN_AREA:.0%} area floor")
    if meas["efficiency"] < MIN_INK_EFFICIENCY:
        out.append(f"sits on emptier ground than the panel average "
                   f"({meas['efficiency']:.2f}x density)")
    if meas["subject_coverage"] < MIN_SUBJECT_COVERAGE:
        out.append(f"keeps only {meas['subject_coverage']:.0%} of the panel's art")
    if meas["blank_coverage"] > MAX_BLANK_COVERAGE:
        out.append(f"{meas['blank_coverage']:.0%} empty background")
    if not about_text and meas["has_text_map"] \
            and meas["text_coverage"] > MAX_TEXT_COVERAGE:
        out.append(f"{meas['text_coverage']:.0%} speech bubble")
    if meas["aspect"] < MIN_ASPECT or meas["aspect"] > MAX_ASPECT:
        out.append(f"aspect {meas['aspect']:.2f} is too thin")
    if meas["edge_hug"]:
        out.append("pinned into a corner of the panel")
    if meas["cut_edges"] >= 2:
        out.append("cuts through the subject on two edges")
    return out


FULL_FRAME = [0.0, 0.0, 1.0, 1.0]


def choose_crop(box, panel_path, beat_text="", metrics=None):
    """THE decision: this crop, or the whole panel?

    Returns (final_box, decision). `decision` always explains itself, so a
    downgrade can be shown to a reviewer instead of silently happening.
    """
    m = metrics if metrics is not None else panel_metrics(panel_path)
    if m is None:
        return FULL_FRAME, {"used": "full", "why": "panel not measurable",
                            "crop_score": None, "full_score": None}

    c = normalize_crop(box)
    if c is None or (c[2] - c[0]) * (c[3] - c[1]) > 0.985:
        return FULL_FRAME, {"used": "full", "why": "no sub-crop proposed",
                            "crop_score": None, "full_score": None}

    cm = measure(c, m)
    fm = measure(FULL_FRAME, m)
    cs, fs = score(cm, beat_text), score(fm, beat_text)
    problems = lint(cm, beat_text)

    if problems:
        return FULL_FRAME, {"used": "full", "why": "; ".join(problems),
                            "crop_score": round(cs, 3),
                            "full_score": round(fs, 3),
                            "downgraded": True, "lint": problems}
    if cs < fs + CROP_MARGIN:
        return FULL_FRAME, {
            "used": "full",
            "why": (f"the crop scored {cs:.2f} against {fs:.2f} for the whole "
                    f"panel — not a clear enough gain to risk cutting"),
            "crop_score": round(cs, 3), "full_score": round(fs, 3),
            "downgraded": True, "lint": []}
    return c, {"used": "crop", "why": (f"keeps {cm['subject_coverage']:.0%} of "
                                       f"the art in {cm['area']:.0%} of the frame"),
               "crop_score": round(cs, 3), "full_score": round(fs, 3),
               "downgraded": False, "lint": []}
