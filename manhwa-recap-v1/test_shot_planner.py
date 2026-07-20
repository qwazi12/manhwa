import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
# Add manhwa-recap-v1 to path
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "manhwa-recap-v1")))

import shot_planner

# Load existing project context
proj_dir = "/Users/kwasiyeboah/Desktop/manhwa/manhwa-recap-v1/review_ui/projects/the-ruler-of-darkness_1"
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
