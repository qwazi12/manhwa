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
import time
import re
import sys
from collections import Counter

# Cost/abuse guardrails (review_ui/usage.py) — optional no-op if unavailable.
_REVIEW_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_ui")
if os.path.isdir(_REVIEW_UI) and _REVIEW_UI not in sys.path:
    sys.path.insert(0, _REVIEW_UI)
try:
    import usage as _usage
except ImportError:
    _usage = None

# ------------------------------------------------------------ tunables
# The matcher aligns the beat sequence to the panel sequence with a GLOBAL
# monotonic dynamic-programming pass (not greedy) — see align_beats_dp. The
# only knob is HOLD_PENALTY: the per-beat cost of keeping the same panel as the
# previous beat. Advancing to a new (forward) panel is free. Higher = fewer,
# shorter holds / more variety; lower = longer holds. Because the DP optimizes
# the whole path at once, small local errors can't compound the way the old
# greedy forward-only matcher's did (that caused the "drifts worse over time"
# desync and 40s+ stalls). Scores are Gemini-embedding cosine in ~0.6-0.9, so a
# 0.02 hold penalty gently favors progression while still allowing a genuine
# 2-3 beat dramatic hold when one panel clearly out-matches the alternatives.
HOLD_PENALTY = 0.06
# Safety rail: strongly discourage holding a panel beyond MAX_HOLD consecutive
# beats (the old greedy matcher parked one panel for 10 beats / 46s). This is a
# steep OVER-cap penalty, NOT a hard wall: holding past the cap is very costly
# but still finite, so when the ONLY forward option is a junk/blank panel (e.g.
# the finale, where the last page is mostly "SKY CORPORATION" title-card
# fragments), the DP keeps holding the last REAL panel instead of advancing onto
# a blank one. Normal stretches never hit this because a real forward panel is
# cheaper than the over-cap penalty.
MAX_HOLD = 5
OVER_HOLD_PENALTY = 0.5   # per-beat cost of holding beyond MAX_HOLD (steep, finite)
# Weight of OCR/dialogue match vs. visual-description match (lexical fallback).
# OCR is the highest-signal field (exact character names and dialogue are
# almost perfectly discriminative), so it gets a majority weight.
OCR_WEIGHT = 0.55
DESC_WEIGHT = 0.45

# ------------------------------------------------------ Gemini embeddings
# Real semantic scores come from the Gemini embeddings API (one call per text
# string). Vectors are cached to disk keyed by text hash so re-runs on the same
# strings don't re-hit the API.
GEMINI_EMBED_MODEL = "gemini-embedding-2"
_EMBED_TASK = "SEMANTIC_SIMILARITY"
_EMBED_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "embeddings_cache.json")

# ------------------------------------------------ junk / SFX-panel filter
# The panel splitter emits content-free fragments: an empty or partial speech
# bubble, a stray gutter line, a blank panel with one black stroke, a floating
# dialogue bubble cut off from its scene, or a bare text/SFX panel (a big
# "FUCK!" or "SERIAL NUMBER" logo). These depict no subject and no scene, so no
# narration beat should ever land on them — yet as candidates they act as
# semantic "parking magnets" a forward-only matcher stalls on. A bare expletive
# in particular embeds as generic anger/distress and out-scores correct scene
# panels across many beats (this is what corrupted beats 5-14 before filtering).
#
# Rule (POSITIVE-KEEP): keep a panel only if its description names a real
# SUBJECT (a person/body part/animal/depicted object) or a real SCENE
# (setting/environment). Everything else is dropped. This is deliberately the
# inverse of the old blocklist: instead of enumerating every junk phrase (which
# always leaves gaps — "curved bracket", "partial abstract shape", "chevron"
# and friends slipped through), a panel must EARN its place by depicting
# something. A content-free fragment (empty bubble, stray line, bare SFX/logo
# word, abstract graphic) names neither a subject nor a scene, so it's dropped
# automatically without needing a pattern for it. The fall shot (pure "EUAAACK"
# SFX but a rich tumbling-down-a-cliff visual) survives because it names a
# subject + scene. Note: generic "background" alone is NOT a scene token (a
# "plain white background" is a blank), so lists below avoid it.
_KEEP_SUBJECT = re.compile(
    r"\b(person|figure|figures|man|men|boy|woman|women|child|children|character|"
    r"hooded|cloaked|masked|warrior|someone|soldier|guard|hand|hands|"
    r"face|faces|eye|eyes|head|hair|body|arm|arms|leg|legs|torso|chest|neck|"
    r"finger|fingers|crowd|rider|horse|monk|girl|king|prince|elder|assassin|"
    r"killer|opponent|enemy|victim|silhouette|blade|sword|dagger|knife|weapon|"
    r"spear|syringe|device|kneeling|standing|lying|crouching|sitting|running|"
    r"lunging|falling|collapsing|collapsed|wounded|clenched)\b", re.I)
_KEEP_SCENE = re.compile(
    r"\b(moon|moonlit|sky|forest|ground|floor|room|wall|tree|trees|mountain|"
    r"mountains|night|cliff|slope|dirt|field|building|street|palace|castle|"
    r"water|fire|flame|lightning|explosion|blast|snow|rain|smoke|dust|debris|"
    r"fog|mist|environment|rocky|desolate|indoor|outdoor|landscape|forest floor|"
    r"battlefield|clearing|woods|nighttime|moonlight|"
    # energy / effect moments (a light-burst or explosion is a real story beat,
    # not a fragment) — keep these even without a person in frame
    r"light|glow|glowing|burst|bursting|energy|aura|spark|sparks|beam|flash|"
    r"radiant|shockwave|reddish-orange|fireball)\b", re.I)
# Override: descriptions that self-declare as abstract / non-representational.
# These beat the positive-keep because a keep-word can appear inside a NEGATION
# ("...rather than a depiction of a physical object or character") — the panel
# describes what it is NOT. Any of these phrases means "this is not a real
# scene", so drop regardless of stray keep-words.
_ABSTRACT_OVERRIDE = re.compile(
    r"(abstract shape|abstract graphic|decorative|symbolic element|"
    r"greater-than|less-than|chevron|a bracket|curved bracket|geometric shape|"
    r"non-representational|rather than a depiction|not a depiction|"
    r"stylized,? (curved|black|abstract)|graphic element|"
    # text/bubble fragments: a panel that is PRIMARILY a speech/thought bubble
    # or rendered text, not a depicted scene (the AI often opens with these)
    r"(half|part|edge|portion|outline) of (a |an )?(speech|thought)?\s?bubble|"
    r"partial (speech|thought) bubble|empty (speech|thought) bubble|"
    r"a (white |jagged )?(speech|thought) bubble (with|containing|is shown)|"
    r"displaying (a |the )?(text|word|speech|thought)|containing the text|"
    # blank / stray-line fragments (an over-split gutter sliver, not a scene):
    # "blank white panel", "a thin/thick black line", a line "running/curving/
    # arching along/across an edge". These render as near-white cards.
    r"blank (white )?panel|mostly blank|(thin|thick) black (vertical|horizontal|"
    r"curved)? ?line|black (vertical|horizontal) line|single (thin|thick)|"
    r"(running|curving|curves|arches|arcing|extending) (down|along|across|"
    r"downwards|upwards)[^.]{0,28}(edge|frame|panel|corner|background)|"
    r"a thick black arc|stark white background)", re.I)


# Promo / scanlation-credits hard block (B2). Unlike the positive-keep rule
# this applies to EVERY regime, including model-curated vision beats: the
# Dungeon Odyssey ch.1 recruitment banner passed the keep-rule because it
# depicts characters, yet it is an aggregator ad, not story. Strong promo
# tokens only — a bare watermarked domain on an otherwise-real cover panel
# must NOT block, so the domain alone is not in this list.
_PROMO_BLOCK = re.compile(
    r"(recruit(ment|ing)?|we('re| are) (hiring|recruiting)|"
    r"join (our|us|the) (discord|team|server)|discord\.gg|"
    r"patreon|ko-?fi\b|paypal|donat(e|ion)|"
    r"(translator|proofreader|typesetter|cleaner|redrawer)s?\s*[:\-]|"
    r"scan(lation)? (group|team)|asura ?scans? (is|team|recruit)|"
    r"read (more |our )?(at|on) |official (site|website|discord))", re.I)


# Minimum crop dimensions for a vision beat to count as real content.
_MIN_BEAT_PX = 40


