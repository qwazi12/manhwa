"""
Panel + sub-shot extractor for manhwa/webtoon pages.

Two layers:

  LAYER 1 — Primary panels.
    Detect gutters (blank white or black gaps) and cut the page into
    primary panel regions. Handles stacked panels (horizontal gutters)
    and side-by-side panels (vertical gutters), including a mix.

  LAYER 2 — Sub-shots inside a panel.
    A tall, continuous action panel (no internal gutter to cut on) is
    not one visual beat — it's several. For any panel whose height/width
    ratio exceeds TALL_RATIO and which has no clean internal gutter, fall
    back to a sliding-window pass: generate overlapping vertical windows,
    score each by content density (edge/ink coverage), and keep the best
    ones in top-to-bottom order as sub-shots.

  A short or gutter-cuttable panel stays a single shot.

Output:
    - PNG crops named panel_XXX.png (single) or panel_XXX_shot_YY.png
      (multi-beat panels).
    - panels.json metadata with bounding boxes, panel type, and reading
      order — compatible with the recap pipeline's shots.json contract.

Deliberately NOT included (Stage 3 territory, not built until Stage 2
validation shows it's needed): speech-bubble masking, face/person
detection, saliency models. The sliding-window fallback is the simple,
dependency-light stand-in for those.

Usage:
    python split_panels.py --input page.webp --out output_dir
    python split_panels.py --input pages_folder --out output_dir --batch
"""

import argparse
import json
import os
import sys
import numpy as np
from PIL import Image

import vision_segment

# Use the vision model to segment gutterless tall panels into meaning-based
# beats (preferred over the geometric density window). Auto-disabled when no
# GEMINI_API_KEY is present — the density-window fallback then handles them.
USE_VISION_TALL = bool(os.environ.get("GEMINI_API_KEY"))

# --- gutter / panel tunables ---
BG_COLOR_TOLERANCE = 10        # how close a pixel must be to bg to count as gutter
GUTTER_FRACTION = 0.985        # fraction of a row/col matching bg to call it a gutter
MIN_GUTTER_RUN = 8             # consecutive gutter rows/cols to count as a real gap
MIN_PANEL_SIZE = 80            # discard slivers smaller than this (px)
EDGE_MARGIN = 4                # trim px off each cut to avoid gutter bleed

# --- content bounding-box trim (removes baked-in blank margins) ---
# After a panel is isolated, it may still carry large blank (white/black)
# bands top/bottom/side — common when a print-style page has grid or
# side-by-side layouts, where a blank band inside one panel's column is NOT
# blank across the full page width, so the gutter pass never cut it. This is
# a per-crop tighten (never a new cut — it can't fragment a panel in two): it
# shrinks the box inward to where real ink begins. Speech bubbles keep their
# black outline/text so they survive; only truly empty margins are stripped.
TRIM_TO_CONTENT = True         # tighten each isolated crop to its content bbox
CONTENT_PAD = 6                # px of breathing room kept around the content
CONTENT_LINE_FRAC = 0.01       # a row/col needs >= this frac non-bg to be content

# --- blank-crop archiving tunables ---
BLANK_DENSITY_THRESHOLD = 0.015   # crops with less than this fraction non-bg content are "blank"
ARCHIVE_BLANKS = True             # move (not delete) blank crops to an archive/ subfolder
TALL_RATIO = 1.8               # panel h/w above this is a candidate for sub-shots
SHOT_WINDOW_MIN = 700          # min sub-shot window height (px)
SHOT_WINDOW_MAX = 1100         # max sub-shot window height (px)
SHOT_OVERLAP = 0.30            # fraction of overlap between candidate windows
SHOT_SCORE_KEEP = 0.55         # keep windows scoring >= this fraction of the best window


# ----------------------------------------------------------- background

def _estimate_background_color(gray: np.ndarray) -> int:
    """Gutters are almost always white or black. Pick whichever has more
    pixels within tolerance (robust against large flat-color panel fills)."""
    white_count = int(np.sum(gray >= 255 - BG_COLOR_TOLERANCE))
    black_count = int(np.sum(gray <= BG_COLOR_TOLERANCE))
    return 255 if white_count >= black_count else 0


