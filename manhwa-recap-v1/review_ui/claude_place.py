"""Claude+ placement — deterministic, monotonic, confidence-aware.

The lab's first placement asked Claude, in one prompt, which panel each line
should play over. That is the wrong tool for the job. Choosing an assignment
that is optimal ACROSS A WHOLE CHAPTER, under ordering and hold constraints, is
a search problem with an exact answer; a language model asked to do it produces
something plausible that nothing checks. Production solves it with dynamic
programming, and production is right.

So this module is deliberately NOT a prompt. It is the same class of solver the
production matcher uses, rebuilt inside the lab so it can be improved without
touching production, and it fixes two weaknesses the production matcher has:

  1. OCR POISONING. Production weights OCR at a fixed 0.55 because OCR is
     usually the most discriminative signal. When the OCR is WRONG, that fixed
     weight is exactly what drags the line onto the wrong panel. Here the weight
     is per-panel and moves with the reader's own confidence: a panel whose OCR
     was marked unreliable is scored mostly on its description, and one whose
     description was marked generic is scored mostly on its OCR. When both are
     weak the panel is not silently trusted — it is reported.

  2. PROVENANCE LOCK-IN. Production forbids a beat from landing outside its own
     scene at a cost of -1e6, which is effectively a wall. If the upstream
     grouping put the wrong panels in a scene, the solver cannot escape it and
     the error is locked in. Here the constraint is SOFT: leaving your scene is
     expensive, but a beat with strong evidence elsewhere can do it, and every
     escape is recorded with its margin so a human can see where the grouping
     was wrong rather than inheriting it silently.

Everything else mirrors production on purpose: forward-only so the story never
runs backwards, a hold penalty that gently favours progression, a steep but
FINITE over-hold penalty so the last real panel can carry a finale rather than
advancing onto a blank card, a junk filter, and an importance bonus.

Claude's role here is advisory only, and optional: `rescore_uncertain()` asks it
to rank a handful of genuinely ambiguous beat/panel pairs. Its answers adjust
scores; they never decide the assignment.
"""

import math
import os
import re

# ---------------------------------------------------------------- tuning
# Mirrors production's matcher where the value is load-bearing, so the two
# solvers behave the same unless a lab improvement is deliberately in play.
HOLD_PENALTY = float(os.environ.get("PLUS_HOLD_PENALTY", 0.06))
MAX_HOLD = int(os.environ.get("PLUS_MAX_HOLD", 5))
OVER_HOLD_PENALTY = float(os.environ.get("PLUS_OVER_HOLD_PENALTY", 0.5))
HOLD_CAP_S = float(os.environ.get("PLUS_HOLD_CAP_S", 12.0))
IMPORTANCE_W = float(os.environ.get("PLUS_IMPORTANCE_W", 0.15))
JUNK_COST = float(os.environ.get("PLUS_JUNK_COST", 3.0))

# Base split between the two text signals, used when both are fully trusted.
# Same starting point as production (OCR is usually the sharper signal); the
# difference is that here it MOVES.
BASE_OCR_W = float(os.environ.get("PLUS_OCR_W", 0.55))
BASE_DESC_W = float(os.environ.get("PLUS_DESC_W", 0.45))

# Leaving your own scene. Finite and tunable — this is the anti-lock-in valve.
#
# CALIBRATED, not guessed. Scores here land in roughly 0.0-0.7, so the cost has
# to be read against that scale. Measured on a fixture whose provenance was
# deliberately wrong (every beat forced into one scene):
#     cost 0.9  -> 0 escapes, the wrong grouping is locked in   (a wall)
#     cost 0.5  -> 1 escape,  only the strongest evidence gets out
#     cost 0.3  -> 2 escapes, the correct assignment fully recovered
#     cost <0.3 -> no further change (plateau)
# 0.3 sits exactly on the knee: an outside panel must beat the best in-scene
# panel by ~50% of the usable score range before a beat will leave its scene.
# That is "strong evidence", not a casual escape — and unlike production's
# -1e6, it is not a wall. Raising this back toward 0.9 reintroduces the
# lock-in this exists to fix.
PROVENANCE_COST = float(os.environ.get("PLUS_PROVENANCE_COST", 0.3))

