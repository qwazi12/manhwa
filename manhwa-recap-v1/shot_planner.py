import json
import os
import time
import base64
import mimetypes
from concurrent.futures import ThreadPoolExecutor

# Cost/abuse guardrails
_REVIEW_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_ui")
try:
    import sys
    if os.path.isdir(_REVIEW_UI) and _REVIEW_UI not in sys.path:
        sys.path.insert(0, _REVIEW_UI)
    import usage
except ImportError:
    usage = None

MODEL_NAME = "gemini-2.5-flash"

# Keywords that trigger a Gemini close-up crop analysis
_DETAIL_KEYWORDS = [
    "face", "eyes", "eye", "glare", "expression", "gaze", "stare", "look", "shocked", "angry",
    "hand", "finger", "fist", "grip", "clasp", "grab", "hold",
    "sword", "blade", "dagger", "weapon", "spear", "shield", "saber",
    "chain", "chains", "link", "lock", "wound", "blood", "cut", "slash",
    "bubble", "caption", "text", "shout", "scream", "cry", "tear", "tears"
]

# --------------------------------------------------------------- crop contract
# ONE definition of "does this segment actually crop?", shared by the exporter
# (render_segments.seg_html) and the editor preview (server.ensure_thumb /
# /segimg). Before this existed each side decided for itself: the exporter
# treated ANY crop_bbox_norm as a crop, while the editor ignored the box
# entirely — so the board showed the full panel and the video showed a
# sub-crop (Session 25 audit, 41 of 82 segments affected on Martial Genius).
FULL_FRAME_TOL = 0.01   # within 1% of every edge == the whole panel


def normalize_crop(crop_bbox_norm):
    """Validated [x0,y0,x1,y1] in [0,1], or None if absent/unusable.

    Returning None is the safe fallback everywhere: caller renders the full
    panel. Rejects wrong shapes, non-numerics, NaN, and inverted/empty boxes
    rather than letting them reach ffmpeg or the CSS viewport.
    """
    if not crop_bbox_norm:
        return None
    try:
        vals = [float(v) for v in crop_bbox_norm]
    except (TypeError, ValueError):
        return None
    if len(vals) != 4 or any(v != v for v in vals):     # v != v catches NaN
        return None
    x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in vals)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return [x0, y0, x1, y1]


# P3 (Session 25): a vision crop keeping less than this fraction of the panel
# is not framing, it is a magnified fragment — on Martial Genius Ch.1 seg 48
# kept 4.2% and seg 19 kept 8.7%, both stored at high confidence. Boxes under
# the floor auto-fall back to the whole panel rather than shipping silently.
# focus_confidence is NOT usable as the gate: the two worst boxes in the audit
# both carried 1.0.
CROP_MIN_AREA = 0.12


def crop_area(crop_bbox_norm):
    """Fraction of the panel the box keeps (0..1); 0.0 when unusable."""
    c = normalize_crop(crop_bbox_norm)
    if c is None:
        return 0.0
    x0, y0, x1, y1 = c
    return (x1 - x0) * (y1 - y0)


def is_full_frame_crop(crop_bbox_norm, tol=FULL_FRAME_TOL):
    """True when the box is the whole panel (within `tol` of every edge)."""
    c = normalize_crop(crop_bbox_norm)
    if c is None:
        return False
    x0, y0, x1, y1 = c
    return x0 <= tol and y0 <= tol and x1 >= 1.0 - tol and y1 >= 1.0 - tol


def crop_status(crop_bbox_norm):
    """Classify a stored box, for reviewer-facing labels and gating.

    "none"    — no box at all
    "invalid" — malformed / inverted / unusable
    "full"    — the whole panel
    "tiny"    — a real box, but below CROP_MIN_AREA (auto-falls back)
    "sub"     — a usable sub-crop
    """
    if not crop_bbox_norm:
        return "none"
    if normalize_crop(crop_bbox_norm) is None:
        return "invalid"
    if is_full_frame_crop(crop_bbox_norm):
        return "full"
    return "tiny" if crop_area(crop_bbox_norm) < CROP_MIN_AREA else "sub"


def effective_crop(crop_bbox_norm):
    """THE box actually rendered, or None meaning "use the whole panel".

    One function so preview and export can never diverge: invalid, full-frame
    and below-floor boxes all resolve to the full panel here, for both.
    """
    return (normalize_crop(crop_bbox_norm)
            if crop_status(crop_bbox_norm) == "sub" else None)


def is_sub_crop(crop_bbox_norm, tol=FULL_FRAME_TOL):
    """True only when the box MEANINGFULLY excludes part of the panel.

    A full-frame box ([0,0,1,1], or within `tol` of it) is not a crop — it is
    the whole panel wearing a crop key. Treating it as a crop is what silently
    disabled the TALL_AR scroll-pan path for tall panels (Session 25, P4).
    Now also false for below-floor boxes, so an unusable crop takes the same
    path as no crop at all.
    """
    return effective_crop(crop_bbox_norm) is not None


