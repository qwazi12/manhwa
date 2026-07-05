"""
The matcher — assigns the right panel to each narration beat, then builds a
gap-free timeline.

Two jobs, one module:

1. SEMANTIC MATCHING (forward-only)
   For each sentence-beat, score it against every panel's
   visual_description + ocr_text and pick the best panel — but only from the
   current position forward. A panel can HOLD across several beats or the
   assignment can ADVANCE, but it never jumps backward, so the story stays
   in order. This is the rule that keeps the video from scrambling.

2. GAP-FREE TIMELINE (exact tiling)
   Beat durations come from real audio length (from the TTS stage). Each
   shot's end is snapped to exactly the next shot's start, so shots tile the
   full runtime with no gaps, no overlaps, no freezes. The last shot ends at
   the exact total audio duration.

Scoring is embedding-based when a model is available (best), with a
deterministic lexical fallback (TF-IDF-ish token overlap) so the tool runs
with zero external services for testing.

Output: beatsheet.json — a list of shots render.py (or a Hyperframes
exporter) consumes:
    {
      "index", "start", "end", "dur",
      "beat_text", "panel_id", "panel_file",
      "width", "height", "score", "held"   # held=True if same panel as prev beat
    }
"""

import hashlib
import json
import math
import os
import re
from collections import Counter

# ------------------------------------------------------------ tunables
# How far forward the matcher may look for a better panel on a single beat.
# A stall that compounds needs runway to find the correct panel again, so this
# is set generously now that embedding scores are meaningful.
LOOKAHEAD = 8
# Penalty applied to advancing to a NEW panel, so it prefers holding unless a
# later panel clearly matches better. Higher = holds panels longer.
# Calibrated for Gemini-embedding cosine scores (~0.6-0.9 range). With lexical
# fallback scores (~0.0-0.1) this value is far too large and the matcher will
# stall — the embedding scorer is the intended path.
ADVANCE_PENALTY = 0.015
# Weight of OCR/dialogue match vs. visual-description match (lexical fallback).
# OCR is the highest-signal field (exact character names and dialogue are
# almost perfectly discriminative), so it gets a majority weight.
OCR_WEIGHT = 0.55
DESC_WEIGHT = 0.45
# Anti-stall: if the same panel has been held for this many consecutive beats,
# the ADVANCE_PENALTY is reduced toward zero so the matcher becomes willing
# to try the next panel. Forward-only rule is preserved.
MAX_HELD = 3

# ------------------------------------------------------ Gemini embeddings
# Real semantic scores come from the Gemini embeddings API (one call per text
# string). Vectors are cached to disk keyed by text hash so re-runs on the same
# strings don't re-hit the API.
GEMINI_EMBED_MODEL = "gemini-embedding-2"
_EMBED_TASK = "SEMANTIC_SIMILARITY"
_EMBED_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "embeddings_cache.json")

_STOP = set("a an the of to in on at and or but with was were is are be been "
            "he she it his her him they them their you your i me my we our as "
            "that this these those for from by not no had have has did do down "
            "up out into over under again then than so if when who what".split())


# ------------------------------------------------------- lexical scoring
def _tokens(text: str):
    return [t for t in re.findall(r"[a-z']+", (text or "").lower())
            if t not in _STOP and len(t) > 1]


def _lexical_sim(a: str, b: str) -> float:
    """Cosine similarity over token-count vectors. Deterministic, no deps."""
    ca, cb = Counter(_tokens(a)), Counter(_tokens(b))
    if not ca or not cb:
        return 0.0
    common = set(ca) & set(cb)
    dot = sum(ca[t] * cb[t] for t in common)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb) if na and nb else 0.0


# ------------------------------------------------- optional embeddings
def _try_embed(texts, model_name):
    """Return list of vectors via sentence-transformers, or None if absent."""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer(model_name)
        vecs = model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs)
    except Exception:
        return None


def _load_embed_cache():
    try:
        with open(_EMBED_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_embed_cache(cache):
    tmp = _EMBED_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, _EMBED_CACHE_PATH)


