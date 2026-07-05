#!/usr/bin/env python3
"""
Regression gate + baseline for the matcher. Run before trusting a render.

Re-runs the DP matcher on the full-chapter fixture, then:
  - hard-fails if validate_beatsheet finds any problem (junk/blank panel, missing
    image, timeline gap, unresolved panel) — the invariants that broke before,
  - compares quality metrics (distinct panels / max hold / junk / blank /
    backward jumps) against a committed baseline and flags regressions,
  - on first run (or with --update) writes the baseline.

Usage:
    python check_matcher.py            # check against committed baseline
    python check_matcher.py --update   # (re)write the baseline from current run

Exit code is non-zero on any hard failure or metric regression, so this can gate
CI or a pre-render hook.
"""
import json
import os
import sys

import matcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
BEATS = os.path.join(HERE, "build_test", "beats_full.json")
DESC = os.path.join(ROOT, "panel-describe", "descriptions.json")
BASELINE = os.path.join(HERE, "matcher_baseline.json")

# Metrics where a LOWER value is a regression (more is better).
_HIGHER_BETTER = {"distinct_panels"}
# Metrics where a HIGHER value is a regression (less is better).
_LOWER_BETTER = {"max_hold", "backward_jumps", "junk_in_output"}


# the actual panel PNGs live in panel-split/review_crops (descriptions.json only
# stores basenames); resolve there so the file-existence check is meaningful.
PANELS_DIR = os.path.join(ROOT, "panel-split", "review_crops")


def load_panels():
    panels = json.load(open(DESC))
    for p in panels:
        if p.get("file") and not os.path.isabs(p["file"]):
            p["file"] = os.path.join(PANELS_DIR, p["file"])
    return [p for p in panels
            if p.get("ok", True) and p.get("width") and p.get("height")]


def main():
    update = "--update" in sys.argv
    beats = json.load(open(BEATS))
    panels = load_panels()

    assignments, method = matcher.match_beats_to_panels(beats, panels)
    shots = matcher.build_timeline(beats, panels, assignments)

    problems = matcher.validate_beatsheet(shots, panels)
    metrics = matcher.beatsheet_metrics(shots, panels)
    metrics["method"] = method

    print(f"Matcher: {method}")
    print("Metrics:", json.dumps({k: metrics[k] for k in sorted(metrics)}, indent=None))

    failed = False
    # 1. hard invariants
    if problems:
        failed = True
        print(f"\n❌ {len(problems)} invariant violation(s):")
        for p in problems[:20]:
            print("   -", p)
    else:
        print("✅ Invariants OK: no junk/blank panels, all images present, no gaps.")

    # 2. metric regression vs baseline
    if update or not os.path.exists(BASELINE):
        json.dump(metrics, open(BASELINE, "w"), indent=2)
        print(f"\n📌 Wrote baseline -> {BASELINE}")
    else:
        base = json.load(open(BASELINE))
        regressions = []
        for k in _HIGHER_BETTER:
            if metrics.get(k, 0) < base.get(k, 0):
                regressions.append(f"{k}: {base[k]} -> {metrics[k]} (dropped)")
        for k in _LOWER_BETTER:
            if metrics.get(k, 0) > base.get(k, 0):
                regressions.append(f"{k}: {base[k]} -> {metrics[k]} (worsened)")
        if regressions:
            failed = True
            print(f"\n❌ {len(regressions)} metric regression(s) vs baseline:")
            for r in regressions:
                print("   -", r)
        else:
            print("✅ No metric regressions vs baseline.")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