# A beat whose best and second-best panels are this close is genuinely
# ambiguous and worth an advisory opinion.
AMBIGUOUS_MARGIN = float(os.environ.get("PLUS_AMBIGUOUS_MARGIN", 0.04))

_STOP = set(
    "a an the of to in on at and or but with was were is are be been being "
    "his her its their our your my he she it they them him we you i as by for "
    "from that this these those had has have not no so then than there here "
    "into over under out up down off again once more most very".split())


# ------------------------------------------------------------- similarity
def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9']+", (text or "").lower())
            if t not in _STOP and len(t) > 1]


def build_idf(docs):
    """Inverse document frequency over the chapter's own panels.

    Chapter-local rather than global on purpose: in a chapter about one duel,
    'sword' is near-useless for telling panels apart, and a global corpus would
    never know that. This is what stops the scorer rewarding the word every
    panel happens to share.
    """
    n = max(1, len(docs))
    df = {}
    for d in docs:
        for t in set(_tokens(d)):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


def sim(a, b, idf):
    """IDF-weighted cosine over token sets. Deterministic, no API, no cost."""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    shared = ta & tb
    if not shared:
        return 0.0
    num = sum(idf.get(t, 1.0) ** 2 for t in shared)
    da = math.sqrt(sum(idf.get(t, 1.0) ** 2 for t in ta))
    db = math.sqrt(sum(idf.get(t, 1.0) ** 2 for t in tb))
    return num / (da * db) if da and db else 0.0


# ------------------------------------------------------- panel judgements
def panel_weights(panel):
    """Per-panel OCR/description weights, driven by the reader's own confidence.

    THE OCR-POISONING FIX. Production fixes OCR at 0.55 for every panel, which
    is right on average and catastrophic on the panels where the OCR is wrong —
    precisely the panels a fixed weight cannot notice. The describe stage now
    reports how much it trusts what it read, so the scorer can stop leaning on a
    signal its own reader flagged.
    """
    oc = panel.get("ocr_confidence")
    dc = panel.get("desc_confidence")
    oc = 1.0 if oc is None else max(0.0, min(1.0, float(oc)))
    dc = 1.0 if dc is None else max(0.0, min(1.0, float(dc)))
    if not (panel.get("ocr_text") or "").strip():
        oc = 0.0
    if not (panel.get("visual_description") or "").strip():
        dc = 0.0
    wo, wd = BASE_OCR_W * oc, BASE_DESC_W * dc
    total = wo + wd
    if total <= 0:
        # Nothing trustworthy to score on. Equal weights so the panel still
        # participates rather than silently scoring 0 and being unreachable —
        # is_uncertain() flags it for review instead.
        return BASE_OCR_W, BASE_DESC_W
    return wo / total * (BASE_OCR_W + BASE_DESC_W), wd / total * (BASE_OCR_W + BASE_DESC_W)


def is_uncertain(panel, floor=0.45):
    """Both signals weak — the panel is scored, but a human should look."""
    oc = panel.get("ocr_confidence")
    dc = panel.get("desc_confidence")
    oc = 1.0 if oc is None else float(oc)
    dc = 1.0 if dc is None else float(dc)
    has_ocr = bool((panel.get("ocr_text") or "").strip())
    return (not has_ocr or oc < floor) and dc < floor


def is_junk(panel):
    """Panels a narration line must not land on.

    Uses the reader's OWN structured judgements (is_credits, subject_type)
    rather than the production matcher's keyword archaeology over a free-text
    description. The reader looked at the image; guessing from its prose
    afterwards is strictly less information.
    """
    if panel.get("is_credits"):
        return True
    if (panel.get("subject_type") or "").lower() in ("credits", "logo"):
        return True
    desc = (panel.get("visual_description") or "").strip()
    ocr = (panel.get("ocr_text") or "").strip()
    if not desc and not ocr:
        return True
    w, h = panel.get("width") or 0, panel.get("height") or 0
    if (w and w < 40) or (h and h < 40):
        return True
    return False


