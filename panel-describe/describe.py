"""
Panel description pass — the "read what's in the image" stage.

For each clean panel image, produce:
    {
      "panel_id": str,          # derived from filename
      "file": str,              # filename
      "width": int,
      "height": int,
      "bbox": [0, 0, w, h],     # panel's own frame (full image here)
      "ocr_text": str,          # dialogue / bubble / caption text in the panel
      "visual_description": str,# what is happening, who's in frame, mood, action
      "source": str,            # which engine produced this ("gemini" | "tesseract" | "none")
      "ok": bool                # whether description succeeded
    }

NO MATCHING LOGIC. This only describes panels. Matching script beats to
these descriptions is a later, separate stage.

Design choice (from research): modern vision-language models do OCR AND
description in a single call, so we don't run a separate OCR engine when
Gemini is available — one request returns both fields as JSON. Gemini 3.5
Flash is the default (gemini-3.1-flash-lite is the cheaper alternative);
it handles the English-translated bubbles in these manhwa panels without
the Japanese-specialized manga-OCR stack.

If no GEMINI_API_KEY is set, the tool falls back to Tesseract for OCR-only
(visual_description left empty) so it still runs for testing — but the
whole point of this stage is the vision description, so a key is expected
for real use.
"""

import base64
import json
import os
import sys
import mimetypes
from PIL import Image

# Cost/abuse guardrails (review_ui/usage.py) — optional: if the review UI isn't
# alongside this checkout (e.g. standalone testing), guardrails are a no-op.
_REVIEW_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "manhwa-recap-v1", "review_ui")
if os.path.isdir(_REVIEW_UI) and _REVIEW_UI not in sys.path:
    sys.path.insert(0, _REVIEW_UI)
try:
    import usage
except ImportError:
    usage = None

# ---- the instruction given to the vision model, per panel ----
VISION_PROMPT = """You are analyzing a single panel from a manhwa/webtoon chapter.
Return ONLY a JSON object (no markdown, no code fences) with exactly these keys:

{
  "ocr_text": "<ALL readable text in speech bubbles, captions, or sound effects, joined with ' / '. Transcribe EXACTLY as written — names, curses, sound effects all matter for matching. Empty string if none.>",
  "visual_description": "<Lead with the single concrete action happening in this exact panel (e.g. 'boy tumbling down a rocky cliff face', 'character collapsed on ground clutching injured leg', 'figure standing upright facing a row of cloaked enemies', 'extreme close-up of one wide shocked eye'). Then: who is in frame (name if visible), setting, shot type. MAX 50 words. Use specific action verbs — NEVER start with vague openers like 'a scene showing', 'a panel depicting', or 'the image features'.>"
}

Rules:
- visual_description MUST start with the action verb phrase, not the subject. Example: 'Falling down a steep rocky slope, the dark-haired boy...' not 'A boy falls...'
- OCR text is the highest-priority match signal — transcribe every word, name, and sound effect exactly.
- If a bubble is only punctuation like '!!!' or 'ACK!!', include it as-is.
- Describe only what is visibly in THIS panel. Do not invent plot.
- Keep visual_description under 50 words."""


def _panel_id_from_name(fname: str) -> str:
    return os.path.splitext(os.path.basename(fname))[0]


def _encode_image(path: str):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8"), mime


# ------------------------------------------------------------------ Gemini

def _call_interactions_api(api_key: str, model: str, img_b64: str, mime: str, certifi_path: str | None = None) -> str:
    """Call the new Interactions API (/v1beta/interactions) with a multimodal input.
    Returns raw response text."""
    import urllib.request, urllib.error, ssl

    url = "https://generativelanguage.googleapis.com/v1beta/interactions"

    # Build multimodal input using parts
    body = json.dumps({
        "model": model,
        "input": [
            {"inlineData": {"mimeType": mime, "data": img_b64}},
            {"text": VISION_PROMPT},
        ]
    }).encode("utf-8")

    ctx = None
    if certifi_path:
        ctx = ssl.create_default_context(cafile=certifi_path)

    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
        method="POST")

    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        data = json.loads(resp.read())

    # Extract text from interactions response
    for step in data.get("steps", []):
        model_out = step.get("modelOutput") or step.get("model_output")
        if model_out:
            for part in model_out.get("content", []):
                if part.get("text"):
                    return part["text"].get("text", "") if isinstance(part["text"], dict) else part["text"]
    # Fallback: check output_text
    if data.get("output_text"):
        return data["output_text"]
    if data.get("outputText"):
        return data["outputText"]
    raise ValueError(f"Unexpected interactions response structure: {list(data.keys())}")


def describe_with_gemini(path: str, api_key: str, model: str):
    """Single call: returns (ocr_text, visual_description). Raises on failure.

    Routes to the Interactions API for new auth-key format (AQ.) models like
    gemini-3.5-flash, and falls back to the legacy generateContent API for
    older models / standard API keys.
    """
    img_b64, mime = _encode_image(path)

    # Detect auth key format — new Google AI Studio auth keys start with "AQ."
    use_interactions = api_key.startswith("AQ.")

    if use_interactions:
        # New Interactions API path (gemini-3.5-flash + auth keys)
        try:
            import certifi
            certifi_path = certifi.where()
        except ImportError:
            certifi_path = None

        def _call():
            raw = _call_interactions_api(api_key, model, img_b64, mime, certifi_path)
            return raw

        if usage:
            with usage.gate("gemini", 1, model=model):
                raw = _call()
        else:
            raw = _call()
    else:
        # Legacy generateContent API path (standard AIzaSy API keys)
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        def _call():
            return client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=base64.b64decode(img_b64), mime_type=mime),
                    types.Part(text=VISION_PROMPT),
                ],
                config=types.GenerateContentConfig(temperature=0.0),
            )

        if usage:
            with usage.gate("gemini", 1, model=model):
                resp = _call()
        else:
            resp = _call()
        raw = (resp.text or "").strip()

    # strip accidental code fences
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if "```" in raw[3:] else raw.strip("`")
        raw = raw.replace("json", "", 1).strip() if raw.lstrip().startswith("json") else raw
    data = json.loads(raw)
    return data.get("ocr_text", "").strip(), data.get("visual_description", "").strip()


# --------------------------------------------------------------- Tesseract

def describe_with_tesseract(path: str):
    """OCR-only fallback. visual_description stays empty."""
    import pytesseract
    txt = pytesseract.image_to_string(Image.open(path)).strip()
    # collapse whitespace/newlines into ' / ' segments
    segs = [s.strip() for s in txt.splitlines() if s.strip()]
    return " / ".join(segs), ""


# ------------------------------------------------------------------ driver

def describe_panel(path: str, api_key: str | None, model: str):
    w, h = Image.open(path).size
    rec = {
        "panel_id": _panel_id_from_name(path),
        "file": os.path.basename(path),
        "width": w, "height": h,
        "bbox": [0, 0, w, h],
        "ocr_text": "", "visual_description": "",
        "source": "none", "ok": False,
    }
    try:
        if api_key:
            ocr, desc = describe_with_gemini(path, api_key, model)
            rec.update(ocr_text=ocr, visual_description=desc, source="gemini", ok=True)
        else:
            ocr, desc = describe_with_tesseract(path)
            rec.update(ocr_text=ocr, visual_description=desc, source="tesseract",
                       ok=bool(ocr))
    except Exception as e:
        # A guardrail cap breach must HALT the whole run, not be swallowed as
        # one panel's failure (that would just re-trip on every next panel
        # while silently marking the whole chapter as "described").
        if usage and isinstance(e, usage.UsageCapExceeded):
            raise
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec
