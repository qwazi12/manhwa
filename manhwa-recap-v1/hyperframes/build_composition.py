#!/usr/bin/env python3
"""
Generate a HyperFrames composition (index.html) from the matcher's beatsheet.

Recreates the reference-video look, per the Session-4 reverse-engineering:
  - each panel is a floating, aspect-preserved CARD with a soft drop shadow
  - behind it, a blurred + desaturated blow-up of the SAME artwork fills 1920x1080
  - a slow Ken Burns zoom (~3%) rides the card for the shot's duration
  - narration is the per-beat TTS clips placed at each beat's real start time
  - NO burned-in subtitles (the reference has none)

Inputs (relative to manhwa-recap-v1/):
  build_test/beatsheet_gemini.json  — shots (start/end/panel_id/held/beat_text)
  build_test/beats 2.json           — beats (real audio start times)
Assets already copied into my-video/assets/{panels,audio}/.
"""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def audio_dur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True).strip()
    return round(float(out), 3)
PROJ = os.path.join(HERE, "my-video")
RECAP = os.path.abspath(os.path.join(HERE, ".."))

shots = json.load(open(os.path.join(RECAP, "build_test/beatsheet_gemini.json")))
beats = json.load(open(os.path.join(RECAP, "build_test/beats 2.json")))

# ---- copy assets so a fresh checkout is reproducible ----------------------
import shutil
PANEL_SRC = os.path.abspath(os.path.join(RECAP, "..", "panel-split", "review_crops"))
AUDIO_SRC = os.path.join(RECAP, "build_test", "tts 2")
os.makedirs(os.path.join(PROJ, "assets", "panels"), exist_ok=True)
os.makedirs(os.path.join(PROJ, "assets", "audio"), exist_ok=True)
for pid in sorted({s["panel_id"] for s in shots}):
    shutil.copy(os.path.join(PANEL_SRC, f"{pid}.png"),
                os.path.join(PROJ, "assets", "panels", f"{pid}.png"))
for i in range(len(beats)):
    a = os.path.join(AUDIO_SRC, f"beat_{i:03d}.mp3")
    if os.path.exists(a):
        shutil.copy(a, os.path.join(PROJ, "assets", "audio", f"beat_{i:03d}.mp3"))

W, H = 1920, 1080
total = max(s["end"] for s in shots)

# ---- build per-shot HTML (background blow-up + foreground card) ------------
bg_layers, card_layers, tl_lines = [], [], []
for i, s in enumerate(shots):
    start = round(float(s["start"]), 3)
    end = round(float(s["end"]), 3)
    dur = round(end - start, 3)
    if dur <= 0:
        continue
    pid = s["panel_id"]
    src = f"assets/panels/{pid}.png"
    held = s["held"]
    # background: same art, scaled to cover, heavily blurred + desaturated (paper feel)
    bg_layers.append(
        f'    <img class="clip bg" data-start="{start}" data-duration="{dur}" '
        f'data-track-index="0" id="bg{i}" src="{src}" alt="" />')
    # foreground card: aspect-preserved (object-fit contain), soft drop shadow
    card_layers.append(
        f'    <div class="clip card" data-start="{start}" data-duration="{dur}" '
        f'data-track-index="1" id="card{i}">\n'
        f'      <img class="cardimg" src="{src}" alt="" />\n'
        f'    </div>')
    # Ken Burns: zoom the card image over the shot. Alternate in/out so
    # consecutive HELD shots on the same panel keep drifting (no reset jerk).
    z0, z1 = (1.0, 1.035) if i % 2 == 0 else (1.035, 1.0)
    tl_lines.append(
        f'  tl.fromTo("#card{i} .cardimg", '
        f'{{ scale: {z0} }}, {{ scale: {z1}, duration: {dur}, ease: "none" }}, {start});')
    # background gets a gentle counter-zoom for depth
    tl_lines.append(
        f'  tl.fromTo("#bg{i}", '
        f'{{ scale: 1.15 }}, {{ scale: 1.22, duration: {dur}, ease: "none" }}, {start});')
    # entrance: card fades/scales in on a real advance (not on a hold)
    if not held:
        tl_lines.append(
            f'  tl.from("#card{i}", '
            f'{{ opacity: 0, scale: 0.94, duration: 0.45, ease: "power2.out" }}, {start});')

# ---- audio tracks: one per beat at its real start, its own natural duration
audio_layers = []
for i, b in enumerate(beats):
    astart = round(float(b["start"]), 3)
    src = f"assets/audio/beat_{i:03d}.mp3"
    apath = os.path.join(PROJ, src)
    if not os.path.exists(apath):
        continue
    adur = audio_dur(apath)
    audio_layers.append(
        f'    <audio class="clip" id="a{i}" data-start="{astart}" data-duration="{adur}" '
        f'data-track-index="9" data-volume="1" src="{src}"></audio>')

html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: #e8e6e3; }}
      #root {{ position: relative; width: {W}px; height: {H}px; background: #e8e6e3; }}
      /* blurred, desaturated blow-up of the artwork itself */
      .bg {{
        position: absolute; inset: 0; width: 100%; height: 100%;
        object-fit: cover; filter: blur(42px) saturate(0.5) brightness(1.08);
        transform: scale(1.18); transform-origin: center;
      }}
      /* soft paper vignette over the background for the reference's flat feel */
      #veil {{ position: absolute; inset: 0; background:
        radial-gradient(ellipse at center, rgba(232,230,227,0.25) 0%, rgba(232,230,227,0.72) 100%); }}
      /* floating aspect-preserved card with soft drop shadow */
      .card {{
        position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
        width: {W}px; height: {H}px; display: flex; align-items: center; justify-content: center;
      }}
      .cardimg {{
        max-width: 46%; max-height: 90%; width: auto; height: auto;
        object-fit: contain; border-radius: 6px; background: #fff;
        box-shadow: 0 30px 70px rgba(0,0,0,0.38), 0 8px 20px rgba(0,0,0,0.22);
        transform-origin: center;
      }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0"
         data-duration="{round(total,3)}" data-width="{W}" data-height="{H}">
{os.linesep.join(bg_layers)}
    <div id="veil"></div>
{os.linesep.join(card_layers)}
{os.linesep.join(audio_layers)}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{os.linesep.join(tl_lines)}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""

out = os.path.join(PROJ, "index.html")
open(out, "w").write(html)
print(f"Wrote {out}")
print(f"{len(shots)} shots, {len(audio_layers)} audio tracks, total {total:.2f}s")