def importance(panel, max_area=None):
    """Editorial weight: big art, real dialogue, a describable subject."""
    w, h = panel.get("width") or 0, panel.get("height") or 0
    area = (w * h) / max_area if max_area else 0.0
    ocr_words = len((panel.get("ocr_text") or "").split())
    explicit = panel.get("importance")
    base = (min(area, 1.0)
            + (0.5 if ocr_words >= 4 else 0.0)
            + (0.3 if len((panel.get("visual_description") or "").split()) >= 8
               else 0.0))
    if explicit:
        # The reader rates 1-5; fold it in rather than replacing the geometric
        # signal, so one confident model opinion cannot dominate.
        try:
            base += (float(explicit) - 3.0) * 0.1
        except (TypeError, ValueError):
            pass
    return max(0.0, base)


# ---------------------------------------------------------------- the DP
TEXT_SURFACE_FRAC = float(os.environ.get("PLUS_TEXT_SURFACE_FRAC", 0.50))


def is_text_surface(panel):
    """Is this panel a place to READ rather than a picture to SHOW?

    THE PRINCIPLE: speech bubbles are BACKEND — they feed the OCR that writes
    the narration. Art is FRONTEND — it is what the viewer's eye gets. A panel
    that is mostly dialogue box has already done its job by the time the script
    exists; putting it on screen spends a frame on words the narrator is
    speaking aloud anyway.

    Measured on I Am The Fated Villain ch.353: 46% of crops were >=25% speech
    bubble and one was 84.9%, because the moment-slicer cuts tall panels at
    GROUPS OF SPEECH BUBBLES — dialogue clusters are its unit of meaning.

    `bubble_frac` is measured from the image when the producer supplies it;
    `subject_type` is the reader's own judgement and is used when it does not.
    """
    bf = panel.get("bubble_frac")
    if bf is not None:
        return float(bf) >= TEXT_SURFACE_FRAC
    return (panel.get("subject_type") or "").lower() == "text"