def png_size(path):
    """(width, height) from a PNG's IHDR — the ONE geometry source.

    segments.json width/height and descriptions.json width/height both drift
    from the real files (Session 25: page020_panel_003_shot_03 recorded
    900x2582, real file 900x811). Anything that decides framing or labels must
    measure the file.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None, None


def crop_rect_px(crop_bbox_norm, image_w, image_h):
    """Integer pixel rect (x, y, w, h) for ffmpeg's crop filter, or None.

    Same box the exporter's CSS viewport shows, expressed in pixels so the
    editor thumbnail can be cut with ffmpeg and match it.
    """
    c = normalize_crop(crop_bbox_norm)
    if c is None or not image_w or not image_h:
        return None
    x0, y0, x1, y1 = c
    x = int(round(x0 * image_w))
    y = int(round(y0 * image_h))
    w = max(1, min(int(round((x1 - x0) * image_w)), int(image_w) - x))
    h = max(1, min(int(round((y1 - y0) * image_h)), int(image_h) - y))
    return x, y, w, h


def get_crop_layout(crop_bbox_norm, image_w, image_h):
    """
    Given normalized crop coordinates [x0, y0, x1, y1] in range [0.0, 1.0],
    calculate visual layout scaling and positioning properties.
    """
    x0, y0, x1, y1 = crop_bbox_norm
    cw = x1 - x0
    ch = y1 - y0
    if cw <= 0: cw = 1.0
    if ch <= 0: ch = 1.0

    scale_w = 1.0 / cw * 100.0
    scale_h = 1.0 / ch * 100.0
    left = -x0 * 1.0 / cw * 100.0
    top = -y0 * 1.0 / ch * 100.0

    # Physical dimensions of the crop box inside the image
    crop_w_pixels = cw * (image_w or 1000)
    crop_h_pixels = ch * (image_h or 1000)
    crop_ar = crop_w_pixels / crop_h_pixels if crop_h_pixels > 0 else 1.0

    # Calculate W and H container dimensions to fit card constraints (max-width: 46%, max-height: 90%)
    max_w = 0.46 * 1920
    max_h = 0.90 * 1080
    if crop_ar > max_w / max_h:
        w = max_w
        h = max_w / crop_ar
    else:
        h = max_h
        w = max_h * crop_ar

    return {
        "scale_w": round(scale_w, 2),
        "scale_h": round(scale_h, 2),
        "left": round(left, 2),
        "top": round(top, 2),
        "w": round(w, 1),
        "h": round(h, 1),
        "ar": round(crop_ar, 4)
    }

def should_crop_close(text):
    text_lower = (text or "").lower()
    return any(w in text_lower for w in _DETAIL_KEYWORDS)

def _encode_image(path: str):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8"), mime

def _query_gemini_for_crops(img_path, beats_list, api_key):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    img_b64, mime = _encode_image(img_path)

    prompt = f"""You are a professional webtoon recap video editor.
