#!/usr/bin/env python3
"""Golden-fixture regression harness (A4).

Two modes:

  --check-script FILE   Zero-cost structural checks of an existing narration
                        script against the approved style contract.
  --live                Re-run narrate.generate_narration on the fixture
                        descriptions (REAL Gemini calls, ~$0.05) and run the
                        same checks on the fresh output, plus provenance
                        ordering checks when script.json provenance exists.

Rule (memory.md): any prompt change to narrate.py REQUIRES a fixture run and
a logged PASS/FAIL table before deploy.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, RECAP)
FIXTURE = os.path.join(HERE, "fixtures", "dungeon-odyssey-ch1")

# ---- style-contract checks (structural, zero API) -------------------------
BANNED = [
    (r'"[^"]{3,}"', 'quoted dialogue (contract: reported speech only)'),
    (r"\b(the panel|this panel|the image|the frame|the camera|on screen|"
     r"in the foreground|in the background of the panel)\b",
     'panel/camera language'),
    (r"\b(speed lines|sound effect|sfx|speech bubble|text box)\b",
     'comic-mechanics language'),
    (r"\bwe (see|watch|observe)\b", 'viewer language'),
]

def check_script(text, name):
    fails = []
    n_sent = len(re.findall(r"[.!?]", text))
    words = len(text.split())
    for pat, why in BANNED:
        hits = re.findall(pat, text, re.I)
        if hits:
            fails.append(f"{why}: {len(hits)}x e.g. {str(hits[0])[:60]!r}")
    if words < 150:
        fails.append(f"suspiciously short ({words} words) — empty/truncated output?")
    # past-tense heuristic: present-tense narration openers are contract breaks
    present = re.findall(r"\b(is|are|begins|stands|looks|says|sees)\b", text)
    ratio = len(present) / max(n_sent, 1)
    if ratio > 0.8:
        fails.append(f"present-tense ratio high ({ratio:.1f}/sentence) — contract is past tense")
    status = "PASS" if not fails else "FAIL"
    print(f"[{status}] {name}: {words} words, ~{n_sent} sentences")
    for f in fails:
        print(f"   - {f}")
    return not fails


def provenance_checks(scenes, panels):
    """scenes: [(scene_panels, text)] — verify ordering + coverage."""
    fails = []
    order = {p["panel_id"]: i for i, p in enumerate(panels)}
    last = -1
    for sp, _txt in scenes:
        idxs = [order.get(p["panel_id"], -1) for p in sp]
        if idxs and min(idxs) < last:
            fails.append(f"scene starting {sp[0]['panel_id']} out of chapter order")
        last = max(idxs + [last])
    covered = sum(len(sp) for sp, _ in scenes)
    print(f"[{'PASS' if not fails else 'FAIL'}] provenance: {len(scenes)} scenes, "
          f"{covered} panels covered, ordering "
          f"{'ok' if not fails else 'BROKEN'}")
    for f in fails:
        print("   -", f)
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-script")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    ok = True
    if args.check_script:
        ok &= check_script(open(args.check_script).read(), args.check_script)
    elif args.live:
        import matcher
        import narrate
        panels = json.load(open(os.path.join(FIXTURE, "descriptions.json")))
        panels = [p for p in panels if p.get("ok", True) and not matcher.is_junk_panel(p)]
        print(f"fixture: {len(panels)} non-junk panels")
        script, scenes = narrate.generate_narration(panels, verbose=False)
        out = os.path.join(HERE, "last_live_script.txt")
        open(out, "w").write(script)
        print(f"fresh script -> {out}")
        ok &= check_script(script, "live narrate output")
        ok &= provenance_checks(scenes, panels)
    else:
        # default: validate the approved fixtures themselves (sanity: the
        # contract must judge the approved scripts as passing)
        for f in ("approved_script.txt", "approved_sample_a.txt"):
            ok &= check_script(open(os.path.join(FIXTURE, f)).read(), f)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