def place(beats, panels, allowed=None, progress=None):
    """Assign one panel per beat: forward-only, globally optimal, explainable.

    `allowed` maps beat index -> set of panel indexes that beat's scene owns
    (provenance). Leaving that set is permitted at PROVENANCE_COST and every
    escape is reported, rather than being forbidden outright.

    Returns (assignments, diagnostics).
    """
    n_b, n_p = len(beats), len(panels)
    if not n_b or not n_p:
        return [], {"error": "nothing to place"}

    idf = build_idf([f"{p.get('ocr_text','')} {p.get('visual_description','')}"
                     for p in panels])
    max_area = max(((p.get("width") or 0) * (p.get("height") or 0))
                   for p in panels) or None
    junk = [is_junk(p) for p in panels]
    imp = [importance(p, max_area) for p in panels]
    wts = [panel_weights(p) for p in panels]

    # BACKEND vs FRONTEND. A text surface keeps feeding the script — the script
    # was already written from these descriptions before placement runs — but
    # it is removed from the VISUAL candidate set outright. A soft penalty was
    # tried and is not enough: the score's OCR-similarity term actively REWARDS
    # matching a line to the panel holding its own words, which is exactly the
    # bubble panel. A cost that competes with that will sometimes lose.
    text_surface = [is_text_surface(p) for p in panels]
    eligible = [j for j in range(n_p) if not text_surface[j]]
    if not eligible:
        # Every panel reads as text: show them rather than show nothing.
        eligible = list(range(n_p))
        text_surface = [False] * n_p

    # ---- score matrix -------------------------------------------------
    S = [[0.0] * n_p for _ in range(n_b)]
    for i, b in enumerate(beats):
        text = b.get("text", "")
        for j, p in enumerate(panels):
            wo, wd = wts[j]
            s = (wo * sim(text, p.get("ocr_text", ""), idf)
                 + wd * sim(text, p.get("visual_description", ""), idf))
            s += IMPORTANCE_W * imp[j]
            if text_surface[j]:
                s = float("-inf")          # backend only — never on screen
            elif junk[j]:
                s -= JUNK_COST
            if allowed is not None:
                ok = allowed.get(i)
                if ok and j not in ok:
                    s -= PROVENANCE_COST
            S[i][j] = s
        if progress and i % 25 == 0:
            progress(f"scoring beat {i + 1}/{n_b}")

    NEG = float("-inf")
    # dp[j][r] — best total with this beat on panel j, held r+1 beats.
    dp = [[NEG] * MAX_HOLD for _ in range(n_p)]
    back = [[None] * MAX_HOLD for _ in range(n_p)]
    for j in range(n_p):
        dp[j][0] = S[0][j]

    for i in range(1, n_b):
        # Prefix max over j' <= j of the best "any hold bucket" value, so the
        # advance transition is O(1) per cell instead of O(n_p).
        best_upto, arg_upto = [NEG] * n_p, [None] * n_p
        run, run_arg = NEG, None
        for j in range(n_p):
            cell, cell_r = NEG, None
            for r in range(MAX_HOLD):
                if dp[j][r] > cell:
                    cell, cell_r = dp[j][r], r
            if cell > run:
                run, run_arg = cell, (j, cell_r)
            best_upto[j], arg_upto[j] = run, run_arg

        ndp = [[NEG] * MAX_HOLD for _ in range(n_p)]
        nback = [[None] * MAX_HOLD for _ in range(n_p)]
        for j in range(n_p):
            s = S[i][j]
            # advance: from any panel <= j (strictly, any j' <= j; staying on j
            # is the hold transition below, which carries its own penalty)
            if j > 0 and best_upto[j - 1] > NEG:
                v = best_upto[j - 1] + s
                if v > ndp[j][0]:
                    ndp[j][0], nback[j][0] = v, arg_upto[j - 1]
            for r in range(MAX_HOLD):
                if dp[j][r] == NEG:
                    continue
                if r + 1 < MAX_HOLD:
                    v = dp[j][r] + s - HOLD_PENALTY
                    if v > ndp[j][r + 1]:
                        ndp[j][r + 1], nback[j][r + 1] = v, (j, r)
                else:
                    v = dp[j][r] + s - OVER_HOLD_PENALTY
                    if v > ndp[j][r]:
                        ndp[j][r], nback[j][r] = v, (j, r)
        dp, back = ndp, nback
        # Keep the backpointer chain for reconstruction.
        if i == 1:
            chain = [back]
        else:
            chain.append(back)
        if progress and i % 50 == 0:
            progress(f"solving beat {i + 1}/{n_b}")

    if n_b == 1:
        chain = []

    # ---- reconstruct ---------------------------------------------------
    best, best_cell = NEG, None
    for j in range(n_p):
        for r in range(MAX_HOLD):
            if dp[j][r] > best:
                best, best_cell = dp[j][r], (j, r)
    if best_cell is None:
        return [], {"error": "no feasible placement"}

    path = [best_cell]
    for step in range(len(chain) - 1, -1, -1):
        j, r = path[-1]
        prev = chain[step][j][r]
        if prev is None:
            break
        path.append(prev)
    path.reverse()
    while len(path) < n_b:                 # defensive: never emit a short path
        path.insert(0, path[0])

    assignments, escapes, uncertain = [], [], []
    for i, (j, r) in enumerate(path[:n_b]):
        esc = False
        if allowed is not None:
            ok = allowed.get(i)
            if ok and j not in ok:
                esc = True
                # Report the margin: how much better this panel scored than the
                # best panel inside the beat's own scene. A large margin means
                # the grouping was wrong; a small one means look at it.
                inside = max((S[i][k] for k in ok), default=None)
                escapes.append({
                    "beat": i, "panel_index": j,
                    "panel_id": panels[j].get("panel_id"),
                    "scene": beats[i].get("scene_id"),
                    "margin": round(S[i][j] - inside, 4)
                              if inside is not None else None,
                })
        if is_uncertain(panels[j]):
            uncertain.append({"beat": i, "panel_id": panels[j].get("panel_id")})
        assignments.append({
            "beat_index": i, "panel_index": j,
            "score": round(S[i][j], 4), "held": r > 0,
            "provenance_escape": esc,
        })

    return assignments, {
        "beats": n_b, "panels": n_p,
        "junk_panels": sum(junk),
        "text_surfaces_excluded": sum(text_surface),
        "provenance_escapes": escapes,
        "uncertain_placements": uncertain,
        "total_score": round(best, 4),
        "distinct_panels": len({a["panel_index"] for a in assignments}),
    }


