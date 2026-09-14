"""Side-by-side evaluation: the Gemini baseline vs the Claude experiment.

The owner asked for "a usable side-by-side evaluation", not impressions. So
every number here is COMPUTED from the two outputs, and each metric declares
whether it can actually say which side is better.

That last part is the honest core of this module. Some metrics have a direction
that is true by construction — a narration unit with no panel is a defect
whoever produced it, and fewer is better. Others do not: longer OCR is not
better OCR, and a tighter crop is not automatically a better crop. Without a
hand-labelled ground truth for this chapter, reporting "Claude wins on OCR
because it transcribed more characters" would be a fabricated result dressed up
as a measurement.

So each metric carries `direction`:

  "lower_better" / "higher_better"  — a real verdict is computed
  "neutral"                         — both numbers are reported, no winner is
                                      declared, and `note` says what a human
                                      would have to look at to decide

The checker is reused as a measuring instrument: `validator.rule_findings` runs
over BOTH variants with identical rules, so "how many problems does the QA pass
find" is an apples-to-apples count rather than two different yardsticks.
"""

import json
import os
import re

import claude_pipeline
import validator

WIN_CLAUDE = "claude"
WIN_BASELINE = "baseline"
WIN_TIE = "tie"
WIN_NONE = None


# ------------------------------------------------------------- helpers
def _tokens(text):
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def _jaccard(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else 0.0


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def _metric(name, baseline, claude, direction, note="", detail=None):
    """One row of the comparison table, with its verdict already decided."""
    winner = WIN_NONE
    if direction == "lower_better":
        winner = (WIN_TIE if baseline == claude
                  else WIN_CLAUDE if claude < baseline else WIN_BASELINE)
    elif direction == "higher_better":
        winner = (WIN_TIE if baseline == claude
                  else WIN_CLAUDE if claude > baseline else WIN_BASELINE)
    return {"metric": name, "baseline": baseline, "claude": claude,
            "direction": direction, "winner": winner, "note": note,
            "detail": detail or {}}


# --------------------------------------------------- rows for the checker
def _baseline_rows(pdir, review=None):
    return validator.build_rows(pdir, review or {})


def _claude_rows(pdir):
    """The Claude variant expressed in the validator's row shape, so the SAME
    rule pass can measure it.

    The timing block is filled from Claude's PROPOSED plan, and every row is
    marked in_video when it received narration — the experiment has no ticking
    step, so "in the video" means "a line was placed on it".
    """
    descs = claude_pipeline.read(pdir, "descriptions.json", []) or []
    scenes = claude_pipeline.read(pdir, "script.json", []) or []
    plan = {t["panel_id"]: t for t in
            (claude_pipeline.read(pdir, "timing.json", []) or [])}

    unit_of, unit_rank = {}, {}
    for sc in scenes:
        for i, pid in enumerate(sc.get("panel_ids", []) or []):
            unit_of[pid] = sc
            unit_rank[pid] = i

    rows = []
    for i, d in enumerate(descs, start=1):
        pid = d.get("panel_id", "")
        sc = unit_of.get(pid)
        t = plan.get(pid)
        if sc is None:
            placement = {"role": "left_out", "unit": None, "text": ""}
        else:
            placement = {
                "role": "carries" if unit_rank.get(pid, 0) == 0 else "shared",
                "unit": sc.get("scene_id"), "text": sc.get("text", "") or "",
                "panel_slot": unit_rank.get(pid, 0) + 1,
                "panel_count": len(sc.get("panel_ids", []) or []),
            }
        rows.append({
            "n": i, "panel_id": pid, "file": d.get("file") or f"{pid}.png",
            "width": d.get("width"), "height": d.get("height"),
            "ocr": (d.get("ocr_text") or "").strip(),
            "desc": (d.get("visual_description") or "").strip(),
            "desc_ok": bool(d.get("ok", True)),
            "placement": placement,
            "timing": {
                "seg_index": i - 1, "pos": i - 1, "seg_count": 1 if t else 0,
                "dur": t["dur"] if t else None,
                "start": None,
                "in_video": sc is not None,
                "spoken_words": len((placement["text"] or "").split()),
                "silent": False,
            },
        })
    return rows


# ------------------------------------------------------------- crops
def _baseline_crops(pdir):
    """Baseline crop boxes live on the SEGMENTS, not the descriptions, because
    the production crop is chosen per rendered segment."""
    try:
        with open(os.path.join(pdir, "segments.json"), encoding="utf-8") as f:
            segs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for s in segs:
        box = s.get("crop_bbox_norm")
        if box and s.get("panel_id") not in out:
            out[s["panel_id"]] = box
    return out


def _area(box):
    try:
        x0, y0, x1, y1 = box
        return max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ the report
def compare(pdir, review=None):
    """Compute the full side-by-side. Returns a dict the TEST tab renders."""
    b_descs = claude_pipeline.baseline_panels(pdir)
    c_descs = claude_pipeline.read(pdir, "descriptions.json", []) or []
    if not b_descs:
        raise ValueError("This project has no Gemini baseline to compare "
                         "against — ingest the chapter normally first.")
    if not c_descs:
        raise ValueError("The Claude experiment has not produced descriptions "
                         "for this project yet.")

    b_by = {d.get("panel_id"): d for d in b_descs}
    c_by = {d.get("panel_id"): d for d in c_descs}
    shared = [p for p in b_by if p in c_by]

    b_rows = _baseline_rows(pdir, review)
    c_rows = _claude_rows(pdir)
    b_find = validator.rule_findings(b_rows)
    c_find = validator.rule_findings(c_rows)

    metrics = []

    # ---------------------------------------------------------- OCR
    b_empty = sum(1 for p in shared if not (b_by[p].get("ocr_text") or "").strip())
    c_empty = sum(1 for p in shared if not (c_by[p].get("ocr_text") or "").strip())
    b_len = _mean([len(b_by[p].get("ocr_text") or "") for p in shared])
    c_len = _mean([len(c_by[p].get("ocr_text") or "") for p in shared])
    agree = _mean([_jaccard(b_by[p].get("ocr_text"), c_by[p].get("ocr_text"))
                   for p in shared])
    # Credit pages: the baseline has no explicit flag, so both sides are scored
    # by the SAME regex — Claude's own is_credits flag is reported separately
    # rather than being allowed to score itself.
    b_credit = sum(1 for p in shared
                   if len(validator._CREDIT_MARKERS.findall(
                       b_by[p].get("ocr_text") or "")) >= 2)
    c_credit = sum(1 for p in shared
                   if len(validator._CREDIT_MARKERS.findall(
                       c_by[p].get("ocr_text") or "")) >= 2)
    c_flagged = sum(1 for p in shared if c_by[p].get("is_credits"))

    metrics.append(_metric(
        "OCR: panels with no text read", b_empty, c_empty, "neutral",
        "Fewer empty is only better if the panels actually had text. Open a "
        "few of the panels where they disagree to settle it.",
        {"panels_compared": len(shared)}))
    metrics.append(_metric(
        "OCR: mean characters per panel", b_len, c_len, "neutral",
        "Longer is not better — a verbose transcription of a watermark is "
        "worse than a short correct one."))
    metrics.append(_metric(
        "OCR: agreement between the two (0-1)", agree, agree, "neutral",
        "How much the two transcriptions overlap. LOW agreement is the signal "
        "worth acting on: it marks the panels a human should look at, because "
        "at least one side is wrong there."))
    metrics.append(_metric(
        "OCR: credit/watermark pages detected", b_credit, c_credit, "neutral",
        f"Scored by the same regex on both sides. Claude additionally flagged "
        f"{c_flagged} panel(s) as credits with its own judgement, which the "
        "baseline cannot do at all.",
        {"claude_self_flagged": c_flagged}))

    # -------------------------------------------------- descriptions
    def _desc_stats(by):
        empty = sum(1 for p in shared if not (by[p].get("visual_description") or "").strip())
        generic = sum(1 for p in shared
                      if validator._GENERIC_DESC.match(
                          (by[p].get("visual_description") or "").strip() or "x"))
        texts = [(by[p].get("visual_description") or "").strip() for p in shared]
        # Near-duplicate descriptions are a real quality failure: a describer
        # that writes the same sentence for many panels has given the matcher
        # nothing to tell them apart with.
        dupes = len(texts) - len({t.lower() for t in texts if t})
        return empty, generic, dupes, _mean([len(t) for t in texts])

    b_e, b_g, b_d, b_dl = _desc_stats(b_by)
    c_e, c_g, c_d, c_dl = _desc_stats(c_by)
    metrics.append(_metric(
        "Descriptions: missing", b_e, c_e, "lower_better",
        "A panel with no description gave the matcher nothing to match on."))
    metrics.append(_metric(
        "Descriptions: generic (could fit any panel)", b_g, c_g, "lower_better",
        "Matched by the same pattern the checker uses."))
    metrics.append(_metric(
        "Descriptions: exact duplicates of another panel", b_d, c_d,
        "lower_better",
        "Two panels with identical descriptions are indistinguishable to any "
        "matcher built on them."))
    metrics.append(_metric(
        "Descriptions: mean length", b_dl, c_dl, "neutral",
        "Length is not quality. Reported so a wildly terser or more verbose "
        "describer is visible."))
    metrics.append(_metric(
        "Descriptions: agreement between the two (0-1)",
        _mean([_jaccard(b_by[p].get("visual_description"),
                        c_by[p].get("visual_description")) for p in shared]),
        _mean([_jaccard(b_by[p].get("visual_description"),
                        c_by[p].get("visual_description")) for p in shared]),
        "neutral",
        "Same figure both sides by definition. Low agreement marks the panels "
        "worth opening."))

    # --------------------------------------------------------- crops
    b_crops = _baseline_crops(pdir)
    c_crops = {d["panel_id"]: d.get("crop_bbox_norm") for d in c_descs}
    both = [p for p in shared if b_crops.get(p) and c_crops.get(p)]
    b_area = _mean([_area(b_crops[p]) for p in both])
    c_area = _mean([_area(c_crops[p]) for p in both])
    b_full = sum(1 for p in both if (_area(b_crops[p]) or 0) > 0.98)
    c_full = sum(1 for p in both if (_area(c_crops[p]) or 0) > 0.98)
    c_tiny = sum(1 for p in both if (_area(c_crops[p]) or 1) < 0.15)
    b_tiny = sum(1 for p in both if (_area(b_crops[p]) or 1) < 0.15)
    metrics.append(_metric(
        "Crops: mean fraction of the panel kept", b_area, c_area, "neutral",
        "Tighter is not automatically better — it is better only if the "
        "subject and any story text survived. Compare a few side by side.",
        {"panels_compared": len(both)}))
    metrics.append(_metric(
        "Crops: left at full panel", b_full, c_full, "neutral",
        "A crop step that never crops is doing nothing; one that always crops "
        "is probably over-eager."))
    metrics.append(_metric(
        "Crops: very small (<15% of the panel)", b_tiny, c_tiny,
        "lower_better",
        "A crop this tight usually means the subject was mislocated."))

    # ----------------------------------------------- matching & order
    def _placement_stats(rows):
        units = {}
        for r in rows:
            u = r["placement"]["unit"]
            if u is None:
                continue
            units.setdefault(u, []).append(r["n"])
        orphan = sum(1 for u, ns in units.items() if not ns)
        used = sum(len(ns) for ns in units.values())
        # Story order: the first panel of each unit must not go backwards.
        firsts = [min(ns) for _, ns in sorted(units.items()) if ns]
        inversions = sum(1 for a, b in zip(firsts, firsts[1:]) if b < a)
        return {"units": len(units), "orphan_units": orphan,
                "panels_used": used,
                "panels_unused": len(rows) - used,
                "mean_panels_per_unit": _mean([len(ns) for ns in units.values()]),
                "order_inversions": inversions}

    bs, cs = _placement_stats(b_rows), _placement_stats(c_rows)
    metrics.append(_metric(
        "Matching: narration units", bs["units"], cs["units"], "neutral",
        "The two wrote different scripts, so unit counts are not comparable "
        "as quality — shown for context."))
    metrics.append(_metric(
        "Matching: units with no panel", bs["orphan_units"], cs["orphan_units"],
        "lower_better", "A line with no panel is heard over someone else's."))
    metrics.append(_metric(
        "Matching: panels used", bs["panels_used"], cs["panels_used"],
        "neutral", "Chapters legitimately contain panels no line needs."))
    metrics.append(_metric(
        "Matching: mean panels per line", bs["mean_panels_per_unit"],
        cs["mean_panels_per_unit"], "neutral",
        "Very high means long holds on one line; very low means a rapid "
        "slideshow."))
    metrics.append(_metric(
        "Story order: backwards jumps", bs["order_inversions"],
        cs["order_inversions"], "lower_better",
        "A later line landing on an earlier panel makes the recap run "
        "backwards. This one is unambiguous."))

    # --------------------------------- checker findings (same instrument)
    def _by_cat(finds):
        out = {c: 0 for c in validator.CATEGORIES}
        for f in finds:
            out[f["category"]] = out.get(f["category"], 0) + 1
        return out

    b_cat, c_cat = _by_cat(b_find), _by_cat(c_find)
    metrics.append(_metric(
        "Checker: total findings", len(b_find), len(c_find), "lower_better",
        "The SAME rule pass run over both, so this is like for like."))
    for cat, label in validator.CATEGORIES.items():
        if not (b_cat.get(cat) or c_cat.get(cat)):
            continue
        metrics.append(_metric(
            f"Checker: {label.lower()}", b_cat.get(cat, 0), c_cat.get(cat, 0),
            "lower_better", "Found by the identical rule on both sides."))

    b_high = sum(1 for f in b_find if f["severity"] == "high")
    c_high = sum(1 for f in c_find if f["severity"] == "high")
    metrics.append(_metric(
        "Manual fixes likely needed (high-severity findings)",
        b_high, c_high, "lower_better",
        "The closest available proxy for 'how much would I have to fix by "
        "hand'. It counts defects the checker is confident about."))

    # ------------------------------------------------ board cleanliness
    b_clean = _pct(len(b_rows) - len({f["row"] for f in b_find}), len(b_rows))
    c_clean = _pct(len(c_rows) - len({f["row"] for f in c_find}), len(c_rows))
    metrics.append(_metric(
        "Board cleanliness (% rows with no finding)", b_clean, c_clean,
        "higher_better", "Rows the checker had nothing to say about."))

    # ------------------------------------------------------- scoring
    scored = [m for m in metrics if m["direction"] != "neutral"]
    wins_c = sum(1 for m in scored if m["winner"] == WIN_CLAUDE)
    wins_b = sum(1 for m in scored if m["winner"] == WIN_BASELINE)
    ties = sum(1 for m in scored if m["winner"] == WIN_TIE)

    # Panels where the two disagree most — the shortlist a human should open.
    disagree = sorted(
        ({"panel_id": p,
          "row": next((d["n"] for d in c_descs if d["panel_id"] == p), None),
          "ocr_agreement": round(_jaccard(b_by[p].get("ocr_text"),
                                          c_by[p].get("ocr_text")), 3),
          "desc_agreement": round(_jaccard(b_by[p].get("visual_description"),
                                           c_by[p].get("visual_description")), 3),
          "baseline_desc": (b_by[p].get("visual_description") or "")[:200],
          "claude_desc": (c_by[p].get("visual_description") or "")[:200]}
         for p in shared),
        key=lambda r: r["ocr_agreement"] + r["desc_agreement"])[:15]

    man = claude_pipeline.load_manifest(pdir) or {}
    return {
        "panels_compared": len(shared),
        "baseline_panels": len(b_descs),
        "claude_panels": len(c_descs),
        "metrics": metrics,
        "score": {"claude": wins_c, "baseline": wins_b, "ties": ties,
                  "scored_metrics": len(scored),
                  "neutral_metrics": len(metrics) - len(scored)},
        "most_disagreement": disagree,
        "claude_cost_usd": man.get("cost_usd"),
        "claude_calls": man.get("calls"),
        "claude_model": man.get("model"),
        "timing_is_proposed": True,
        "caveats": [
            "Both sides were given the SAME panel crops. Scraping and panel "
            "splitting are shared, so the only variable is the judgement calls.",
            "There is no hand-labelled ground truth for this chapter, so "
            "metrics marked 'neutral' report both numbers without declaring a "
            "winner. Only metrics with a direction that is true by "
            "construction are scored.",
            "Claude's timings are PROPOSED, not measured — this experiment "
            "generates no audio, while the production timeline is derived from "
            "real TTS audio. Timing numbers are not comparable like for like.",
            "The two wrote different scripts, so unit counts and panel usage "
            "differ for reasons that are not quality.",
            "The win count is a tally of narrow metrics, not an overall "
            "verdict. Read the rows, especially the disagreement shortlist.",
        ],
    }