Analyze the provided panel image and the list of narration beats assigned to it.
For each beat, identify the most relevant visual subject being described (e.g., a specific character's face, a hand, a weapon, a caption, or a specific action) and determine the crop bounding box that should fill the video frame.

Return ONLY a JSON array of objects (no markdown, no code fences), one for each input beat, containing:
- "beat_index": (int) the index of the beat
- "crop_bbox_norm": (array of 4 floats: [x0, y0, x1, y1] normalized to [0.0, 1.0] where [0.0, 0.0, 1.0, 1.0] is the full panel. x0, y0 is top-left, and x1, y1 is bottom-right)
- "framing_mode": (string) "close_up", "medium", or "full"
- "focus_reason": (string) brief rationale for the chosen crop
- "focus_confidence": (float between 0.0 and 1.0) your confidence score

Beats list:
{json.dumps(beats_list)}
"""

    def _call():
        return client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Part.from_bytes(data=base64.b64decode(img_b64), mime_type=mime),
                types.Part(text=prompt),
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json"
            ),
        )

    if usage:
        with usage.gate("gemini", 1, model=MODEL_NAME):
            resp = _call()
    else:
        resp = _call()

    text = resp.text.strip()
    # strip markdown code block wrapper if present
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return json.loads(text)

def plan_shots(shots, desc_path, crops_dir, api_key=None):
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")
    
    # 1. Apply fast local hybrid routing (saliency/keyword detection & sub-shot check)
    to_plan = []
    panel_to_shots = {}
    for s in shots:
        pid = s["panel_id"]
        panel_to_shots.setdefault(pid, []).append(s)

    for pid, pshots in panel_to_shots.items():
        img_path = os.path.join(crops_dir, f"{pid}.png")
        # If we can't find the image or API key is missing, fall back immediately
        if not os.path.exists(img_path) or not api_key:
            for s in pshots:
                s["crop_bbox_norm"] = [0.0, 0.0, 1.0, 1.0]
                s["focus_source"] = "fallback_full" if api_key else "no_api_key_fallback"
                s["focus_reason"] = "default full panel"
                s["focus_confidence"] = 1.0
            continue

        # For each shot, check if it needs detail cropping
        needs_gemini = False
        for s in pshots:
            if should_crop_close(s["beat_text"]):
                needs_gemini = True
            else:
                # Local fallbacks
                if "_shot_" in pid:
                    s["crop_bbox_norm"] = [0.0, 0.0, 1.0, 1.0]
                    s["focus_source"] = "subshot"
                    s["focus_reason"] = "existing sub-shot window"
                    s["focus_confidence"] = 1.0
                else:
                    s["crop_bbox_norm"] = [0.0, 0.0, 1.0, 1.0]
                    s["focus_source"] = "fallback_full"
                    s["focus_reason"] = "general narration (no detail keywords)"
                    s["focus_confidence"] = 1.0

        if needs_gemini:
            to_plan.append((pid, img_path, pshots))

    # 2. Query Gemini in parallel for the detail-oriented panels
    if to_plan:
        proj_dir = os.path.dirname(desc_path)
        cache_path = os.path.join(proj_dir, "framing_cache.json")
        cache = {}
        if os.path.exists(cache_path):
            try:
                cache = json.load(open(cache_path))
                print(f"Loaded {len(cache)} cached panel crop plans.")
            except Exception:
                pass

        # Filter out cached ones
        query_list = []
        for pid, img_path, pshots in to_plan:
            if pid in cache:
                cached_beats = cache[pid]
                for s in pshots:
                    b_idx_str = str(s["index"])
                    if b_idx_str in cached_beats:
                        cdata = cached_beats[b_idx_str]
                        s["crop_bbox_norm"] = cdata.get("crop_bbox_norm", [0.0, 0.0, 1.0, 1.0])
                        s["focus_source"] = cdata.get("focus_source", "vision")
                        s["focus_reason"] = cdata.get("focus_reason", "cached crop")
                        s["focus_confidence"] = cdata.get("focus_confidence", 1.0)
                continue
            query_list.append((pid, img_path, pshots))

        if query_list:
            print(f"Planning crops for {len(query_list)} detail-heavy panels using Gemini...")
            
            def process_panel(item):
                pid, img_path, pshots = item
                # Only send the beats that actually need detail cropping to keep context clean
                beats_list = [{"index": s["index"], "text": s["beat_text"]} for s in pshots]
                try:
                    results = _query_gemini_for_crops(img_path, beats_list, api_key)
                    mapped = {r["beat_index"]: r for r in results if "beat_index" in r}
                    panel_cache = {}
                    for s in pshots:
                        r = mapped.get(s["index"])
                        if r and "crop_bbox_norm" in r:
                            s["crop_bbox_norm"] = r["crop_bbox_norm"]
                            s["focus_source"] = "vision"
                            s["focus_reason"] = r.get("focus_reason", "AI planned")
                            s["focus_confidence"] = r.get("focus_confidence", 0.9)
                        else:
                            # fallback within query
                            s["crop_bbox_norm"] = [0.0, 0.0, 1.0, 1.0]
                            s["focus_source"] = "fallback_full"
                            s["focus_reason"] = "AI missed beat crop request"
                            s["focus_confidence"] = 0.5
                        
                        panel_cache[str(s["index"])] = {
                            "crop_bbox_norm": s["crop_bbox_norm"],
                            "focus_source": s["focus_source"],
                            "focus_reason": s["focus_reason"],
                            "focus_confidence": s["focus_confidence"]
                        }
                    return pid, panel_cache
                except Exception as e:
                    print(f"Error planning crops for panel {pid}: {e}")
                    panel_cache = {}
                    for s in pshots:
                        s["crop_bbox_norm"] = [0.0, 0.0, 1.0, 1.0]
                        s["focus_source"] = "fallback_full"
                        s["focus_reason"] = f"Error: {e}"
                        s["focus_confidence"] = 0.0
                        panel_cache[str(s["index"])] = {
                            "crop_bbox_norm": s["crop_bbox_norm"],
                            "focus_source": s["focus_source"],
                            "focus_reason": s["focus_reason"],
                            "focus_confidence": s["focus_confidence"]
                        }
                    return pid, panel_cache

            with ThreadPoolExecutor(max_workers=5) as executor:
                completed = list(executor.map(process_panel, query_list))

            # Update cache and save
            for pid, panel_cache in completed:
                cache[pid] = panel_cache
            try:
                with open(cache_path, "w") as f:
                    json.dump(cache, f, indent=2)
            except Exception:
                pass

    return shots
