"""Source-preserving orientation and framing contract for board and renderers.

Aspect ratio is evidence about shape, never proof that artwork is sideways.
EXIF is normalized first; an explicit reviewer quarter-turn is applied second;
crop coordinates refer to that oriented image. No source image is rewritten.
"""
from PIL import Image, ImageOps

TALL_AR = 2.2
ROTATIONS = (0, 90, 180, 270)  # clockwise


def rotation(seg):
    value = seg.get("rotation_deg", 0)
    if type(value) is not int or value not in ROTATIONS:
        raise ValueError("picture rotation must be 0, 90, 180 or 270 degrees clockwise")
    return value


def geometry(path, degrees=0):
    """Measured native and display dimensions, including mirrored EXIF cases."""
    rotation({"rotation_deg": degrees})
    if not path:              # a segment without a picture file is unmeasurable
        return None
    try:
        with Image.open(path) as im:
            w, h = im.size
            exif = im.getexif().get(274, 1)
        dw, dh = (h, w) if exif in (5, 6, 7, 8) else (w, h)
        if degrees in (90, 270):
            dw, dh = dh, dw
        return {"native_width": w, "native_height": h, "exif_orientation": exif,
                "width": dw, "height": dh, "rotation_deg": degrees}
    except (OSError, TypeError, ValueError):
        return None


def load_image(path, degrees=0):
    rotation({"rotation_deg": degrees})
    with Image.open(path) as im:
        out = ImageOps.exif_transpose(im)
        if degrees:
            # Pillow's transpose is lossless and does not interpolate/stretch.
            out = out.transpose({90: Image.Transpose.ROTATE_270,
                                 180: Image.Transpose.ROTATE_180,
                                 270: Image.Transpose.ROTATE_90}[degrees])
        return out.copy()


def asset_name(seg):
    deg = rotation(seg)
    return f"{seg['panel_id']}{'_r' + str(deg) if deg else ''}.png"


def clear_picture_edits(seg):
    """A new picture cannot inherit another picture's coordinate system."""
    for key in ("crop", "crop_box", "crop_bbox_norm", "crop_bbox_norm_ai",
                "focus_source", "focus_source_ai", "focus_reason", "focus_confidence",
                "rotation_deg", "rotation_source", "rotation_reason", "rotation_suggestion"):
        seg.pop(key, None)


def inspect(seg, path=None):
    """Read-only pre-render evidence and advisory findings; never guesses a turn."""
    from shot_planner import effective_crop
    deg = rotation(seg)
    g = geometry(path or seg.get("panel_file"), deg)
    if not g:
        return {"rotation_deg": deg, "geometry": None, "regime": "unknown",
                "findings": [{"rule": "F0-unreadable-image", "msg": "Cannot measure this picture; inspect the source before rendering."}]}
    w, h = g["width"], g["height"]
    crop = effective_crop(seg.get("crop_bbox_norm"))
    cw, ch = ((crop[2] - crop[0]) * w, (crop[3] - crop[1]) * h) if crop else (w, h)
    regime = "crop" if crop else "scroll" if h / w >= TALL_AR else "fit"
    findings = []
    # A wide source turning into a portrait crop is a particularly useful signal.
    if crop and w > h and ch / cw >= 1.25:
        findings.append({"rule": "F1-wide-to-portrait", "msg": "A landscape source is cropped into a portrait shot. Check the subject and action direction."})
    if regime == "fit" and h / w >= 1.8:
        findings.append({"rule": "F2-narrow-card", "msg": "This tall picture will appear as a narrow card. Check the original: choose a detail crop or a reviewed rotation if the artwork is sideways; do not stretch it."})
    if regime == "scroll":
        findings.append({"rule": "F3-scroll-review", "msg": "Tall-picture scroll: inspect the beginning, middle and end so the subject stays visible. Shape alone does not justify rotation."})
    sug = seg.get("rotation_suggestion")
    if not deg and isinstance(sug, dict) and sug.get("degrees") in ROTATIONS[1:]:
        findings.append({"rule": "F5-turn-suggested", "msg": f"Automatic check suggests turning this picture {sug['degrees']}° clockwise ({sug.get('confidence', 0):.0%}): {sug.get('evidence', '')} Compare with the original before applying."})
    if deg and seg.get("rotation_source") == "auto":
        findings.append({"rule": "F6-auto-turned", "msg": f"Turned automatically: {seg.get('rotation_reason', '')} Choose 'As source' to undo."})
    return {"rotation_deg": deg, "geometry": g, "regime": regime,
            "crop_size": [round(cw), round(ch)], "findings": findings,
            "suggestion": sug if not deg and isinstance(sug, dict) else None,
            "rotation_source": seg.get("rotation_source")}