def junk_reason(panel):
    """Human-readable WHY for a junk panel (storyboard left-out rows).
    Returns None for non-junk panels. Mirrors is_junk_panel's order."""
    desc = panel.get("visual_description", "") or ""
    if _PROMO_BLOCK.search(f"{panel.get('ocr_text','') or ''} {desc}"):
        return "promo/credits card — never narrated"
    if panel.get("source") == "vision-segment":
        w, h = panel.get("width") or 0, panel.get("height") or 0
        if (w and w < _MIN_BEAT_PX) or (h and h < _MIN_BEAT_PX):
            return "degenerate crop (too small)"
        if not (panel.get("ocr_text") or "").strip() and not desc.strip():
            return "empty beat (no text, no description)"
        return None
    if _ABSTRACT_OVERRIDE.search(desc):
        return "abstract/fragment (bubble, sliver, SFX, blank)"
    if not (_KEEP_SUBJECT.search(desc) or _KEEP_SCENE.search(desc)):
        return "names no subject or scene"
    return None


def is_junk_panel(panel):
    """True if the panel should be DROPPED from matching.

    Two regimes:

    - VISION-SEGMENTED beats (source == "vision-segment"): these are already
      model-CURATED story beats, not geometric over-splits, so the keyword
      artifact filter does not apply (it was built for sliver/blank/bubble
      fragments and false-drops real lore beats like "a bald martial artist
      stands"). A vision beat is junk ONLY if it is physically empty — a
      degenerate crop or no text AND no description at all.

    - GEOMETRIC crops (everything else): positive-keep rule — junk unless the
      description names a real subject or scene, minus an abstract/decorative
      self-declaration override. Content-free fragments (empty bubbles, stray
      lines, bare SFX/logo words, abstract shapes) are dropped without needing
      an explicit pattern for each junk variety.
    """
    desc = panel.get("visual_description", "") or ""
    # Promo/credits block outranks every keep-rule in every regime (B2).
    if _PROMO_BLOCK.search(f"{panel.get('ocr_text','') or ''} {desc}"):
        return True
    if panel.get("source") == "vision-segment":
        ocr = panel.get("ocr_text", "") or ""
        w, h = panel.get("width") or 0, panel.get("height") or 0
        too_small = (w and w < _MIN_BEAT_PX) or (h and h < _MIN_BEAT_PX)
        empty = not ocr.strip() and not desc.strip()
        return bool(too_small or empty)
    if _ABSTRACT_OVERRIDE.search(desc):
        return True
    return not (_KEEP_SUBJECT.search(desc) or _KEEP_SCENE.search(desc))


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


# Why the last _gemini_embed call gave up, or "" if it succeeded. Read by
# build_scorer so a fallback to lexical carries its REASON instead of
# vanishing (Session 27: Iron-Blooded silently matched on token overlap).
LAST_EMBED_ERROR = ""

# Appended to whenever a run degrades, so ingest can persist the reason.
EMBED_FALLBACK_REASON = []

# Long holds the cap could not split (no free panel between). Silently doing
# nothing here is why a 16.7s single image survived to a finished export.
HOLD_CAP_REPORT = []

EMBED_BATCH = 32          # texts per API call
EMBED_RETRIES = 4         # attempts per batch before giving up
_TRANSIENT = ("429", "rate", "quota", "exhaust", "unavailable", "timeout",
              "deadline", "503", "500", "internal")


def _is_transient(err):
    m = f"{type(err).__name__} {err}".lower()
    return any(t in m for t in _TRANSIENT)


def _gemini_embed(texts, model_name=GEMINI_EMBED_MODEL):
    """Embed texts via the Gemini embeddings API, BATCHED, with retries.

    Returns an L2-normalized array, or None (and sets LAST_EMBED_ERROR) if the
    SDK / key is missing or the API keeps failing. Vectors are cached on disk
    by text hash so repeat runs cost nothing.

    Session 27: this used to issue ONE CALL PER STRING — 133 calls for a
    single chapter — and wrap the whole loop in `except Exception: return
    None`. Iron-Blooded got 19 calls in, hit a transient API failure, and
    silently degraded to lexical token-overlap matching with no trace of why.
    Batching removes the rate-limit exposure that caused it; retries absorb
    what is left; LAST_EMBED_ERROR makes any remaining fallback visible.
    """
    global LAST_EMBED_ERROR
    LAST_EMBED_ERROR = ""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not texts:
        LAST_EMBED_ERROR = "no texts to embed"
        return None
    if not api_key:
        LAST_EMBED_ERROR = "GEMINI_API_KEY is not set"
        return None
    try:
        from google import genai
        from google.genai import types
        import numpy as np
    except Exception as e:
        LAST_EMBED_ERROR = f"google-genai SDK unavailable: {e}"
        return None

    cache = _load_embed_cache()
    client = None
    dirty = False
    out = {}
    todo = []
    for t in texts:
        key = _text_key(t, model_name)
        if cache.get(key) is not None:
            out[key] = cache[key]
        elif key not in [k for k, _ in todo]:
            todo.append((key, t if t else " "))

    # Whether the installed SDK really returns one vector per text for a list
    # of contents. Dockerfile pins only "google-genai>=1.0.0", so production
    # can differ from any dev box: a batch of 32 came back as 1 vector in
    # prod while the same call returned 3-for-3 locally (Session 27). Rather
    # than chase versions, probe once and drop to per-text if batching is not
    # honoured — correctness must not depend on the resolved dependency.
    batch_ok = True
    i = 0
    while i < len(todo):
        chunk = todo[i:i + (EMBED_BATCH if batch_ok else 1)]
        if client is None:
            client = genai.Client(api_key=api_key)

        def _call():
            payload = [t for _, t in chunk]
            return client.models.embed_content(
                model=model_name,
                contents=payload if len(payload) > 1 else payload[0],
                config=types.EmbedContentConfig(task_type=_EMBED_TASK),
            )

        last_err = None
        for attempt in range(EMBED_RETRIES):
            try:
                if _usage:
                    with _usage.gate("gemini", len(chunk), model=model_name):
                        resp = _call()
                else:
                    resp = _call()
                embs = list(resp.embeddings)
                if len(embs) != len(chunk):
                    if len(chunk) > 1:
                        # This SDK does not batch. Redo this chunk one text at
                        # a time; slower, but it actually works.
                        batch_ok = False
                        last_err = None
                        break
                    raise RuntimeError(
                        f"API returned {len(embs)} vectors for {len(chunk)} texts")
                for (key, _t), emb in zip(chunk, embs):
                    vec = list(emb.values)
                    cache[key] = vec
                    out[key] = vec
                dirty = True
                last_err = None
                i += len(chunk)
                break
            except Exception as e:
                # A guardrail cap breach must HALT the job, not degrade to the
                # known-bad lexical fallback — that would mask a hard stop as a
                # quality regression.
                if _usage and isinstance(e, _usage.UsageCapExceeded):
                    if dirty:
                        _save_embed_cache(cache)
                    raise
                last_err = e
                if not _is_transient(e) or attempt == EMBED_RETRIES - 1:
                    break
                time.sleep(2 ** attempt)          # 1s, 2s, 4s
        if last_err is not None:
            LAST_EMBED_ERROR = (
                f"{type(last_err).__name__}: {last_err} "
                f"(at text {i + 1} of {len(todo)} uncached, "
                f"{len(out)}/{len(texts)} embedded, "
                f"batching={'on' if batch_ok else 'off'})")
            if dirty:
                _save_embed_cache(cache)
            return None

    if dirty:
        _save_embed_cache(cache)

    try:
        vectors = [out[_text_key(t, model_name)] for t in texts]
    except KeyError as e:
        LAST_EMBED_ERROR = f"missing vector for a text ({e})"
        return None
    arr = np.asarray(vectors, dtype="float32")
    if arr.ndim != 2 or arr.shape[0] != len(texts):
        LAST_EMBED_ERROR = (f"degenerate embedding array {arr.shape} "
                            f"for {len(texts)} texts")
        return None
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
    why = LAST_EMBED_ERROR
    emb_p = _gemini_embed(panel_texts) if emb_b is not None else None
    if emb_b is not None and emb_p is None:
        why = LAST_EMBED_ERROR
    method_name = "gemini-embeddings"
    if emb_b is None or emb_p is None:
        # LOUD: semantic matching just degraded to token overlap. This used to
        # be invisible and produced a whole chapter matched on bag-of-words.
        print(f"[!] SEMANTIC MATCHING UNAVAILABLE — falling back. Reason: {why}")
        EMBED_FALLBACK_REASON.append(why)
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