# ---------------------------------------------------------------- repairs
def enforce_monotonic(assignments):
    """Guarantee the story never runs backwards.

    The DP cannot produce an inversion — advance only moves forward. This is a
    belt-and-braces pass so that ANY future source of assignments (a Claude
    rescoring, a hand edit, a bug) still cannot ship a backwards recap. It
    reports what it had to touch rather than fixing silently.
    """
    fixed, last = [], -1
    for a in assignments:
        a = dict(a)
        if a["panel_index"] < last:
            a["repaired_from"] = a["panel_index"]
            a["panel_index"] = last
        last = a["panel_index"]
        fixed.append(a)
    return fixed, [a for a in fixed if "repaired_from" in a]


def cap_holds(assignments, beats, cap_s=HOLD_CAP_S):
    """Report (never silently redistribute) any panel held past the cap.

    Production redistributes. Here it is surfaced instead, because in the lab
    the interesting output is WHERE the pacing broke, not a quietly patched
    timeline that hides it.
    """
    out, run, start = [], [], None
    for a in assignments:
        b = beats[a["beat_index"]]
        if run and run[-1]["panel_index"] == a["panel_index"]:
            run.append(a)
        else:
            if run:
                span = beats[run[-1]["beat_index"]].get("end", 0) - start
                if span > cap_s:
                    out.append({"panel_index": run[0]["panel_index"],
                                "beats": len(run), "seconds": round(span, 2)})
            run, start = [a], b.get("start", 0)
    if run:
        span = beats[run[-1]["beat_index"]].get("end", 0) - start
        if span > cap_s:
            out.append({"panel_index": run[0]["panel_index"],
                        "beats": len(run), "seconds": round(span, 2)})
    return out


def ambiguous_pairs(beats, panels, assignments, limit=12):
    """Beats whose winner barely beat the runner-up — the only ones worth
    spending an advisory Claude call on."""
    idf = build_idf([f"{p.get('ocr_text','')} {p.get('visual_description','')}"
                     for p in panels])
    wts = [panel_weights(p) for p in panels]
    out = []
    for a in assignments:
        i = a["beat_index"]
        text = beats[i].get("text", "")
        scored = []
        for j, p in enumerate(panels):
            wo, wd = wts[j]
            scored.append((wo * sim(text, p.get("ocr_text", ""), idf)
                           + wd * sim(text, p.get("visual_description", ""), idf), j))
        scored.sort(reverse=True)
        if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < AMBIGUOUS_MARGIN:
            out.append({"beat": i, "text": text[:200],
                        "chosen": panels[a["panel_index"]].get("panel_id"),
                        "runner_up": panels[scored[1][1]].get("panel_id"),
                        "margin": round(abs(scored[0][0] - scored[1][0]), 4)})
    out.sort(key=lambda r: r["margin"])
    return out[:limit]


# ------------------------------------------------- visual progression
# THE FOLDING FIX (second half; the first is finer scenes upstream).
#
# "Folded" on the board does not mean segments were merged and it is not a UI
# artefact. storyboard.py reaches that branch when a panel HAS script
# provenance but never received a segment — the narration unit claimed it and
# the matcher had no beat to put on it. So on a chapter with 164 panels and 14
# units, most panels were folded by construction: there simply were not enough
# beats to go round, and the viewer sat on one frame while three sentences
# played.
#
# The cure is narration granularity upstream. But even with well-sized scenes a
# unit legitimately spans several distinct panels, and the timeline must still
# MOVE through them rather than holding one image for the whole line. That is
# what this does: it gives every distinct panel a slice of its unit's own
# window.
#
# Two things it must not do: create flicker (so every slice has a floor), and
# explode a run of near-identical panels into a strobe (so panels are selected
# for distinctness, not just taken in order).

MIN_VISUAL_SEC = float(os.environ.get("PLUS_MIN_VISUAL_SEC", 1.4))


def _distinct(a, b):
    """Are these two panels different enough to be worth a separate slice?

    Cheap and deliberately conservative — a page change or a real change of
    content. Near-duplicates (the same shot redrawn, a reaction beat on the
    same framing) collapse, which is what stops a strobe.
    """
    if a is None:
        return True
    pa = (a.get("panel_id") or "").split("_")[0]
    pb = (b.get("panel_id") or "").split("_")[0]
    if pa != pb:
        return True                       # different source page
    if (a.get("subject_type") or "") != (b.get("subject_type") or ""):
        return True
    ta = set(_tokens(a.get("visual_description", "")))
    tb = set(_tokens(b.get("visual_description", "")))
    if not ta or not tb:
        return True
    overlap = len(ta & tb) / float(len(ta | tb))
    return overlap < 0.55


