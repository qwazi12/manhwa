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

def detect_panels(image_path: str):
    """Return (PIL image, list of panel dicts). Each panel dict:
        {
          "panel_id": int,
          "bbox": [x0, y0, x1, y1],       # in page coordinates
          "type": "single" | "continuous_vertical_action",
          "shots": [ {"shot_id": int, "bbox": [x0,y0,x1,y1]}, ... ]
        }
    """
    img = Image.open(image_path).convert("RGB")
    gray = np.array(img.convert("L"))
    bg = _estimate_background_color(gray)

    panels = []
    panel_id = 0

    for (r0, r1) in _split_axis(gray, axis=0, bg_color=bg):
        strip = gray[r0:r1, :]
        for (c0, c1) in _split_axis(strip, axis=1, bg_color=bg):
            top = min(r0 + EDGE_MARGIN, r1)
            bottom = max(r1 - EDGE_MARGIN, top + 1)
            left = min(c0 + EDGE_MARGIN, c1)
            right = max(c1 - EDGE_MARGIN, left + 1)
            pw, ph = right - left, bottom - top
            if pw < MIN_PANEL_SIZE or ph < MIN_PANEL_SIZE:
                continue

            panel_id += 1
            panel_gray = gray[top:bottom, left:right]

            # Layer 2: is this a tall continuous panel needing sub-shots?
            if ph / pw >= TALL_RATIO:
                # only sub-split if there's no clean internal gutter already
                internal = _split_axis(panel_gray, axis=0, bg_color=bg)
                if len(internal) <= 1:
                    # Layer 2a: content-aware vision segmentation (preferred).
                    # A gutterless tall strip's beats are defined by meaning
                    # (caption + the art it narrates), which geometry can't see.
                    # The vision model returns the ordered beats with bounds +
                    # OCR + description in one call, generalizing across manhwa
                    # styles. Falls back to the density window on any failure.
                    vbeats = None
                    if USE_VISION_TALL:
                        vbeats = vision_segment.segment_tall_panel_image(
                            img.crop((left, top, right, bottom)))
                    if vbeats:
                        boxes = vision_segment.beats_to_pixel_bboxes(
                            vbeats, right - left, bottom - top)
                        shots = [{
                            "shot_id": i + 1,
                            "bbox": [left, top + bx["bbox"][1], right, top + bx["bbox"][3]],
                            # carry the vision OCR/description so these sub-beats
                            # arrive pre-described (no separate describe pass)
                            "ocr_text": bx["ocr_text"],
                            "visual_description": bx["visual_description"],
                        } for i, bx in enumerate(boxes)]
                        if len(shots) > 1:
                            panels.append({
                                "panel_id": panel_id,
                                "bbox": [left, top, right, bottom],
                                "type": "continuous_vertical_action",
                                "segmented_by": "vision",
                                "shots": shots,
                            })
                            continue
                    # Layer 2b: geometric density-window fallback
                    windows = _sliding_shot_windows(panel_gray, bg)
                    if len(windows) > 1:
                        shots = [{
                            "shot_id": i + 1,
                            "bbox": [left, top + wy0, right, top + wy1],
                        } for i, (wy0, wy1) in enumerate(windows)]
                        panels.append({
                            "panel_id": panel_id,
                            "bbox": [left, top, right, bottom],
                            "type": "continuous_vertical_action",
                            "segmented_by": "density-window",
                            "shots": shots,
                        })
                        continue

            panels.append({
                "panel_id": panel_id,
                "bbox": [left, top, right, bottom],
                "type": "single",
                "shots": [{"shot_id": 1, "bbox": [left, top, right, bottom]}],
            })

    if not panels:
        w, h = img.size
        panels = [{
            "panel_id": 1,
            "bbox": [0, 0, w, h],
            "type": "single",
            "shots": [{"shot_id": 1, "bbox": [0, 0, w, h]}],
        }]

    return img, panels


def _is_blank_crop(crop_gray: np.ndarray, bg_color: int) -> bool:
    """A crop counts as blank if almost none of it is non-background
    content — e.g. a transition page, a stray sliver of empty gutter
    that slipped past MIN_PANEL_SIZE, or a mostly-white 'meanwhile...'
    panel with just a few words of text."""
    return _content_density(crop_gray, bg_color) < BLANK_DENSITY_THRESHOLD


# ----------------------------------------------------------- save

def process_file(input_path: str, out_dir: str, prefix: str, all_meta: list,
                  archive_blanks: bool = ARCHIVE_BLANKS):
    img, panels = detect_panels(input_path)
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
