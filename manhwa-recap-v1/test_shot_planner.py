import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
# This file already lives in manhwa-recap-v1; the old "up one, back down"
# insert only resolved by accident and broke from any other checkout.
sys.path.insert(0, HERE)

import shot_planner

# This pointed at an absolute path inside the iCloud Desktop copy, which
# CLAUDE.md says not to rely on — so the script failed for everyone working in
# the real clone. Resolve relative to this file, and take a project name on the
# command line, defaulting to whichever project is actually present.
PROJECTS = os.path.join(HERE, "review_ui", "projects")


def _pick_project():
    if len(sys.argv) > 1:
        return os.path.join(PROJECTS, sys.argv[1])
    try:
        names = sorted(n for n in os.listdir(PROJECTS)
                       if os.path.exists(os.path.join(PROJECTS, n, "segments.json")))
    except OSError:
        names = []
    if not names:
        print(f"No project with a segments.json under {PROJECTS}")
        print("Usage: python3 test_shot_planner.py [project-name]")
        sys.exit(1)
    return os.path.join(PROJECTS, names[0])


proj_dir = _pick_project()
print(f"Project: {os.path.basename(proj_dir)}")
desc_path = os.path.join(proj_dir, "descriptions.json")
crops_dir = os.path.join(proj_dir, "crops")
segments_path = os.path.join(proj_dir, "segments.json")

if not os.path.exists(segments_path):
    print(f"Error: segments.json not found at {segments_path}")
    sys.exit(1)

# Reconstruct shots/beats from segments
segs = json.load(open(segments_path))
shots = []
for seg in segs:
    for b in seg["beats"]:
        shots.append({
            "index": b["index"],
            "beat_text": b["text"],
            "panel_id": seg["panel_id"],
            "panel_file": os.path.join(crops_dir, f"{seg['panel_id']}.png"),
            "width": 1000,   # placeholder
            "height": 1000,  # placeholder
        })

print(f"Loaded {len(shots)} beats from segments.")

# Take a slice of the first 8 beats
test_slice = shots[:8]
print("\n--- Running shot planner on first 8 beats ---")
planned = shot_planner.plan_shots(test_slice, desc_path, crops_dir)

# Print results
for s in planned:
    print(f"\nBeat #{s['index']} matched to {s['panel_id']}")
    print(f"Text: '{s['beat_text']}'")
    print(f"Framing Mode: {s.get('framing_mode')} | Reason: {s.get('focus_reason')}")
    print(f"Crop Bbox: {s.get('crop_bbox')}")
    print(f"CSS styling: width={s.get('scale_w')}%, height={s.get('scale_h')}%, left={s.get('left')}%, top={s.get('top')}%")