# ----------------------------------------------------------- gutter cuts

def _find_gutter_runs(uniform_mask: np.ndarray, min_run: int):
    runs, start = [], None
    for i, val in enumerate(uniform_mask):
        if val and start is None:
            start = i
        elif not val and start is not None:
            if i - start >= min_run:
                runs.append((start, i))
            start = None
    if start is not None and len(uniform_mask) - start >= min_run:
        runs.append((start, len(uniform_mask)))
    return runs


def _cut_points_from_gutters(runs, length: int):
    points = [0] + [(s + e) // 2 for s, e in runs] + [length]
    segments = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        if b - a >= MIN_PANEL_SIZE:
            segments.append((a, b))
    return segments


def _split_axis(arr: np.ndarray, axis: int, bg_color: int):
    close_to_bg = np.abs(arr.astype(np.int16) - bg_color) <= BG_COLOR_TOLERANCE
    frac = close_to_bg.mean(axis=1 if axis == 0 else 0)
    # Vertical gutters (side-by-side panels) are rarer and more prone to
    # false positives from noise, so hold them to a stricter fraction.
    threshold = GUTTER_FRACTION if axis == 0 else max(GUTTER_FRACTION, 0.995)
    uniform = frac >= threshold
    runs = _find_gutter_runs(uniform, MIN_GUTTER_RUN)
    return _cut_points_from_gutters(runs, arr.shape[axis])


# ----------------------------------------------------------- content score

def _content_density(gray_region: np.ndarray, bg_color: int) -> float:
    """Fraction of pixels that are NOT background — a cheap proxy for how
    much 'stuff' (art, faces, action) is in a region. Used to score
    sliding-window sub-shot candidates without any ML."""
    if gray_region.size == 0:
        return 0.0
    not_bg = np.abs(gray_region.astype(np.int16) - bg_color) > BG_COLOR_TOLERANCE
    return float(not_bg.mean())


# ----------------------------------------------------------- content trim

def _content_bounds(region_gray: np.ndarray, bg_color: int, pad: int = CONTENT_PAD):
    """Return (x0, y0, x1, y1) — the tight content box within region_gray,
    padded by `pad`. A row/col counts as content only if enough of it is
    non-background (CONTENT_LINE_FRAC), so a stray speck won't defeat the trim
    but a speech bubble's outline/text will. Returns the full region if there
    is no real content (leave blank crops for the blank-archiver to handle)."""
    h, w = region_gray.shape
    if h == 0 or w == 0:
        return 0, 0, w, h
    not_bg = np.abs(region_gray.astype(np.int16) - bg_color) > BG_COLOR_TOLERANCE
    row_thresh = max(3, int(CONTENT_LINE_FRAC * w))
    col_thresh = max(3, int(CONTENT_LINE_FRAC * h))
    rows = np.where(not_bg.sum(axis=1) >= row_thresh)[0]
    cols = np.where(not_bg.sum(axis=0) >= col_thresh)[0]
    if rows.size == 0 or cols.size == 0:
        return 0, 0, w, h
    y0 = max(0, int(rows[0]) - pad)
    y1 = min(h, int(rows[-1]) + 1 + pad)
    x0 = max(0, int(cols[0]) - pad)
    x1 = min(w, int(cols[-1]) + 1 + pad)
    return x0, y0, x1, y1


# ----------------------------------------------------------- sub-shots

def _sliding_shot_windows(panel_gray: np.ndarray, bg_color: int):
    """Return a list of (y0, y1) sub-shot windows for a tall continuous
    panel, chosen by content-density scoring over overlapping windows."""
    h = panel_gray.shape[0]
    win = int(np.clip(h / 3, SHOT_WINDOW_MIN, SHOT_WINDOW_MAX))
    if win >= h:
        return [(0, h)]

    step = max(int(win * (1 - SHOT_OVERLAP)), 1)
    candidates = []
    y = 0
    while y < h:
        y1 = min(y + win, h)
        score = _content_density(panel_gray[y:y1, :], bg_color)
        candidates.append({"y0": y, "y1": y1, "score": score})
        if y1 >= h:
            break
        y += step

    if not candidates:
        return [(0, h)]

    best = max(c["score"] for c in candidates) or 1.0
    kept = [c for c in candidates if c["score"] >= SHOT_SCORE_KEEP * best]

    # suppress heavily-overlapping kept windows: greedily keep top-scored,
    # drop any candidate overlapping an already-kept one by >60%
    kept.sort(key=lambda c: c["score"], reverse=True)
    chosen = []
    for c in kept:
        overlap = False
        for k in chosen:
            inter = max(0, min(c["y1"], k["y1"]) - max(c["y0"], k["y0"]))
            if inter > 0.6 * (c["y1"] - c["y0"]):
                overlap = True
                break
        if not overlap:
            chosen.append(c)

    chosen.sort(key=lambda c: c["y0"])
    return [(c["y0"], c["y1"]) for c in chosen] or [(0, h)]


# ----------------------------------------------------------- main detect

# ----------------------------------------------------------- yolo detect
def detect_panels_yolo(image_path: str, model_path: str = None) -> list:
    """Detect panels using the pretrained YOLO model.
    Returns a list of bounding boxes: [[x0, y0, x1, y1], ...] sorted in reading order.
    """
    if model_path is None:
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yolo_panel_detector.pt")
    if not os.path.exists(model_path):
        # Loud, greppable, on BOTH streams: a deployment without the weights
        # silently produced 2x the crops (268 vs 126 on dungeon-odyssey ch1)
        # before anyone noticed — never let this degrade quietly again.
        msg = (f"WARNING: YOLO WEIGHTS MISSING at {model_path} — "
               f"falling back to geometric splits (worse crops). "
               f"Ship yolo_panel_detector.pt into the image (see deploy/Dockerfile).")
        print(msg)
        print(msg, file=sys.stderr)
        return []
    
    try:
        from ultralytics import YOLO
        model = YOLO(model_path)
        # Run inference (conf=0.3 is standard for layouts)
        results = model.predict(source=image_path, conf=0.3, verbose=False)
        boxes = []
        if results and len(results) > 0:
            for box in results[0].boxes:
                coords = box.xyxy[0].tolist()
                x0, y0, x1, y1 = [int(round(c)) for c in coords]
                boxes.append([x0, y0, x1, y1])
        
        if not boxes:
            return []
            
        # Group boxes by row to resolve reading order (overlap threshold = 40%)
        boxes.sort(key=lambda b: b[1])  # sort by top coordinate first
        rows = []
        for b in boxes:
            placed = False
            for r in rows:
                r_top = min(x[1] for x in r)
                r_bot = max(x[3] for x in r)
                overlap = max(0, min(b[3], r_bot) - max(b[1], r_top))
                h = b[3] - b[1]
                if overlap > 0.4 * h or overlap > 0.4 * (r_bot - r_top):
                    r.append(b)
                    placed = True
                    break
            if not placed:
                rows.append([b])
        
        sorted_boxes = []
        for r in rows:
            r.sort(key=lambda b: b[0])  # sort left-to-right in the row
            sorted_boxes.extend(r)
            
        return sorted_boxes
    except Exception as e:
        print(f"YOLO detection failed: {e}. Falling back.", file=sys.stderr)
        return []


def _apply_bleed_guard(bbox, gray_full, bg_color, max_expand=30):
    """Expand the bbox coordinates if there are non-background pixels on the borders,
    preventing clipping of character limbs/art bleeding into the gutters.
    """
    x0, y0, x1, y1 = bbox
    h_full, w_full = gray_full.shape
    
    # Check left border (x0)
    max_exp = max_expand
    while x0 > 0 and max_exp > 0:
        col = gray_full[y0:y1, x0]
        non_bg_ratio = np.mean(np.abs(col.astype(np.int16) - bg_color) > BG_COLOR_TOLERANCE)
        if non_bg_ratio > 0.05:
            x0 -= 1
            max_exp -= 1
        else:
            break
            
    # Check right border (x1)
    max_exp = max_expand
    while x1 < w_full and max_exp > 0:
        col = gray_full[y0:y1, x1 - 1]
        non_bg_ratio = np.mean(np.abs(col.astype(np.int16) - bg_color) > BG_COLOR_TOLERANCE)
        if non_bg_ratio > 0.05:
            x1 += 1
            max_exp -= 1
        else:
            break
            
    # Check top border (y0)
    max_exp = max_expand
    while y0 > 0 and max_exp > 0:
        row = gray_full[y0, x0:x1]
        non_bg_ratio = np.mean(np.abs(row.astype(np.int16) - bg_color) > BG_COLOR_TOLERANCE)
        if non_bg_ratio > 0.05:
            y0 -= 1
            max_exp -= 1
        else:
            break
            
    # Check bottom border (y1)
    max_exp = max_expand
    while y1 < h_full and max_exp > 0:
        row = gray_full[y1 - 1, x0:x1]
        non_bg_ratio = np.mean(np.abs(row.astype(np.int16) - bg_color) > BG_COLOR_TOLERANCE)
        if non_bg_ratio > 0.05:
            y1 += 1
            max_exp -= 1
        else:
            break
            
    return [x0, y0, x1, y1]


# -------------------------------------------- S1/S1b: coverage + anchors

# Coverage-gated hybrid split (S1): YOLO's boxes are only trusted as far as
# they COVER the page's actual content. Content rows not covered by any box
# are re-attacked with the geometric gutter splitter, and whatever still
# remains uncovered ships as a density-band crop (fail-open: an imperfect
# crop on the review board beats silently lost story — the 2026-07-19 audit
# found YOLO alone kept only 23-76% of dungeon-odyssey ch2's art).
COVERAGE_TARGET = 0.85         # below this, gap recovery kicks in
GAP_MIN_DENSITY = 0.04         # a gap band needs >= this ink fraction to matter
GAP_MIN_H = 120                # ignore uncovered slivers shorter than this (px)

# Anchor sweep (S1b): regions with STORY SIGNAL must never be dropped,
# whatever the panel detectors think. Signals: speech bubbles (geometric,
# any art style) and figures (local pretrained person model, best-effort).
ANCHOR_COVERED_FRAC = 0.6      # anchor is safe if >=60% of it lies in a kept box
BUBBLE_MIN_AREA = 4000         # px^2 — smaller light blobs are noise/SFX
BUBBLE_MAX_AREA_FRAC = 0.25    # a "bubble" bigger than 25% of the page isn't one
FIGURE_CONF = 0.30
FIGURE_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "yolov8n.pt")   # generic COCO model, person class


