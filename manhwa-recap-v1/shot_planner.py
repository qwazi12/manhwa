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
