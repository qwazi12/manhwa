#!/usr/bin/env python3
"""
Per-segment renderer + concatenator (the review-layer build).

Renders ONE clip per segment (a maximal same-panel run), then concatenates the
clips into the final video. This is the "Segment = render unit" architecture:

  - full build:      python render_segments.py
  - re-render one:   python render_segments.py --only 42     (then it re-concats)
  - just concat:     python render_segments.py --concat-only
  - first N (test):  python render_segments.py --limit 4

Why: a bad panel is a one-clip, ~15s fix instead of a ~15-min full re-render —
the foundation for the Stage-5 review UI. segments.json is the manifest (which
beats/panel/clip per segment) a UI would drive.

Inputs (env-overridable, same as build_composition.py):
  HF_BEATSHEET / HF_BEATS / HF_AUDIO_DIR  (default to the full-chapter fixture)

Pre-render gate (matcher.validate_beatsheet) runs first: a junk/blank/missing
panel aborts before any clip is rendered.
"""
import argparse
import json
import os
import subprocess
import sys

from segments import build_segments

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.abspath(os.path.join(HERE, ".."))
PROJ = os.path.abspath(os.path.join(HERE, "..", "..")) # ROOT
PANELS_SUBDIR = os.environ.get("HF_PANELS_DIR", "panel-split/review_crops")
DESCRIPTIONS_FILE = os.environ.get("HF_DESCRIPTIONS", "panel-describe/descriptions.json")
PANEL_DIR = os.path.abspath(os.path.join(PROJ, PANELS_SUBDIR))
WORK = os.environ.get("HF_WORKSPACE", os.path.join(HERE, "segments-workspace"))
CLIPS = os.path.join(WORK, "clips")
ASSETS = os.path.join(WORK, "assets")
W, H = 1920, 1080

BEATSHEET = os.environ.get("HF_BEATSHEET", "build_test/beatsheet_full.json")
BEATS = os.environ.get("HF_BEATS", "build_test/beats_full.json")
AUDIO_SUBDIR = os.environ.get("HF_AUDIO_DIR", "build_test/tts_full")


def ffprobe_dur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True).strip()
    return round(float(out), 3)


def copy(src, dst):
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(data)


def seg_html(seg, audio_dir):
    """Standalone HyperFrames composition for one segment: blurred blow-up
    background + aspect-preserved card + Ken Burns, with the segment's beat
    audio placed at within-segment offsets so narration timing is preserved."""
    pid = seg["panel_id"]
    src = f"assets/{pid}.png"
    dur = seg["dur"]
    z0, z1 = (1.0, 1.035) if seg["seg_index"] % 2 == 0 else (1.035, 1.0)
    audio_layers, tl = [], []

    has_crop = seg.get("crop_bbox") is not None and seg.get("scale_w") is not None
    if has_crop:
        ar = seg["crop_ar"]
        max_w, max_h = 0.46 * W, 0.90 * H
        if ar > max_w / max_h:
            w_px, h_px = max_w, max_w / ar
        else:
            h_px, w_px = max_h, max_h * ar
        
        card_html = f"""  <div class="clip card" id="card" data-start="0" data-duration="{dur}" data-track-index="1">
    <div class="crop-container" id="card_container" style="position:relative; overflow:hidden; width:{w_px:.1f}px; height:{h_px:.1f}px; border-radius:6px; background:#fff; box-shadow:0 30px 70px rgba(0,0,0,.38), 0 8px 20px rgba(0,0,0,.22);">
      <img class="cardimg" src="{src}" style="position:absolute; width:{seg["scale_w"]}%; height:{seg["scale_h"]}%; left:{seg["left"]}%; top:{seg["top"]}%; max-width:none; max-height:none;" alt="" />
    </div>
  </div>"""
        target_el = "#card_container"
    else:
        card_html = f"""  <div class="clip card" id="card" data-start="0" data-duration="{dur}" data-track-index="1">
    <img class="cardimg" src="{src}" alt="" />
  </div>"""
        target_el = "#card .cardimg"

    tl.append(f'  tl.fromTo("{target_el}", {{ scale: {z0} }}, '
              f'{{ scale: {z1}, duration: {dur}, ease: "none" }}, 0);')
    tl.append(f'  tl.fromTo("#bg", {{ scale: 1.15 }}, '
              f'{{ scale: 1.22, duration: {dur}, ease: "none" }}, 0);')
    tl.append('  tl.from("#card", { opacity: 0, scale: 0.94, duration: 0.4, '
              'ease: "power2.out" }, 0);')
    for b in seg["beats"]:
        a = os.path.join(audio_dir, f"beat_{b['index']:03d}.mp3")
        if not os.path.exists(a):
            continue
        off = round(b["start"] - seg["start"], 3)
        adur = ffprobe_dur(a)
        audio_layers.append(
            f'    <audio class="clip" id="a{b["index"]}" data-start="{off}" '
            f'data-duration="{adur}" data-track-index="9" data-volume="1" '
            f'src="assets/beat_{b["index"]:03d}.mp3"></audio>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8" />
<meta name="viewport" content="width={W}, height={H}" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden; background:#e8e6e3; }}
#root {{ position:relative; width:{W}px; height:{H}px; background:#e8e6e3; }}
#bg {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
  filter:blur(42px) saturate(.5) brightness(1.08); transform:scale(1.18); }}
