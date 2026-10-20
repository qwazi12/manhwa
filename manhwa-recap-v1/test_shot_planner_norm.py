import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "manhwa-recap-v1")))

import shot_planner

# Simulate some test shots/beats
test_shots = [
    {
        "index": 0,
        "beat_text": "The dark lord's eyes flared with intense rage.",
        "panel_id": "page_001_panel_001",
        "width": 800,
        "height": 600
    },
    {
        "index": 1,
        "beat_text": "A massive stone castle stood under the full moon.",
        "panel_id": "page_001_panel_002",
        "width": 800,
        "height": 600
    },
    {
        "index": 2,
        "beat_text": "He gripped the sword hilt tightly with his left hand.",
        "panel_id": "page_001_panel_001",  # Same panel, details beat
        "width": 800,
        "height": 600
    },
    {
        "index": 3,
        "beat_text": "The wind howled through the barren valley.",
        "panel_id": "page_002_shot_01",     # Sub-shot panel
        "width": 800,
        "height": 1000
    }
]

print("--- Running should_crop_close checks ---")
for s in test_shots:
    print(f"Beat #{s['index']}: '{s['beat_text']}' -> should_crop_close: {shot_planner.should_crop_close(s['beat_text'])}")

# Mock describe and crops paths
desc_path = "/tmp/dummy_descriptions.json"
crops_dir = "/tmp"

# Create dummy panel images so file exists check passes for testing
for pid in ["page_001_panel_001", "page_001_panel_002", "page_002_shot_01"]:
    with open(f"/tmp/{pid}.png", "w") as f:
        f.write("dummy")

# Run hybrid planner with dummy/no API key to verify routing logic
print("\n--- Running hybrid planner routing (no API key fallback check) ---")
planned = shot_planner.plan_shots(test_shots, desc_path, crops_dir, api_key=None)

for s in planned:
    print(f"\nBeat #{s['index']} matched to {s['panel_id']}")
    print(f"  Focus Source: {s.get('focus_source')}")
    print(f"  Focus Reason: {s.get('focus_reason')}")
    print(f"  Crop Bbox Norm: {s.get('crop_bbox_norm')}")
    
    # Calculate derived layout
    layout = shot_planner.get_crop_layout(s["crop_bbox_norm"], s["width"], s["height"])
    print(f"  Derived layout: w={layout['w']}px, h={layout['h']}px, scale_w={layout['scale_w']}%, left={layout['left']}%")

# Clean up dummy files
for pid in ["page_001_panel_001", "page_001_panel_002", "page_002_shot_01"]:
    try:
        os.remove(f"/tmp/{pid}.png")
    except Exception:
        pass