# ------------------------------------------- global monotonic DP aligner
def panel_importance(p, max_area=None):
    """Local editorial-importance heuristic (A2), zero API calls.

    area (bigger art = bigger story moment, normalized to the chapter's max)
    + real dialogue (>=4 OCR words) + a description naming both a subject and
    a scene. Range ~0..1.8. Used as a match-score bonus so beats gravitate to
    story panels, and by the hold-cap redistribution to pick fill targets.
    """
    w, h = p.get("width") or 0, p.get("height") or 0
    area = (w * h) / max_area if max_area else 0.0
    ocr_words = len((p.get("ocr_text") or "").split())
    desc = p.get("visual_description") or ""
    return (min(area, 1.0)
            + (0.5 if ocr_words >= 4 else 0.0)
            + (0.3 if (_KEEP_SUBJECT.search(desc) and _KEEP_SCENE.search(desc)) else 0.0))


# A beat may only land on a panel from its own scene (B1 provenance). The
# violation cost is steep-but-finite so the DP stays soluble even if a
# provenance list is malformed.
CONSTRAINT_COST = -1e6
IMPORTANCE_W = 0.15
HOLD_CAP_S = 12.0   # A2: max seconds one panel may stay on screen


def _provenance_sets(beats):
    """Per-beat allowed panel-id sets, or None to disable constraints.

    Constraints engage only when most beats carry provenance (>=80%) — a
    hand-edited or legacy script without panel_ids falls back to free
    matching rather than half-constrained nonsense.
    """
    sets = [set(b["panel_ids"]) if b.get("panel_ids") else None for b in beats]
    tagged = sum(1 for s in sets if s)
    if not beats or tagged / len(beats) < 0.8:
        return None
    return sets


