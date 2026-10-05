"""The board as data (rebuild step 5, owner plan 2026-10-04).

The classic board (storyboard.py) is built as one large HTML string on the
server. The new Chapter page needs the same facts as JSON, so this module
computes them from the same files with the same rules:

  * "in the video" = ticked AND not rejected (the renderer's rule);
  * a segment's time is its position in the VIDEO, not the master timeline;
  * every panel row says where it went: on screen, left out (and why),
    folded into another paragraph, or unplaced.
"""

import json
import os
import re

from shot_planner import crop_area, crop_status, is_sub_crop

TALL_AR = 2.2          # must match render_segments.TALL_AR
LONG_HOLD = 12.0


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _natural(pid):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pid or "")]


def build(pdir, review, junk_reason=None):
    """-> {"panels": [...], "segments": [...], "summary": {...}}"""
    descs = _load(os.path.join(pdir, "descriptions.json"), []) or []
    if isinstance(descs, dict):
        descs = list(descs.values())
    descs.sort(key=lambda r: _natural(r.get("panel_id")))
    segs = _load(os.path.join(pdir, "segments.json"), []) or []
    scenes = _load(os.path.join(pdir, "script.json"), []) or []

    seg_by_panel = {}
    for s in segs:
        seg_by_panel.setdefault(s["panel_id"], []).append(s["seg_index"])
    unit_of = {}
    for sc in scenes:
        for pid in sc.get("panel_ids", []):
            unit_of[pid] = (sc.get("scene_id"), sc.get("text", ""))
    beat_panels = {}
    for s in segs:
        for b in s.get("beats", []):
            ps = beat_panels.setdefault(b["index"], [])
            if s["panel_id"] not in ps:
                ps.append(s["panel_id"])

    vt, out_segs = 0.0, []
    run = []
    for s in segs:
        default_inc = not s.get("silent_hold", False)
        st = (review.get(str(s["seg_index"])) or {}).get("status", "pending")
        in_video = bool(s["user_included"] if "user_included" in s else default_inc) and st != "rejected"
        box = s.get("crop_bbox_norm")
        cst = crop_status(box)
        d = next((x for x in descs if x.get("panel_id") == s["panel_id"]), {}) or {}
        w, h = d.get("width") or 0, d.get("height") or 0
        ar = h / max(w, 1)
        silent = bool(s.get("silent_hold") or not s.get("beats"))
        motion = ("silent hold" if silent else "planned crop + Ken Burns" if is_sub_crop(box)
                  else "tall strip scroll" if ar >= TALL_AR else
                  "Ken Burns " + ("push-in" if s["seg_index"] % 2 == 0 else "pull-out"))
        row = {"seg_index": s["seg_index"], "panel_id": s["panel_id"], "dur": round(float(s.get("dur") or 0), 2),
               "start": s.get("start"), "in_video": in_video, "review": st,
               "video_start": round(vt, 2) if in_video else None, "silent": silent, "motion": motion,
               "beats": [{"index": b["index"], "text": b.get("text", ""), "at": round(b["start"] - s["start"], 2),
                          "sliced": bool(b.get("file"))} for b in s.get("beats", [])],
               "crop": {"status": cst, "keeps": round(crop_area(box) * 100) if cst == "sub" else None,
                        "full_override": s.get("focus_source") == "manual_full",
                        "restorable": s.get("crop_bbox_norm_ai") is not None},
               "flags": [], "clip": s.get("clip")}
        if row["dur"] > LONG_HOLD:
            row["flags"].append("long hold")
        if ar >= TALL_AR and not is_sub_crop(box):
            row["flags"].append("tall strip")
        if in_video:
            vt = round(vt + row["dur"], 3)
            if run and run[-1]["panel_id"] == s["panel_id"]:
                run.append(row)
            else:
                _flush(run)
                run = [row]
        out_segs.append(row)
    _flush(run)

    panels = []
    counts = {"on_screen": 0, "left_out": 0, "folded": 0, "unplaced": 0}
    for i, d in enumerate(descs, 1):
        pid = d.get("panel_id")
        if pid in seg_by_panel:
            place = "on_screen"
        elif junk_reason and junk_reason(d):
            place = "left_out"
        elif pid in unit_of:
            place = "folded"
        else:
            place = "unplaced"
        counts[place] += 1
        shared = []
        for si in seg_by_panel.get(pid, []):
            s = next(x for x in segs if x["seg_index"] == si)
            for b in s.get("beats", []):
                others = [q for q in beat_panels.get(b["index"], []) if q != pid]
                if others:
                    shared.append({"beat": b["index"], "also_on": others})
        unit = unit_of.get(pid)
        panels.append({"n": i, "panel_id": pid, "width": d.get("width"), "height": d.get("height"),
                       "role": d.get("role") or "art", "ocr": (d.get("ocr_text") or "").strip(),
                       "seen": (d.get("visual_description") or "").strip(),
                       "place": place, "left_out_why": junk_reason(d) if (junk_reason and place == "left_out") else None,
                       "unit": unit[0] if unit else None, "unit_text": (unit[1] or "")[:400] if unit else None,
                       "segments": seg_by_panel.get(pid, []), "shared": shared})
    inv = [s for s in out_segs if s["in_video"]]
    summary = {"panels": len(panels), "segments": len(out_segs), "in_video": len(inv),
               "runtime": round(sum(s["dur"] for s in inv), 1),
               "long_holds": sum(1 for s in inv if "long hold" in s["flags"]),
               "silent": sum(1 for s in inv if s["silent"]), **counts}
    return {"panels": panels, "segments": out_segs, "summary": summary}


def _flush(run):
    if len(run) > 1:
        span = round(sum(x["dur"] for x in run), 1)
        if span >= 15:
            run[0]["flags"].append(f"same image {span:.0f}s")
