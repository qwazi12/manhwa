"""Composition-aware crop scoring (crop_score.py) — the gate that did not exist.

Before this, a crop was validated on GEOMETRY ALONE. Nothing asked whether the
box held the subject, sat on a speech bubble, or was mostly background, and
nothing ever compared it against simply using the whole panel — so any box
above the 12% area floor shipped.

What these protect:

1. The trigger no longer invites crops onto text. "bubble", "caption" and
   "text" were in production's _DETAIL_KEYWORDS, so a beat about someone
   SPEAKING asked for a crop on the word balloon.
2. Composition is measured, not assumed: bubble coverage, blank coverage, edge
   hugging, thin aspect, and whether the box landed on denser ground than the
   panel average.
3. Full frame WINS unless a crop earns its place by a real margin.
4. focus_confidence is never the gate. shot_planner.py:64 records why — the two
   worst boxes in the Martial Genius audit both carried 1.0.

Panels here are synthetic and built in int16 then clipped: writing noise
straight into a uint8 array wraps negatives to 255 and produces a fixture that
looks textured but is actually extreme noise.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def build_panel(path, bubble=True, figure=True):
    """A light page with a solid figure and, optionally, a white speech bubble
    holding dark text lines — the shape _detect_bubbles is built to find."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(0)
    a = np.full((600, 400), 245, np.int16)
    a += rng.integers(-6, 6, a.shape)
    if figure:
        a[200:520, 80:320] = 55
        a[230:300, 120:200] = 20
    if bubble:
        a[60:190, 70:330] = 252
        for y in range(80, 175, 14):
            a[y:y + 5, 90:310] = 15
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)
    return path


def build_art_panel(path):
    """Art filling the frame with a WHITE bubble island inside it — the case
    where the bubble detector actually fires."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(1)
    a = np.full((600, 400), 128, np.int16)
    a += rng.integers(-20, 20, a.shape)
    a[200:520, 80:320] = 60
    a[60:190, 70:330] = 255
    for y in range(80, 175, 14):
        a[y:y + 5, 90:310] = 15
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)
    return path


def main():
    import crop_score as CS
    tmp = tempfile.mkdtemp(prefix="cropscore_")

    # ============================ the trigger no longer invites text crops
    check("a beat about a face still asks for a crop",
          CS.should_crop_close("his face twisted in anger"))
    check("a beat about a weapon still asks for a crop",
          CS.should_crop_close("the blade shattered against the shield"))
    check("a beat that only REPORTS SPEECH does not",
          not CS.should_crop_close("he demanded to know who the stranger was"))
    check("...because the words are already in the narration audio",
          CS.beat_is_speech_only("she replied that the gate was closed"))
    check("'bubble' alone no longer triggers a detail crop",
          not CS.should_crop_close("a bubble floats above him"))
    check("'caption' alone no longer triggers a detail crop",
          not CS.should_crop_close("a caption sits in the corner"))
    check("but a beat about READING something does",
          CS.should_crop_close("the sign read that the gate was sealed"))
    check("...and that is not treated as speech-only",
          not CS.beat_is_speech_only("the letter read that his brother had died"))

    # ============================ measurement
    p = build_panel(os.path.join(tmp, "panel.png"))
    m = CS.panel_metrics(p)
    check("a panel can be measured", m is not None)
    check("background is taken from production's estimator, not a mode",
          m["bg"] in (0, 255))

    figure_box = [0.18, 0.32, 0.82, 0.88]
    fm = CS.measure(figure_box, m)
    check("a crop on the subject reads as denser than the panel average",
          fm["efficiency"] > 1.0)
    blank_box = [0.05, 0.02, 0.95, 0.18]
    bm = CS.measure(blank_box, m)
    check("a crop on empty page reads as emptier than average",
          bm["efficiency"] < fm["efficiency"])

    # ============================ full frame wins unless the crop earns it
    box, d = CS.choose_crop(figure_box, p, "he raised his blade", metrics=m)
    check("a strong crop survives the gate", d["used"] == "crop")
    check("...and beats the full-frame baseline it was compared against",
          d["crop_score"] > d["full_score"])
    check("...and the decision explains itself", bool(d["why"]))

    for label, bad in [
            ("a thin sliver", [0.45, 0.10, 0.52, 0.95]),
            ("a corner scrap", [0.0, 0.0, 0.22, 0.18]),
            ("an empty band", [0.05, 0.02, 0.95, 0.14])]:
        _, dd = CS.choose_crop(bad, p, "he raised his blade", metrics=m)
        check(f"{label} is downgraded to full frame", dd["used"] == "full")
        check(f"...and {label} says why", bool(dd["why"]))

    # A crop that is merely TIED with the whole panel is not worth the risk.
    check("a crop must beat full frame by a real margin, not merely tie",
          CS.CROP_MARGIN > 0)

    # ============================ speech bubbles
    ap = build_art_panel(os.path.join(tmp, "art.png"))
    am = CS.panel_metrics(ap)
    check("the speech-bubble detector fires on a bubble inside art",
          len(am["bubbles"]) >= 1 and am["has_text_map"])
    bubble_box = [0.15, 0.08, 0.85, 0.33]
    bmm = CS.measure(bubble_box, am)
    check("bubble coverage is measured, not guessed",
          bmm["text_coverage"] > 0.5)
    _, bd = CS.choose_crop(bubble_box, ap,
                           "he demanded to know who she was", metrics=am)
    check("a crop onto a speech bubble is refused for a speech beat",
          bd["used"] == "full")
    check("...naming the bubble as the reason",
          "bubble" in bd["why"] or "art" in bd["why"])

    # ============================ confidence is NEVER the gate
    import inspect
    src = inspect.getsource(CS)
    check("crop_score never reads a confidence field",
          "focus_confidence" not in src.replace(
              "focus_confidence is NOT usable", "").replace(
              "`focus_confidence`", ""))
    strong = CS.choose_crop(figure_box, p, "he raised his blade", metrics=m)[1]
    weak = CS.choose_crop([0.45, 0.10, 0.52, 0.95], p, "he raised his blade",
                          metrics=m)[1]
    check("a bad box is refused no matter how confident its author was",
          weak["used"] == "full" and strong["used"] == "crop")

    # ============================ production's gate delegates here
    import shot_planner
    check("production's trigger delegates to the shared gate",
          not shot_planner.should_crop_close(
              "he demanded to know who the stranger was"))
    check("...and still fires on a real detail beat",
          shot_planner.should_crop_close("his face twisted in anger"))
    shot = {"crop_bbox_norm": [0.45, 0.10, 0.52, 0.95],
            "beat_text": "he raised his blade", "focus_confidence": 1.0}
    shot_planner.gate_crop(shot, p)
    check("production downgrades a bad box even at confidence 1.0",
          shot["crop_bbox_norm"] == [0.0, 0.0, 1.0, 1.0])
    check("...recording that it was downgraded and why",
          shot.get("crop_downgraded") and shot.get("crop_downgrade_reason"))
    keep = {"crop_bbox_norm": figure_box, "beat_text": "he raised his blade"}
    shot_planner.gate_crop(keep, p)
    check("...and leaves a good box alone",
          keep["crop_bbox_norm"] == figure_box)

    # A panel that cannot be read must never produce a guessed crop.
    _, nd = CS.choose_crop(figure_box, os.path.join(tmp, "missing.png"),
                           "x")
    check("an unreadable panel falls back to full frame, never a guess",
          nd["used"] == "full")

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
