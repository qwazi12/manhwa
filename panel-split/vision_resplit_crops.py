"""
Re-split already-extracted TALL crops into meaning-based beats, in place.

Use this to upgrade a crop folder that was split before vision segmentation
existed (or whose tall panels were kept whole / cut by the density window):
for every crop taller than the vision threshold, ask the vision model for its
ordered beats, write one sub-crop per beat (named <panel_id>_beat_NN.png), and
rewrite the descriptions.json record set so each beat is its own panel with the
vision OCR + description already filled in. Originals are archived, not deleted.

    python vision_resplit_crops.py --crops DIR --descriptions FILE

Idempotent-ish: a crop already named ..._beat_NN is skipped.
"""

import argparse
import json
import os

from PIL import Image

import vision_segment


def _trim_bounds(pil_img):
    """Light content trim (same idea as split_panels): drop blank margins."""
    import numpy as np
    g = np.array(pil_img.convert("L"))
    h, w = g.shape
    white = int(np.median(g))  # dominant tone
    not_bg = np.abs(g.astype(np.int16) - white) > 12
    rows = np.where(not_bg.sum(axis=1) >= max(3, int(0.01 * w)))[0]
    cols = np.where(not_bg.sum(axis=0) >= max(3, int(0.01 * h)))[0]
    if rows.size == 0 or cols.size == 0:
        return (0, 0, w, h)
    pad = 6
    return (max(0, int(cols[0]) - pad), max(0, int(rows[0]) - pad),
            min(w, int(cols[-1]) + 1 + pad), min(h, int(rows[-1]) + 1 + pad))


def main():
    ap = argparse.ArgumentParser(description="Vision re-split tall crops in place")
    ap.add_argument("--crops", required=True, help="folder of extracted crops")
    ap.add_argument("--descriptions", required=True, help="descriptions.json to rewrite")
    ap.add_argument("--model", default="gemini-3.5-flash")
    args = ap.parse_args()

    with open(args.descriptions, encoding="utf-8") as f:
        records = json.load(f)
    by_id = {r["panel_id"]: r for r in records}

    archive = os.path.join(args.crops, "archive_preresplit")
    new_records = []
    n_expanded = 0

    for r in records:
        pid = r["panel_id"]
        if "_beat_" in pid:            # already a beat sub-crop
            new_records.append(r); continue
        path = os.path.join(args.crops, r.get("file", pid + ".png"))
        if not os.path.exists(path):
            new_records.append(r); continue

        beats = vision_segment.segment_tall_panel(path, model=args.model)
        if not beats:
            new_records.append(r); continue

        # expand: crop each beat, trim, save, make a description record
        img = Image.open(path).convert("RGB")
        W, H = img.size
        boxes = vision_segment.beats_to_pixel_bboxes(beats, W, H)
        os.makedirs(archive, exist_ok=True)
        for i, bx in enumerate(boxes):
            _, y0, _, y1 = bx["bbox"]
            sub = img.crop((0, y0, W, y1))
            tx0, ty0, tx1, ty1 = _trim_bounds(sub)
            sub = sub.crop((tx0, ty0, tx1, ty1))
            beat_id = f"{pid}_beat_{i:02d}"
            fname = beat_id + ".png"
            sub.save(os.path.join(args.crops, fname))
            sw, sh = sub.size
            new_records.append({
                "panel_id": beat_id, "file": fname,
                "width": sw, "height": sh, "bbox": [0, 0, sw, sh],
                "ocr_text": bx["ocr_text"],
                "visual_description": bx["visual_description"],
                "source": "vision-segment", "ok": True,
            })
        # archive the original tall crop out of the working set
        os.rename(path, os.path.join(archive, os.path.basename(path)))
        n_expanded += 1
        print(f"{pid}: {W}x{H} -> {len(boxes)} beats")

    # keep natural order
    def nk(name):
        import re
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]
    new_records.sort(key=lambda r: nk(r["panel_id"]))
    with open(args.descriptions, "w", encoding="utf-8") as f:
        json.dump(new_records, f, indent=2, ensure_ascii=False)
    print(f"\nExpanded {n_expanded} tall crop(s); "
          f"{len(records)} -> {len(new_records)} records. Originals in {archive}/")


if __name__ == "__main__":
    main()