def _row_coverage(page_h, boxes):
    cov = np.zeros(page_h, dtype=bool)
    for x0, y0, x1, y1 in boxes:
        cov[max(0, y0):min(page_h, y1)] = True
    return cov


def _content_rows(gray, bg):
    not_bg = np.abs(gray.astype(np.int16) - bg) > BG_COLOR_TOLERANCE
    return not_bg.mean(axis=1) >= GAP_MIN_DENSITY


def _uncovered_bands(gray, bg, boxes):
    """(y0, y1) bands of real content not covered by any detected box."""
    content = _content_rows(gray, bg)
    cov = _row_coverage(gray.shape[0], boxes)
    miss = content & ~cov
    bands, start = [], None
    for i, v in enumerate(miss):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= GAP_MIN_H:
                bands.append((start, i))
            start = None
    if start is not None and len(miss) - start >= GAP_MIN_H:
        bands.append((start, len(miss)))
    # merge bands separated by thin covered/blank strips (< GAP_MIN_H)
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < GAP_MIN_H:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(list(b))
    return [tuple(b) for b in merged]


def _detect_bubbles(gray, bg):
    """Speech-bubble anchors, no ML: large light connected blobs containing
    dark marks (text). Works on dark pages where gutter logic fails."""
    try:
        from scipy import ndimage
    except ImportError:
        return []
    h, w = gray.shape
    light = gray >= 225
    lbl, n = ndimage.label(light)
    if not n:
        return []
    out = []
    for sl in ndimage.find_objects(lbl):
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        area = (y1 - y0) * (x1 - x0)
        if area < BUBBLE_MIN_AREA or area > BUBBLE_MAX_AREA_FRAC * h * w:
            continue
        if (y1 - y0) < 40 or (x1 - x0) < 60:
            continue
        region = gray[y0:y1, x0:x1]
        dark_frac = float((region <= 80).mean())
        if 0.01 <= dark_frac <= 0.5:      # has text but isn't mostly art
            out.append([x0, y0, x1, y1])
    return out


