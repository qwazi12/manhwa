"""P2/P4 tests: the editor preview must show the frame the EXPORT renders,
and a full-frame crop box must not count as a crop (Session 25).

Why this exists:
  P2 — the board rendered /panelimg (the whole panel) while the exporter
       applied crop_bbox_norm, so an approved frame was not the delivered
       frame. On Martial Genius Ch.1 that affected 41 of 82 segments.
  P4 — `crop_bbox_norm is not None` treated a full-frame box as a crop, which
       silently disabled the TALL_AR scroll-pan branch for tall strips.

Run: python3 test_crop_preview.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "hyperframes"))

import render_segments as rs
import server as srv
from shot_planner import crop_rect_px, is_sub_crop, normalize_crop

FULL = [0.0, 0.0, 1.0, 1.0]
SUB = [0.09, 0.04, 0.96, 0.49]          # real Martial Genius seg 9 box


def _png(path, w, h):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"color=c=gray:s={w}x{h}", "-frames:v", "1", path],
                   check=True, capture_output=True)


def _branch(html):
    if 'id="card_container"' in html:
        return "crop"
    if 'id="pan_viewport"' in html:
        return "tall"
    return "normal"


def _seg(idx, pid, box, w, h, pdir):
    return {"seg_index": idx, "panel_id": pid, "crop_bbox_norm": box,
            "width": w, "height": h, "dur": 4.0, "start": 0.0, "beats": [],
            "panel_file": os.path.join(pdir, "crops", pid + ".png")}


def main():
    tmp = tempfile.mkdtemp(prefix="croptest_")
    assets = os.path.join(tmp, "assets")
    crops = os.path.join(tmp, "crops")
    os.makedirs(assets); os.makedirs(crops); os.makedirs(os.path.join(tmp, "thumbnails"))
    # tall strip (AR 3.0) and an ordinary landscape panel
    for d in (assets, crops):
        _png(os.path.join(d, "tall.png"), 300, 900)
        _png(os.path.join(d, "wide.png"), 600, 400)
    rs.ASSETS = assets
    srv.active_project_dir = lambda: tmp

    r = []

    # ---------------------------------------------------------------- P4
    r.append(("full-frame box is NOT a sub-crop", is_sub_crop(FULL) is False))
    r.append(("near-full box is NOT a sub-crop",
              is_sub_crop([0.005, 0.0, 1.0, 0.995]) is False))
    r.append(("a real sub-crop IS a sub-crop", is_sub_crop(SUB) is True))
    r.append(("missing/garbage boxes are not sub-crops",
              not any(is_sub_crop(b) for b in
                      (None, [], ["a", "b", "c", "d"], [0.1, 0.2],
                       [0.9, 0.9, 0.1, 0.1]))))
    r.append(("invalid boxes normalize to None",
              all(normalize_crop(b) is None for b in
                  (None, ["a", "b", "c", "d"], [0.1, 0.2], [0.5, 0.5, 0.1, 0.1]))))
    r.append(("out-of-range box is clamped into [0,1]",
              normalize_crop([-0.5, -0.5, 1.9, 1.9]) == FULL))

    tall_full = _seg(0, "tall", FULL, 300, 900, tmp)
    tall_sub = _seg(1, "tall", SUB, 300, 900, tmp)
    wide_full = _seg(2, "wide", FULL, 600, 400, tmp)
    r.append(("tall panel + full-frame box -> scroll-pan (P4 regression)",
              _branch(rs.seg_html(tall_full, tmp)) == "tall"))
    r.append(("tall panel + REAL sub-crop still crops",
              _branch(rs.seg_html(tall_sub, tmp)) == "crop"))
    r.append(("normal panel + full-frame box -> aspect-fit card",
              _branch(rs.seg_html(wide_full, tmp)) == "normal"))

    # the manifest lied about the size; the real file must decide the branch
    stale = _seg(3, "wide", FULL, 900, 2582, tmp)     # claims AR 2.87, really 0.67
    r.append(("branch follows the REAL image, not stale manifest dims",
              _branch(rs.seg_html(stale, tmp)) == "normal"))

    # ---------------------------------------------------------------- P2
    rect = srv.seg_crop_rect(tall_sub)
    r.append(("preview crop rect == exporter's box in pixels",
              rect == crop_rect_px(SUB, 300, 900)))
    r.append(("preview shows the FULL panel when the box is full-frame",
              srv.seg_crop_rect(tall_full) is None))
    r.append(("preview falls back to the full panel on a bad box",
              all(srv.seg_crop_rect(_seg(9, "tall", b, 300, 900, tmp)) is None
                  for b in (None, ["a", "b", "c", "d"], [0.9, 0.9, 0.1, 0.1]))))

    # the cached thumbnail is really cropped: its aspect ratio follows the box
    t = srv.ensure_thumb(tall_sub)
    ok = False
    if t and os.path.exists(t):
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", t], text=True).strip()
        tw, th = (int(v) for v in out.split("x"))
        want = ((SUB[2] - SUB[0]) * 300) / ((SUB[3] - SUB[1]) * 900)
        ok = abs(tw / th - want) < 0.06
    r.append(("cached thumbnail is cropped to the box's aspect ratio", ok))

    # framing changes must not be served from a stale cache
    swapped = dict(tall_sub); swapped["panel_id"] = "wide"
    recropped = dict(tall_sub); recropped["crop_bbox_norm"] = [0.2, 0.2, 0.8, 0.8]
    r.append(("thumb cache key changes when the crop changes",
              srv.thumb_path(1, tall_sub) != srv.thumb_path(1, recropped)))
    r.append(("thumb cache key changes when the panel is swapped",
              srv.thumb_path(1, tall_sub) != srv.thumb_path(1, swapped)))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