def enforce_hold_cap(beats, panels, assignments, cap_s=HOLD_CAP_S):
    """A2 post-pass: a single-panel hold longer than cap_s spreads its
    trailing beats across UNUSED non-junk panels lying between it and the
    next assigned panel (monotonic order preserved). Mirrors what a human
    editor does with a long narration stretch over a tall action sequence."""
    if not assignments or not beats or "start" not in beats[0]:
        return assignments
    junk = [is_junk_panel(p) for p in panels]
    used = {a["panel_index"] for a in assignments}
    i = 0
    while i < len(assignments):
        j = i
        while (j + 1 < len(assignments)
               and assignments[j + 1]["panel_index"] == assignments[i]["panel_index"]):
            j += 1
        run = assignments[i:j + 1]
        dur = beats[run[-1]["beat_index"]]["end"] - beats[run[0]["beat_index"]]["start"]
        if dur > cap_s and len(run) >= 2:
            lo = assignments[i]["panel_index"]
            hi = assignments[j + 1]["panel_index"] if j + 1 < len(assignments) else len(panels)
            gap = [k for k in range(lo + 1, hi) if k not in used and not junk[k]]
            if not gap:
                HOLD_CAP_REPORT.append({
                    "panel": panels[lo].get("panel_id"),
                    "seconds": round(dur, 2), "beats": len(run),
                    "reason": ("no free non-junk panel between this one and the "
                               "next assigned panel, so the hold could not be split")})
            if gap:
                # spread the run's beats evenly across [lo] + gap panels
                targets = [lo] + gap
                per = max(1, len(run) // len(targets))
                for n, a in enumerate(run):
                    t = targets[min(n // per, len(targets) - 1)]
                    a["panel_index"] = t
                    a["held"] = n % per > 0
                    used.add(t)
        i = j + 1
    return assignments


def match_beats_to_panels(beats, panels, embed_model=None):
    """
    Assign one panel per beat, monotonically non-decreasing in panel index
    (story order preserved), maximizing the TOTAL match score over the whole
    sequence via dynamic programming — NOT greedily.

    Why global DP and not greedy: a greedy forward-only matcher makes each
    choice locally and can never revisit it, so small local errors compound and
    the video "drifts" more and more out of sync with the narration over time
    (and a single dynamic panel could swallow 10+ beats). The DP optimizes the
    entire beat->panel path at once, so a locally-tempting-but-globally-wrong
    panel is rejected when it would hurt later beats. This is the structural fix
    for the accumulating desync.

    State (prefix-max DP, O(n_beats * n_panels * MAX_HOLD)):
        dp[j][r] = best total score with the current beat on panel j, having
                   been on j for r+1 consecutive beats (r in 0..MAX_HOLD-1).
    Transitions to the next beat:
        hold      -> same j, r+1  (r+1 < MAX_HOLD)   : cost - HOLD_PENALTY
        over-hold -> same j, stay at top bucket      : cost - OVER_HOLD_PENALTY
        advance   -> any j' > j, r'=0                : cost
    Holding costs HOLD_PENALTY (gently favors progression); the top bucket has a
    steep OVER_HOLD_PENALTY self-loop so holding past MAX_HOLD is very costly but
    still possible — this is what keeps the finale on the last REAL panel instead
    of advancing onto blank title-card junk when the last page has no real panels.

    Junk/blank panels get a steep finite cost (JUNK_COST), so the optimal path
    avoids them unless there is literally no alternative — and an over-cap hold on
    a real panel always beats advancing onto junk. Indices stay valid for the
    caller's build_timeline (panels list is never mutated).
    """
    raw_score, method = build_scorer(beats, panels, embed_model)
    junk = [is_junk_panel(p) for p in panels]
    n_junk = sum(junk)
    if n_junk:
        print(f"Excluded {n_junk} junk/blank panels of {len(panels)} "
              f"(steep cost; avoided unless nothing else is reachable).")

    # B1: provenance constraints (beat may only land in its own scene) and
    # A2: importance bonus (beats gravitate to story panels).
    allowed = _provenance_sets(beats)
    if allowed:
        method += "+provenance"
    max_area = max((p.get("width") or 0) * (p.get("height") or 0)
                   for p in panels) or None
    imp = [panel_importance(p, max_area) for p in panels]
    pid = [p.get("panel_id") for p in panels]

    NEG = -1e18        # sentinel for an unreachable DP state
    JUNK_COST = -100.0  # finite: junk is avoided but not impossible (last resort)
    R = max(1, MAX_HOLD)  # number of run-length buckets

    def cost(bi, pi):
        if allowed and allowed[bi] and pid[pi] not in allowed[bi]:
            return CONSTRAINT_COST
        if junk[pi]:
            return JUNK_COST
        return raw_score(bi, pi) + IMPORTANCE_W * imp[pi]

    n_b, n_p = len(beats), len(panels)
    if n_b == 0 or n_p == 0:
        return [], method

    # dp[j][r] for the current beat; beat 0 starts every panel at run-length 0.
    dp = [[NEG] * R for _ in range(n_p)]
    for j in range(n_p):
        dp[j][0] = cost(0, j)
    # back[i][j][r] = (prev_j, prev_r) chosen for beat i landing on (j, r)
    back = [None]  # back[0] unused
    for i in range(1, n_b):
        newdp = [[NEG] * R for _ in range(n_p)]
        bp = [[None] * R for _ in range(n_p)]
        # prefix-max over panels j' < j of the best value across any run-length,
        # used for the ADVANCE transition (new run-length 0). prefix_arg is the
        # (j', r') that achieved it, so backpointers are always (prev_j, prev_r).
        prefix_best, prefix_arg = NEG, None
        for j in range(n_p):
            c = cost(i, j)
            # advance into j (from best earlier panel), run-length resets to 0
            if prefix_arg is not None:
                newdp[j][0] = c + prefix_best
                bp[j][0] = prefix_arg
            # hold on j: extend run-length r-1 -> r (r >= 1), cost the penalty
            for r in range(1, R):
                prev = dp[j][r - 1]
                if prev > NEG:
                    val = c + prev - HOLD_PENALTY
                    if val > newdp[j][r]:
                        newdp[j][r] = val
                        bp[j][r] = (j, r - 1)
            # over-cap self-loop at the top bucket: keep holding past MAX_HOLD
            # at a steep (finite) penalty, so the finale can dwell on the last
            # real panel rather than advance onto blank/junk title-card panels
            top_prev = dp[j][R - 1]
            if top_prev > NEG:
                val = c + top_prev - OVER_HOLD_PENALTY
                if val > newdp[j][R - 1]:
                    newdp[j][R - 1] = val
                    bp[j][R - 1] = (j, R - 1)
            # fold panel j's best (over run-lengths) into the advance window
            jr = max(range(R), key=lambda r: dp[j][r])
            if dp[j][jr] > prefix_best:
                prefix_best, prefix_arg = dp[j][jr], (j, jr)
        dp = newdp
        back.append(bp)

    # backtrack the optimal path
    best_j, best_r, best_val = 0, 0, NEG
    for j in range(n_p):
        for r in range(R):
            if dp[j][r] > best_val:
                best_val, best_j, best_r = dp[j][r], j, r
    path = [0] * n_b
    j, r = best_j, best_r
    for i in range(n_b - 1, -1, -1):
        path[i] = j
        if i > 0:
            j, r = back[i][j][r]
    assignments = []
    for i in range(n_b):
        advanced = (i == 0) or (path[i] != path[i - 1])
        assignments.append({
            "beat_index": i,
            "panel_index": path[i],
            "score": round(cost(i, path[i]), 4),
            "held": not advanced,
        })
    assignments = enforce_hold_cap(beats, panels, assignments)
    return assignments, method + "+dp"


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


# ------------------------------------------------- regression gate
def validate_beatsheet(shots, panels):
    """Pre-render gate. Return a list of problem strings (empty == clean).

    Enforces the invariants that broke in past sessions, so a bad beatsheet
    fails in ~1s instead of wasting a ~15-min render:
      1. every beat resolves to a panel that exists in descriptions,
      2. no beat lands on a junk / blank / line-fragment panel,
      3. every referenced image file actually exists on disk,
      4. no gaps in the timeline (already asserted, re-checked here).
    'Hold the last valid panel when only junk remains' is guaranteed upstream by
    the DP (OVER_HOLD_PENALTY), so the OUTPUT simply must contain no junk — which
    check (2) verifies directly.
    """
    by_id = {p.get("panel_id"): p for p in panels}
    problems = []
    for s in shots:
        pid = s.get("panel_id")
        p = by_id.get(pid)
        if p is None:
            problems.append(f"beat {s['index']}: panel_id {pid!r} not in descriptions")
            continue
        if is_junk_panel(p):
            problems.append(
                f"beat {s['index']} ({s.get('start',0):.1f}s): junk/blank panel {pid} "
                f"— {(p.get('visual_description','') or '')[:50]!r}")
        f = s.get("panel_file") or p.get("file")
        if f and os.path.isabs(f) and not os.path.exists(f):
            problems.append(f"beat {s['index']}: image file missing: {f}")
    for i in range(len(shots) - 1):
        if abs(shots[i]["end"] - shots[i + 1]["start"]) > 1e-6:
            problems.append(f"beat {s['index']}: timeline gap before {i+1}")
    return problems


def beatsheet_metrics(shots, panels):
    """Compact quality snapshot for regression baselining."""
    by_id = {p.get("panel_id"): p for p in panels}
    max_hold = run = 1
    for i in range(1, len(shots)):
        if shots[i]["panel_id"] == shots[i - 1]["panel_id"]:
            run += 1
            max_hold = max(max_hold, run)
        else:
            run = 1

    def pnum(pid):
        m = re.findall(r"\d+", pid or "")
        return (int(m[0]), int(m[1])) if len(m) >= 2 else (0, 0)
    backward = sum(1 for i in range(1, len(shots))
                   if pnum(shots[i]["panel_id"]) < pnum(shots[i - 1]["panel_id"]))
    junk = sum(1 for s in shots
               if by_id.get(s["panel_id"]) and is_junk_panel(by_id[s["panel_id"]]))
    return {
        "beats": len(shots),
        "distinct_panels": len({s["panel_id"] for s in shots}),
        "max_hold": max_hold,
        "held": sum(1 for s in shots if s["held"]),
        "backward_jumps": backward,
        "junk_in_output": junk,
    }


def run(beats_path, descriptions_path, out_path, embed_model=None):
    beats = json.load(open(beats_path))
    panels = json.load(open(descriptions_path))
    # Resolve relative file paths to absolute, rooted at the descriptions.json dir
    desc_dir = os.path.dirname(os.path.abspath(descriptions_path))
    for p in panels:
        if p.get("file") and not os.path.isabs(p["file"]):
            p["file"] = os.path.join(desc_dir, p["file"])
    # only use panels that described successfully and have real size
    # (junk/SFX-panel filtering now happens inside match_beats_to_panels so it
    # applies to every entry point, not just this one)
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