def _detect_figures(image_path):
    """Figure/person anchors via the generic pretrained model (best-effort:
    missing weights or import failure just returns [] — the density net
    below still guarantees coverage)."""
    try:
        from ultralytics import YOLO
        # local baked weights preferred; else ultralytics resolves the name
        # (auto-download on first use — deploy/Dockerfile bakes it for prod)
        model = FIGURE_MODEL if os.path.exists(FIGURE_MODEL) else "yolov8n.pt"
        res = YOLO(model).predict(source=image_path, conf=FIGURE_CONF,
                                  classes=[0], verbose=False)
        out = []
        for r in res:
            for b in r.boxes:
                x0, y0, x1, y1 = [int(round(c)) for c in b.xyxy[0].tolist()]
                out.append([x0, y0, x1, y1])
        return out
    except Exception as e:
        print(f"figure-anchor detection skipped: {e}", file=sys.stderr)
        return []


def _anchor_uncovered(anchors, boxes):
    """Anchors whose area is not ANCHOR_COVERED_FRAC-covered by kept boxes."""
    out = []
    for a in anchors:
        ax0, ay0, ax1, ay1 = a
        area = max(1, (ax1 - ax0) * (ay1 - ay0))
        covered = 0
        for x0, y0, x1, y1 in boxes:
            iw = max(0, min(ax1, x1) - max(ax0, x0))
            ih = max(0, min(ay1, y1) - max(ay0, y0))
            covered = max(covered, iw * ih)
        if covered / area < ANCHOR_COVERED_FRAC:
            out.append(a)
    return out