#veil {{ position:absolute; inset:0; background:radial-gradient(ellipse at center,
  rgba(232,230,227,.25) 0%, rgba(232,230,227,.72) 100%); }}
.card {{ position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
  width:{W}px; height:{H}px; display:flex; align-items:center; justify-content:center; }}
.cardimg {{ max-width:46%; max-height:90%; width:auto; height:auto; object-fit:contain;
  border-radius:6px; background:#fff;
  box-shadow:0 30px 70px rgba(0,0,0,.38), 0 8px 20px rgba(0,0,0,.22); }}
</style></head>
<body><div id="root" data-composition-id="main" data-start="0"
  data-duration="{dur}" data-width="{W}" data-height="{H}">
  <img class="clip" id="bg" data-start="0" data-duration="{dur}" data-track-index="0" src="{src}" alt="" />
  <div id="veil"></div>
{card_html}
{os.linesep.join(audio_layers)}
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{os.linesep.join(tl)}
window.__timelines["main"] = tl;
</script></body></html>
"""


def ensure_project():
    """Minimal hyperframes project scaffolding shared by all segment renders."""
    os.makedirs(CLIPS, exist_ok=True)
    os.makedirs(ASSETS, exist_ok=True)
    hf = os.path.join(WORK, "hyperframes.json")
    if not os.path.exists(hf):
        json.dump({"name": "segments", "width": W, "height": H, "fps": 30},
                  open(hf, "w"), indent=2)


def render_segment(seg, audio_dir):
    """Render one segment to clips/seg_NNN.mp4 via the hyperframes CLI."""
    pid = seg["panel_id"]
    copy(os.path.join(PANEL_DIR, f"{pid}.png"), os.path.join(ASSETS, f"{pid}.png"))
    for b in seg["beats"]:
        a = os.path.join(audio_dir, f"beat_{b['index']:03d}.mp3")
        if os.path.exists(a):
            copy(a, os.path.join(ASSETS, f"beat_{b['index']:03d}.mp3"))
    open(os.path.join(WORK, "index.html"), "w").write(seg_html(seg, audio_dir))
    dst = os.path.join(CLIPS, f"seg_{seg['seg_index']:03d}.mp4")
    if os.path.exists(dst):
        os.remove(dst)
    subprocess.run(["npx", "hyperframes", "render", "-o", dst],
                   cwd=WORK, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst


def concat(segments, final_path):
    """Concatenate all segment clips in order into the final video."""
    listing = os.path.join(WORK, "concat.txt")
    with open(listing, "w") as f:
        for seg in segments:
            clip = os.path.join(WORK, seg["clip"])
            if not os.path.exists(clip):
                raise SystemExit(f"missing clip for segment {seg['seg_index']} "
                                 f"— render it first")
            f.write(f"file '{os.path.abspath(clip)}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing,
                    "-c", "copy", final_path], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Concatenated {len(segments)} clips -> {final_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, help="re-render just this segment index, then re-concat")
    ap.add_argument("--limit", type=int, help="render only the first N segments (test)")
    ap.add_argument("--concat-only", action="store_true", help="skip rendering, just concat existing clips")
    args = ap.parse_args()

    segments_path = os.path.join(WORK, "segments.json")
    if os.path.exists(segments_path):
        print(f"Loading segments directly from {segments_path}...")
        segments = json.load(open(segments_path))
    else:
        shots = json.load(open(os.path.join(RECAP, BEATSHEET)))
        panels = json.load(open(os.path.abspath(os.path.join(PROJ, DESCRIPTIONS_FILE))))
        for s in shots:
            s["panel_file"] = os.path.join(PANEL_DIR, f"{s['panel_id']}.png")

        # pre-render gate
        sys.path.insert(0, RECAP)
        import matcher
        for p in panels:
            if p.get("file") and not os.path.isabs(p["file"]):
                p["file"] = os.path.join(PANEL_DIR, p["file"])
        problems = matcher.validate_beatsheet(shots, panels)
        if problems:
            print(f"ABORT: {len(problems)} validation problem(s):")
            for p in problems[:20]:
                print("  -", p)
            raise SystemExit(1)

        segments = build_segments(shots)
        ensure_project()
        json.dump(segments, open(segments_path, "w"), indent=2)

    audio_dir = os.path.join(RECAP, AUDIO_SUBDIR)
    final = os.path.join(WORK, "final.mp4")

    if args.concat_only:
        concat(segments, final)
        return
    if args.only is not None:
        seg = next(s for s in segments if s["seg_index"] == args.only)
        print(f"Re-rendering segment {args.only} ({seg['panel_id']})...")
        render_segment(seg, audio_dir)
        concat(segments, final)
        return

    todo = segments[:args.limit] if args.limit else segments
    for i, seg in enumerate(todo, 1):
        render_segment(seg, audio_dir)
        print(f"[{i}/{len(todo)}] seg {seg['seg_index']:3} {seg['panel_id']} "
              f"({seg['dur']:.1f}s, {len(seg['beats'])} beat(s))")
    if not args.limit:
        concat(segments, final)


if __name__ == "__main__":
    main()
