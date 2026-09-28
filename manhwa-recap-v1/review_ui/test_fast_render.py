"""RENDERER=ffmpeg (hyperframes/fast_render.py) — the browser-free renderer.

Pins what the export and the viewer depend on:
  * stream parameters identical to the HyperFrames clips (the export
    stream-copies a MIX of clips from both renderers, so any drift breaks it),
  * exact length (ceil(dur*30) frames) and narration placed at its offset,
  * the same "audio does not fit" refusal as the HyperFrames path,
  * all three card shapes (normal / planned sub-crop / tall scroll-pan) draw,
    the entrance starts invisible, a failed render leaves no half clip,
  * when the hyperframes CLI is present: frame-level parity (SSIM) with it.

Run: python3 test_fast_render.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.join(HERE, "..")
sys.path[:0] = [HERE, RECAP, os.path.join(RECAP, "hyperframes")]

import numpy as np
import cv2
import render_segments as rs
import fast_render as fr

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def _tone(path, secs, freq=440):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:sample_rate=24000:duration={secs}",
                    "-ac", "1", "-q:a", "4", path], check=True)


def _panel(path, w, h, seed):
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 255, np.uint8)
    for _ in range(12):   # coloured blocks so blur/scale/crop are visible
        x, y = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(20, w // 3)), y + int(rng.integers(20, h // 3))),
                      tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    cv2.imwrite(path, img)


def _probe(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,profile,pix_fmt,r_frame_rate,time_base,sample_rate,channels,width,height",
         "-of", "json", path], text=True)
    return json.loads(out)["streams"]


def _dur(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True))


def _frame(path, t):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path,
                                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
    return np.frombuffer(raw, np.uint8).reshape(1080, 1920, 3)


def main():
    tmp = tempfile.mkdtemp(prefix="fastr_")
    crops, audio = os.path.join(tmp, "crops"), os.path.join(tmp, "audio")
    os.makedirs(crops); os.makedirs(audio)
    _panel(os.path.join(crops, "wide.png"), 900, 600, 1)
    _panel(os.path.join(crops, "tall.png"), 800, 2600, 2)
    _panel(os.path.join(crops, "port.png"), 700, 1000, 3)
    _tone(os.path.join(audio, "beat_000.mp3"), 2.0)
    _tone(os.path.join(audio, "beat_001.mp3"), 1.5, 660)
    _tone(os.path.join(audio, "beat_002.mp3"), 5.0)

    rs.RENDERER = "ffmpeg"
    rs.WORK, rs.CLIPS, rs.ASSETS = tmp, os.path.join(tmp, "clips"), os.path.join(tmp, "assets")
    rs.PANEL_DIR = crops

    def seg(si, pid, dur, beats, crop=None):
        return {"seg_index": si, "panel_id": pid, "panel_file": os.path.join(crops, pid + ".png"),
                "start": 10.0, "end": 10.0 + dur, "dur": dur, "crop_bbox_norm": crop,
                "beats": [{"index": i, "start": 10.0 + off, "end": 10.0 + off + 1} for i, off in beats]}

    normal = seg(0, "wide", 4.2, [(0, 0.5), (1, 2.6)])
    cropped = seg(1, "port", 3.0, [(0, 0.0)], crop=[0.1, 0.2, 0.7, 0.6])
    tall = seg(2, "tall", 3.0, [(0, 0.25)])
    silent = seg(3, "wide", 2.0, [])

    kinds = {}
    for s_ in (normal, cropped, tall, silent):
        src = rs._panel_src(s_)
        c, _h, t_, _w, _hh = rs.card_regime(s_, src)
        kinds[s_["seg_index"]] = "tall" if t_ else ("crop" if c else "normal")
    check("the three card shapes are routed as seg_html routes them",
          kinds == {0: "normal", 1: "crop", 2: "tall", 3: "normal"})

    t0 = time.time()
    outs = {s_["seg_index"]: rs.render_segment(s_, audio) for s_ in (normal, cropped, tall, silent)}
    elapsed = time.time() - t0
    check("all four clips render", all(os.path.exists(p) for p in outs.values()))
    check("no half-written .part file is left behind",
          not [f for f in os.listdir(rs.CLIPS) if f.endswith(".part.mp4")])

    want_v = {"codec_name": "h264", "profile": "High", "pix_fmt": "yuv420p",
              "r_frame_rate": "30/1", "time_base": "1/15360", "width": 1920, "height": 1080}
    want_a = {"codec_name": "aac", "profile": "LC", "sample_rate": "48000", "channels": 2}
    ok_streams = True
    for p in outs.values():
        v, a = _probe(p)
        ok_streams &= all(v.get(k) == x for k, x in want_v.items())
        ok_streams &= all(a.get(k) == x for k, x in want_a.items())
    check("stream params match the HyperFrames clips (export stream-copies both)", ok_streams)

    check("clip length is exactly ceil(dur*30) frames",
          all(abs(_dur(outs[s_["seg_index"]]) - (-(-s_["dur"] * 30 // 1)) / 30) < 0.03
              for s_ in (normal, cropped, tall, silent)))

    # narration lands at its offset: the 660Hz second line starts at +2.6s
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", outs[0], "-f", "s16le",
                                   "-ac", "1", "-ar", "8000", "-"])
    pcm = np.abs(np.frombuffer(raw, np.int16).astype(np.float32))
    onset = int(np.argmax(pcm > 0.1 * pcm.max())) / 8000
    check("the first line starts at its offset (0.5s)", abs(onset - 0.5) < 0.05)
    w = lambda a, b: pcm[int(a * 8000):int(b * 8000)].mean()
    check("silence between lines, sound during each line",
          w(0.6, 2.4) > 10 * max(w(2.52, 2.58), 1) and w(2.7, 4.0) > 10 * max(w(2.52, 2.58), 1))
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", outs[3], "-f", "s16le", "-"])
    check("a silent hold renders with a silent track",
          np.abs(np.frombuffer(raw, np.int16)).max() == 0)

    # entrance: the card starts invisible, is fully in by 0.4s
    f0, f_mid = _frame(outs[0], 0), _frame(outs[0], 2.0)
    centre = lambda f: f[440:640, 860:1060].astype(np.float32)
    bg_only = f0[440:640, 860:1060].std()
    check("frame 0 shows no card (opacity starts at 0)",
          np.abs(centre(f0) - cv2.GaussianBlur(centre(f0), (0, 0), 8)).mean() < 6)
    check("mid-clip shows the card over the background",
          np.abs(centre(f_mid) - centre(f0)).mean() > 10)
    ft0, ft1 = _frame(outs[2], 0.5), _frame(outs[2], 2.8)
    check("a tall strip scroll-pans (card content moves between frames)",
          np.abs(ft0[100:980, 700:1220].astype(np.float32) - ft1[100:980, 700:1220]).mean() > 8)

    # the same refusal as the HyperFrames path
    too_long = seg(4, "wide", 3.0, [(2, 0.0)])          # 5.0s of audio, 3.0s window
    try:
        rs.render_segment(too_long, audio); refused = ""
    except RuntimeError as e:
        refused = str(e)
    check("audio that does not fit is refused with the renderer's message",
          "does not fit its 3.000s window" in refused and
          not os.path.exists(os.path.join(rs.CLIPS, "seg_004.mp4")))

    check("RENDERER only accepts the two known values",
          "RENDERER must be" in open(rs.__file__).read())

    # parity with the HyperFrames CLI, when it is installed (skipped in CI
    # images without node/chrome — the checks above still pin the contract)
    if os.path.exists(os.path.join(RECAP, "hyperframes", "node_modules", ".bin", "hyperframes")):
        rs.RENDERER = "hyperframes"
        rs.ensure_project()
        hf_clip = rs.render_segment(dict(normal, seg_index=10), audio)
        rs.RENDERER = "ffmpeg"
        ff_clip = rs.render_segment(dict(normal, seg_index=11), audio)
        log = os.path.join(tmp, "ssim.log")
        subprocess.run(["ffmpeg", "-v", "error", "-i", hf_clip, "-i", ff_clip, "-lavfi",
                        f"[0:v][1:v]ssim=stats_file={log}", "-f", "null", "-"], check=True)
        vals = [float(re.search(r"All:([0-9.]+)", l).group(1)) for l in open(log)]
        check(f"frame parity with HyperFrames on every frame (min SSIM {min(vals):.3f} >= 0.95)",
              min(vals) >= 0.95)
    else:
        print("SKIP parity check: hyperframes CLI not installed here")

    print(f"(4 clips rendered in {elapsed:.1f}s)")
    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print(f"\n{n}/{len(R)} passed")
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