def _grow_to_quiet_rows(gray, bg, y0, y1, max_grow=400):
    """Expand a band to the nearest low-ink rows so cuts land in visual
    pauses instead of through art."""
    h = gray.shape[0]
    density = ( np.abs(gray.astype(np.int16) - bg) > BG_COLOR_TOLERANCE ).mean(axis=1)
    g = 0
    while y0 > 0 and density[y0 - 1] > 0.02 and g < max_grow:
        y0 -= 1; g += 1
    g = 0
    while y1 < h and density[y1] > 0.02 and g < max_grow:
        y1 += 1; g += 1
    return y0, y1


def _merge_boxes(boxes, gap=40):
    """Merge vertically-overlapping/near-touching full-width bands."""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: b[1])
    out = [list(boxes[0])]
    for b in boxes[1:]:
        if b[1] <= out[-1][3] + gap:
            out[-1][2] = max(out[-1][2], b[2])
            out[-1][3] = max(out[-1][3], b[3])
            out[-1][0] = min(out[-1][0], b[0])
        else:
            out.append(list(b))
    return [tuple(b) for b in out]


# ----------------------------------------------------------- main detect

def _layer2_shots(img, gray, bg, bbox):
    """Shared Layer-2 logic: decide whether bbox is one shot or a tall
    continuous panel that needs sub-shots (vision, else density windows)."""
    x0, y0, x1, y1 = bbox
    pw, ph = x1 - x0, y1 - y0
    panel_gray = gray[y0:y1, x0:x1]
    if pw <= 0 or ph / max(pw, 1) < TALL_RATIO:
        return "single", None, [{"shot_id": 1, "bbox": [x0, y0, x1, y1]}]
    internal = _split_axis(panel_gray, axis=0, bg_color=bg)
    if len(internal) > 1:
        return "single", None, [{"shot_id": 1, "bbox": [x0, y0, x1, y1]}]
    if USE_VISION_TALL:
        vbeats = vision_segment.segment_tall_panel_image(img.crop((x0, y0, x1, y1)))
        if vbeats:
            boxes = vision_segment.beats_to_pixel_bboxes(vbeats, pw, ph)
            shots = [{
                "shot_id": i + 1,
                "bbox": [x0, y0 + bx["bbox"][1], x1, y0 + bx["bbox"][3]],
                "ocr_text": bx["ocr_text"],
                "visual_description": bx["visual_description"],
            } for i, bx in enumerate(boxes)]
            if len(shots) > 1:
                return "continuous_vertical_action", "vision", shots
    windows = _sliding_shot_windows(panel_gray, bg)
    if len(windows) > 1:
        shots = [{"shot_id": i + 1, "bbox": [x0, y0 + wy0, x1, y0 + wy1]}
                 for i, (wy0, wy1) in enumerate(windows)]
        return "continuous_vertical_action", "density-window", shots
    return "single", None, [{"shot_id": 1, "bbox": [x0, y0, x1, y1]}]


