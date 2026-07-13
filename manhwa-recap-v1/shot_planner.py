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
- "crop_bbox": (array of 4 ints: [ymin, xmin, ymax, xmax] normalized to [0, 1000] where [0, 0, 1000, 1000] is the full panel)
  - ymin and ymax must be between 0 and 1000; xmin and xmax must be between 0 and 1000.
  - The crop should focus closely on the subject (e.g. face, hand, action) if specifically mentioned, so it dominates the frame.
- "framing_mode": (string) "close_up", "medium", or "full"
- "focus_reason": (string) brief rationale for the chosen crop

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
    if not api_key:
        print("WARNING: GEMINI_API_KEY not set. Shot planner will use fallback full crops.")
        for s in shots:
            s["crop_bbox"] = [0, 0, 1000, 1000]
            s["framing_mode"] = "full"
            s["focus_reason"] = "fallback"
            s["scale_w"] = 100.0
            s["scale_h"] = 100.0
            s["left"] = 0.0
            s["top"] = 0.0
            s["crop_ar"] = round((s.get("width") or 1000) / (s.get("height") or 1000), 4)
        return shots

    # Group shots by panel_id
    panel_to_shots = {}
    for s in shots:
        pid = s["panel_id"]
        panel_to_shots.setdefault(pid, []).append(s)

    # Load cache if it exists in project dir
    proj_dir = os.path.dirname(desc_path)
    cache_path = os.path.join(proj_dir, "framing_cache.json")
    cache = {}
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
            print(f"Loaded {len(cache)} cached panel crop plans.")
        except Exception:
            pass

    # Find panels that need planning
    to_plan = []
    for pid, pshots in panel_to_shots.items():
        if pid in cache:
            # Apply cached crops
            cached_beats = cache[pid]
            for s in pshots:
                b_idx_str = str(s["index"])
                if b_idx_str in cached_beats:
                    cdata = cached_beats[b_idx_str]
                    s["crop_bbox"] = cdata.get("crop_bbox", [0, 0, 1000, 1000])
                    s["framing_mode"] = cdata.get("framing_mode", "full")
                    s["focus_reason"] = cdata.get("focus_reason", "cached")
            continue

        img_path = os.path.join(crops_dir, f"{pid}.png")
        if not os.path.exists(img_path):
            # Fallback
            for s in pshots:
                s["crop_bbox"] = [0, 0, 1000, 1000]
                s["framing_mode"] = "full"
                s["focus_reason"] = "missing image"
            continue
        to_plan.append((pid, img_path, pshots))

    if to_plan:
        print(f"Planning crops for {len(to_plan)} unique panels...")
        
        def process_panel(item):
            pid, img_path, pshots = item
            beats_list = [{"index": s["index"], "text": s["beat_text"]} for s in pshots]
            try:
                results = _query_gemini_for_crops(img_path, beats_list, api_key)
                # Map by beat_index
                mapped = {r["beat_index"]: r for r in results if "beat_index" in r}
                panel_cache = {}
                for s in pshots:
                    r = mapped.get(s["index"])
                    if r and "crop_bbox" in r:
                        s["crop_bbox"] = r["crop_bbox"]
                        s["framing_mode"] = r.get("framing_mode", "full")
                        s["focus_reason"] = r.get("focus_reason", "AI planned")
                    else:
                        s["crop_bbox"] = [0, 0, 1000, 1000]
                        s["framing_mode"] = "full"
                        s["focus_reason"] = "AI missed beat"
                    
                    panel_cache[str(s["index"])] = {
                        "crop_bbox": s["crop_bbox"],
                        "framing_mode": s["framing_mode"],
                        "focus_reason": s["focus_reason"]
                    }
                return pid, panel_cache
            except Exception as e:
                print(f"Error planning crops for panel {pid}: {e}")
                # Fallback
                panel_cache = {}
                for s in pshots:
                    s["crop_bbox"] = [0, 0, 1000, 1000]
                    s["framing_mode"] = "full"
                    s["focus_reason"] = f"Error: {e}"
                    panel_cache[str(s["index"])] = {
                        "crop_bbox": s["crop_bbox"],
                        "framing_mode": s["framing_mode"],
                        "focus_reason": s["focus_reason"]
                    }
                return pid, panel_cache

        # Run in parallel to avoid long sequential delays
        with ThreadPoolExecutor(max_workers=5) as executor:
            completed = list(executor.map(process_panel, to_plan))

        # Update cache
        for pid, panel_cache in completed:
            cache[pid] = panel_cache

        # Save cache
        try:
            with open(cache_path, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass

    # Post-process: Calculate CSS scaling & positions
    for s in shots:
        crop_bbox = s.get("crop_bbox", [0, 0, 1000, 1000])
        ymin, xmin, ymax, xmax = [float(x) for x in crop_bbox]
        cw = xmax - xmin
        ch = ymax - ymin
        if cw <= 0: cw = 1000.0
        if ch <= 0: ch = 1000.0

        scale_w = round(1000.0 / cw * 100.0, 2)
        scale_h = round(1000.0 / ch * 100.0, 2)
        left = round(-xmin * 100.0 / cw, 2)
        top = round(-ymin * 100.0 / ch, 2)

        s["scale_w"] = scale_w
        s["scale_h"] = scale_h
        s["left"] = left
        s["top"] = top

        # Aspect ratio of the crop box
        image_w = s.get("width") or 1000
        image_h = s.get("height") or 1000
        crop_w_pixels = (cw / 1000.0) * image_w
        crop_h_pixels = (ch / 1000.0) * image_h
        s["crop_ar"] = round(crop_w_pixels / crop_h_pixels, 4) if crop_h_pixels > 0 else 1.0

    return shots