def select_progression(panels, capacity):
    """Pick which of a unit's panels actually earn screen time."""
    if capacity <= 0 or not panels:
        return []
    kept, last = [], None
    for p in panels:
        if is_junk(p):
            continue
        if _distinct(last, p):
            kept.append(p)
            last = p
    if not kept:
        kept = [p for p in panels if not is_junk(p)][:1]
    if len(kept) <= capacity:
        return kept
    # Too many for the window: keep the most important, but preserve reading
    # order so the story still runs forwards.
    ranked = sorted(kept, key=lambda p: -importance(p))[:capacity]
    order = {id(p): i for i, p in enumerate(kept)}
    return sorted(ranked, key=lambda p: order[id(p)])


def expand_units(assigns, beats, descs, unit_panels, min_sec=None):
    """Turn beat->panel assignments into VISUAL SLOTS that progress.

    `unit_panels` maps scene_id -> ordered list of panel indexes that unit owns.
    Returns (slots, diagnostics) where a slot is
    {panel_index, start, end, beat_index}.
    """
    min_sec = MIN_VISUAL_SEC if min_sec is None else min_sec
    by_scene = {}
    for a in assigns:
        sid = beats[a["beat_index"]].get("scene_id")
        by_scene.setdefault(sid, []).append(a)

    slots, diag = [], {"units_expanded": 0, "panels_recovered": 0,
                       "wide_units": [], "capacity_limited": []}

    for sid, group in sorted(by_scene.items(), key=lambda kv: (kv[1][0]["beat_index"])):
        group.sort(key=lambda a: a["beat_index"])
        w0 = float(beats[group[0]["beat_index"]]["start"])
        w1 = float(beats[group[-1]["beat_index"]]["end"])
        span = max(0.001, w1 - w0)

        owned = [descs[i] for i in unit_panels.get(sid, [])]
        used_idx = [a["panel_index"] for a in group]
        if not owned:
            owned = [descs[i] for i in used_idx]

        capacity = max(1, int(span // min_sec))
        wanted = select_progression(owned, 10 ** 6)
        chosen = select_progression(owned, capacity)

        # Reported BEFORE the early return. The worst capacity squeeze — a
        # window so short it forces the unit back down to one panel — lands in
        # the no-expansion branch, so checking only inside the expansion branch
        # stays silent exactly when the limit bites hardest.
        if len(wanted) > capacity:
            diag["capacity_limited"].append(
                {"unit": sid, "wanted": len(wanted), "fitted": capacity,
                 "seconds": round(span, 1)})

        if len(chosen) <= len(group):
            # Nothing to recover — one slot per beat, as before.
            for a in group:
                b = beats[a["beat_index"]]
                slots.append({"panel_index": a["panel_index"],
                              "start": float(b["start"]), "end": float(b["end"]),
                              "beat_index": a["beat_index"]})
            continue

        idx_of = {d["panel_id"]: i for i, d in enumerate(descs)}
        step = span / len(chosen)
        for i, p in enumerate(chosen):
            s0 = w0 + i * step
            s1 = w1 if i == len(chosen) - 1 else s0 + step
            mid = (s0 + s1) / 2.0
            bi = next((a["beat_index"] for a in group
                       if beats[a["beat_index"]]["start"] <= mid
                       <= beats[a["beat_index"]]["end"]), None)
            if bi is None:
                bi = min(group, key=lambda a: abs(
                    (beats[a["beat_index"]]["start"]
                     + beats[a["beat_index"]]["end"]) / 2 - mid))["beat_index"]
            slots.append({"panel_index": idx_of.get(p["panel_id"],
                                                    used_idx[0]),
                          "start": round(s0, 3), "end": round(s1, 3),
                          "beat_index": bi})
        diag["units_expanded"] += 1
        diag["panels_recovered"] += len(chosen) - len(group)
        if len(chosen) > 3:
            diag["wide_units"].append({"unit": sid, "panels": len(chosen),
                                       "seconds": round(span, 1)})


    slots.sort(key=lambda s: s["start"])
    return slots, diag