def detect_panels(image_path: str):
    """Return (PIL image, list of panel dicts, stats dict).

    Detection is COVERAGE-GATED (S1) with ANCHOR back-stops (S1b):
      1. YOLO panel boxes (bleed-guarded)
      2. geometric gutter split on any content bands YOLO left uncovered
      3. speech-bubble + figure anchors force-include what both missed
      4. remaining content bands ship as density crops (fail-open)
    Every panel records its "detector"; stats record per-stage coverage so
    the pipeline/UI can surface split quality (S2).
    """
    img = Image.open(image_path).convert("RGB")
    gray = np.array(img.convert("L"))
    bg = _estimate_background_color(gray)
    W, H = img.size

    boxes = []          # [x0,y0,x1,y1] accepted panel boxes, any detector
    origins = []        # parallel list: which stage produced each box

    def content_coverage():
        content = _content_rows(gray, bg)
        n = int(content.sum())
        if not n:
            return 1.0
        cov = _row_coverage(H, boxes)
        return float((content & cov).sum() / n)

    # -- 1. YOLO ---------------------------------------------------------
    for bbox in detect_panels_yolo(image_path):
        bbox = _apply_bleed_guard(bbox, gray, bg)
        x0, y0, x1, y1 = bbox
        if x1 - x0 >= MIN_PANEL_SIZE and y1 - y0 >= MIN_PANEL_SIZE:
            boxes.append([x0, y0, x1, y1])
            origins.append("yolo")
    cov_yolo = content_coverage()

    # -- 2. geometric on uncovered bands ---------------------------------
    if cov_yolo < COVERAGE_TARGET:
        for (by0, by1) in _uncovered_bands(gray, bg, boxes):
            band = gray[by0:by1, :]
            cuts = _split_axis(band, axis=0, bg_color=bg)
            band_boxes = []
            for (r0, r1) in cuts:
                if r1 - r0 >= MIN_PANEL_SIZE:
                    band_boxes.append([0, by0 + r0, W, by0 + r1])
            if not band_boxes:
                gy0, gy1 = _grow_to_quiet_rows(gray, bg, by0, by1)
                band_boxes = [[0, gy0, W, gy1]]
            for b in _merge_boxes(band_boxes):
                boxes.append(list(b))
                origins.append("gap-geometric" if len(cuts) > 1 else "gap-density")
    cov_gaps = content_coverage()

    # -- 3. anchors: bubbles + figures ------------------------------------
    anchors = ([("bubble", a) for a in _detect_bubbles(gray, bg)]
               + [("figure", a) for a in _detect_figures(image_path)])
    n_anchor_rescued = 0
    for kind, a in anchors:
        if _anchor_uncovered([a], boxes):
            ay0, ay1 = _grow_to_quiet_rows(gray, bg, a[1], a[3])
            boxes.append([0, ay0, W, ay1])
            origins.append(f"anchor-{kind}")
            n_anchor_rescued += 1
    cov_final = content_coverage()

    # -- assemble in reading order ----------------------------------------
    order = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
    panels = []
    for rank, i in enumerate(order, 1):
        x0, y0, x1, y1 = boxes[i]
        ptype, seg_by, shots = _layer2_shots(img, gray, bg, (x0, y0, x1, y1))
        p = {"panel_id": rank, "bbox": [x0, y0, x1, y1], "type": ptype,
             "detector": origins[i], "shots": shots}
        if seg_by:
            p["segmented_by"] = seg_by
        panels.append(p)

    if not panels:
        panels = [{"panel_id": 1, "bbox": [0, 0, W, H], "type": "single",
                   "detector": "whole-page", "shots": [{"shot_id": 1, "bbox": [0, 0, W, H]}]}]

    stats = {
        "coverage_yolo": round(cov_yolo, 3),
        "coverage_after_gaps": round(cov_gaps, 3),
        "coverage_final": round(cov_final, 3),
        "n_yolo": origins.count("yolo"),
        "n_gap": sum(1 for o in origins if o.startswith("gap-")),
        "n_anchor": n_anchor_rescued,
        "n_bubbles": sum(1 for k, _ in anchors if k == "bubble"),
        "n_figures": sum(1 for k, _ in anchors if k == "figure"),
    }
    print(f"{os.path.basename(image_path)}: yolo {stats['n_yolo']} boxes "
          f"({cov_yolo:.0%} of content) + gaps {stats['n_gap']} "
          f"+ anchors {stats['n_anchor']} -> coverage {cov_final:.0%}",
          file=sys.stderr)
    if cov_final < COVERAGE_TARGET:
        print(f"WARNING: SPLIT COVERAGE {cov_final:.0%} < {COVERAGE_TARGET:.0%} "
              f"on {os.path.basename(image_path)} — review this page's crops.",
              file=sys.stderr)
    return img, panels, stats