def _text_key(text, model_name):
    payload = f"{model_name}\x00{_EMBED_TASK}\x00{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _gemini_embed(texts, model_name=GEMINI_EMBED_MODEL):
    """Embed each text via the Gemini embeddings API (one call per string).

    Same (texts, model_name) -> np.ndarray|None interface as _try_embed, so
    build_scorer can use it transparently. Returns an L2-normalized array of
    vectors, or None if the SDK / GEMINI_API_KEY is unavailable (caller then
    falls back). Vectors are cached to disk keyed by a hash of the text so
    repeat runs on the same strings make zero API calls.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai import types
        import numpy as np
    except Exception:
        return None

    cache = _load_embed_cache()
    client = None
    dirty = False
    vectors = []
    try:
        for t in texts:
            key = _text_key(t, model_name)
            cached = cache.get(key)
            if cached is not None:
                vectors.append(cached)
                continue
            if client is None:
                client = genai.Client(api_key=api_key)
            resp = client.models.embed_content(
                model=model_name,
                contents=t if t else " ",
                config=types.EmbedContentConfig(task_type=_EMBED_TASK),
            )
            vec = list(resp.embeddings[0].values)
            cache[key] = vec
            vectors.append(vec)
            dirty = True
    except Exception:
        # Partial progress is still worth persisting; the finally block saves.
        return None
    finally:
        if dirty:
            _save_embed_cache(cache)

    arr = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def build_scorer(beats, panels, embed_model):
    """
    Returns score(beat_idx, panel_idx) -> float in [0,1].
    Uses embeddings if available (cosine of beat vs panel-desc+ocr),
    else lexical similarity blending OCR and description.
    """
    beat_texts = [b["text"] for b in beats]
    panel_texts = [
        f"{p.get('visual_description','')} {p.get('ocr_text','')}".strip()
        for p in panels
    ]

    # Prefer Gemini embeddings (real semantic scores). Fall back to
    # sentence-transformers only if an embed_model is explicitly requested,
    # then to lexical token-overlap as a last resort.
    emb_b = _gemini_embed(beat_texts)
    emb_p = _gemini_embed(panel_texts) if emb_b is not None else None
    method_name = "gemini-embeddings"
    if emb_b is None or emb_p is None:
        emb_b = _try_embed(beat_texts, embed_model) if embed_model else None
        emb_p = _try_embed(panel_texts, embed_model) if embed_model else None
        method_name = "embeddings"

    if emb_b is not None and emb_p is not None:
        import numpy as np
        sim = emb_b @ emb_p.T  # both normalized -> cosine
        sim = (sim + 1.0) / 2.0  # map to [0,1]

        def score(bi, pi):
            return float(sim[bi, pi])
        return score, method_name

    # lexical fallback: blend description-match and ocr-match
    def score(bi, pi):
        bt = beats[bi]["text"]
        d = _lexical_sim(bt, panels[pi].get("visual_description", ""))
        o = _lexical_sim(bt, panels[pi].get("ocr_text", ""))
        return DESC_WEIGHT * d + OCR_WEIGHT * o
    return score, "lexical"


# --------------------------------------------------- forward-only match
def match_beats_to_panels(beats, panels, embed_model=None):
    """
    Assign one panel per beat, monotonically non-decreasing in panel order.
    Greedy with bounded look-ahead: for each beat, consider staying on the
    current panel or advancing up to LOOKAHEAD panels forward; pick the best
    score, applying ADVANCE_PENALTY per panel advanced so it holds unless a
    forward panel is clearly better.

    Anti-stall guard: if the same panel has held for MAX_HELD consecutive beats,
    the advance penalty is reduced to near-zero for that decision so the matcher
    is forced to consider moving forward. Forward-only rule stays intact.
    """
    score, method = build_scorer(beats, panels, embed_model)
    n_p = len(panels)
    assignments = []
    cur = 0       # current panel index; never decreases
    held_run = 0  # consecutive beats on the same panel

    for bi in range(len(beats)):
        # Anti-stall: scale down advance penalty when we've held too long
        stall_factor = max(0.0, 1.0 - max(0, held_run - MAX_HELD) * 0.25)
        effective_penalty = ADVANCE_PENALTY * stall_factor

        best_pi, best_val = cur, score(bi, cur)
        # consider advancing up to LOOKAHEAD panels forward
        for step in range(1, LOOKAHEAD + 1):
            pi = cur + step
            if pi >= n_p:
                break
            val = score(bi, pi) - effective_penalty * step
            if val > best_val:
                best_val, best_pi = val, pi

        # never exhaust the panel list before beats run out:
        # only advance one step at a time when panels are scarce
        beats_left = len(beats) - bi
        panels_left = n_p - cur
        if panels_left <= beats_left and best_pi > cur:
            best_pi = min(best_pi, cur + 1)

        advanced = best_pi > cur
        assignments.append({
            "beat_index": bi,
            "panel_index": best_pi,
            "score": round(score(bi, best_pi), 4),
            "held": not advanced,
            "held_run": held_run,
        })

        # update stall counter
        if advanced:
            held_run = 0
        else:
            held_run += 1
        cur = best_pi

    return assignments, method


# ------------------------------------------------- gap-free timeline
def build_timeline(beats, panels, assignments):
    """
    Produce shots that tile the full audio duration exactly.

    Each beat already has real 'start'/'end' from the TTS stage. We emit one
    shot per beat, then snap every shot's end to the next shot's start so
    there are no gaps or overlaps. The final shot ends at the last beat's end
    (the true total audio duration).
    """
    shots = []
    for a in assignments:
        b = beats[a["beat_index"]]
        p = panels[a["panel_index"]]
        shots.append({
            "index": a["beat_index"],
            "start": float(b["start"]),
            "end": float(b["end"]),
            "beat_text": b["text"],
            "panel_id": p.get("panel_id"),
            "panel_file": p.get("file"),
            "width": p.get("width"),
            "height": p.get("height"),
            "score": a["score"],
            "held": a["held"],
        })

    # exact tiling: snap each shot end to the next shot start
    for i in range(len(shots) - 1):
        shots[i]["end"] = shots[i + 1]["start"]
    for s in shots:
        s["dur"] = round(s["end"] - s["start"], 3)

    # sanity: no gaps/overlaps
    for i in range(len(shots) - 1):
        assert abs(shots[i]["end"] - shots[i + 1]["start"]) < 1e-6, "gap detected"

    return shots


def run(beats_path, descriptions_path, out_path, embed_model=None):
    beats = json.load(open(beats_path))
    panels = json.load(open(descriptions_path))
    # Resolve relative file paths to absolute, rooted at the descriptions.json dir
    desc_dir = os.path.dirname(os.path.abspath(descriptions_path))
    for p in panels:
        if p.get("file") and not os.path.isabs(p["file"]):
            p["file"] = os.path.join(desc_dir, p["file"])
    # only use panels that described successfully and have real size
    panels = [p for p in panels
              if p.get("ok", True) and p.get("width") and p.get("height")]
    if not panels:
        raise SystemExit("No usable panels in descriptions.json")
    if not beats:
        raise SystemExit("No beats provided")

    assignments, method = match_beats_to_panels(beats, panels, embed_model)
    shots = build_timeline(beats, panels, assignments)

    json.dump(shots, open(out_path, "w"), indent=2, ensure_ascii=False)

    total = shots[-1]["end"] if shots else 0
    held = sum(1 for s in shots if s["held"])
    uniq = len({s["panel_id"] for s in shots})
    print(f"Matched {len(beats)} beats to {uniq} distinct panels "
          f"(method: {method}).")
    print(f"{held} beats held the previous panel; {len(shots)-held} advanced.")
    print(f"Timeline: {len(shots)} shots tiling {total:.1f}s with no gaps.")
    print(f"Wrote {out_path}")
    return shots