def _is_blank_crop(crop_gray: np.ndarray, bg_color: int) -> bool:
    """A crop counts as blank if almost none of it is non-background
    content — e.g. a transition page, a stray sliver of empty gutter
    that slipped past MIN_PANEL_SIZE, or a mostly-white 'meanwhile...'
    panel with just a few words of text."""
    return _content_density(crop_gray, bg_color) < BLANK_DENSITY_THRESHOLD


# ----------------------------------------------------------- save

def process_file(input_path: str, out_dir: str, prefix: str, all_meta: list,
                  archive_blanks: bool = ARCHIVE_BLANKS):
    img, panels, stats = detect_panels(input_path)
    gray_full = np.array(img.convert("L"))
    bg = _estimate_background_color(gray_full)
    os.makedirs(out_dir, exist_ok=True)
    archive_dir = os.path.join(out_dir, "archive_blank")

    n_crops, n_blank = 0, 0

    def save_crop(bbox, fname):
        nonlocal n_crops, n_blank
        x0, y0, x1, y1 = bbox
        # tighten the isolated crop to its content box (strips baked-in
        # blank margins the gutter pass couldn't see); returns the trimmed
        # page-coordinate bbox so panels.json reflects what was actually saved
        if TRIM_TO_CONTENT:
            tx0, ty0, tx1, ty1 = _content_bounds(gray_full[y0:y1, x0:x1], bg)
            x0, y0, x1, y1 = x0 + tx0, y0 + ty0, x0 + tx1, y0 + ty1
        crop = img.crop((x0, y0, x1, y1))
        crop_gray = gray_full[y0:y1, x0:x1]
        blank = _is_blank_crop(crop_gray, bg)
        if blank and archive_blanks:
            os.makedirs(archive_dir, exist_ok=True)
            crop.save(os.path.join(archive_dir, fname))
            n_blank += 1
        else:
            crop.save(os.path.join(out_dir, fname))
        n_crops += 1
        return blank, [x0, y0, x1, y1]

    for p in panels:
        base = f"{prefix}_panel_{p['panel_id']:03d}"
        if p["type"] == "single":
            p["blank"], p["bbox"] = save_crop(p["bbox"], base + ".png")
        else:
            for s in p["shots"]:
                fname = f"{base}_shot_{s['shot_id']:02d}.png"
                s["blank"], s["bbox"] = save_crop(s["bbox"], fname)

    multi = sum(1 for p in panels if p["type"] != "single")
    kept = n_crops - n_blank
    msg = (f"{os.path.basename(input_path)}: {len(panels)} panel(s), "
           f"{n_crops} crop(s) ({multi} multi-beat)")
    if archive_blanks:
        msg += f" — {kept} kept, {n_blank} archived as blank"
    print(msg + f" -> {out_dir}")

    all_meta.append({
        "source": os.path.basename(input_path),
        "prefix": prefix,
        "coverage": stats,          # S2: per-page split quality, surfaced in UI
        "panels": panels,
    })
    return n_crops


def retrim_directory(crop_dir: str):
    """Apply the content-bbox trim to already-extracted crops, in place.

    Each file is a standalone isolated crop, so the trim runs on the whole
    image. Use this to tighten a crop folder that was produced before
    TRIM_TO_CONTENT existed, without re-running the whole split. Filenames and
    IDs are preserved; only the pixels (and thus width/height) change."""
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    files = sorted(f for f in os.listdir(crop_dir)
                   if os.path.splitext(f)[1].lower() in exts)
    changed, saved_px = 0, 0
    for fname in files:
        path = os.path.join(crop_dir, fname)
        img = Image.open(path).convert("RGB")
        gray = np.array(img.convert("L"))
        bg = _estimate_background_color(gray)
        x0, y0, x1, y1 = _content_bounds(gray, bg)
        w, h = img.size
        if (x0, y0, x1, y1) == (0, 0, w, h):
            continue
        img.crop((x0, y0, x1, y1)).save(path)
        changed += 1
        saved_px += (w * h) - (x1 - x0) * (y1 - y0)
        print(f"  {fname}: {w}x{h} -> {x1-x0}x{y1-y0}")
    print(f"\nRe-trimmed {changed}/{len(files)} crops in {crop_dir} "
          f"({saved_px/1e6:.1f}M px of blank margin removed).")


def main():
    ap = argparse.ArgumentParser(
        description="Extract primary panels and sub-shots from manhwa/webtoon pages")
    ap.add_argument("--retrim-dir",
                    help="apply the content-bbox trim to existing crops in this "
                         "folder in place (no re-split); ignores --input/--out")
    ap.add_argument("--input", help="a page image, or a folder if --batch")
    ap.add_argument("--out", help="output folder for crops + panels.json")
    ap.add_argument("--batch", action="store_true", help="treat --input as a folder of pages")
    ap.add_argument("--keep-blanks", action="store_true",
                    help="don't archive near-blank crops (transition pages, empty gutter "
                         "slivers) — keep everything in the main output folder")
    args = ap.parse_args()

    if args.retrim_dir:
        retrim_directory(args.retrim_dir)
        return
    if not args.input or not args.out:
        ap.error("--input and --out are required (unless using --retrim-dir)")

    archive_blanks = not args.keep_blanks
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    all_meta = []

    if args.batch:
        files = sorted(f for f in os.listdir(args.input)
                       if os.path.splitext(f)[1].lower() in exts)
        if not files:
            raise SystemExit(f"No images found in {args.input}")
        total = 0
        for idx, fname in enumerate(files, 1):
            total += process_file(os.path.join(args.input, fname),
                                  args.out, f"page{idx:03d}", all_meta, archive_blanks)
        print(f"\nDone: {total} crops from {len(files)} pages.")
    else:
        process_file(args.input, args.out, "page001", all_meta, archive_blanks)

    if archive_blanks:
        print("Blank/near-empty crops were moved to archive_blank/ instead of deleted —")
        print("check that folder before assuming nothing useful got filtered out.")

    with open(os.path.join(args.out, "panels.json"), "w", encoding="utf-8") as f:
        json.dump({"pages": all_meta}, f, indent=2)


    print("\nWrote panels.json with panel/shot coordinates and reading order.")
    print("Review the crops, then rename them (001.png, 002.png, ...) in final")
    print("story order before feeding into the recap pipeline's input/images/.")
    print("Note: multi-beat panels are split into _shot_NN files — a tall action")
    print("panel becomes several crops. Verify these look right; drop any that don't.")


if __name__ == "__main__":
    main()
