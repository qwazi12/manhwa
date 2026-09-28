"""Browser-free segment renderer (RENDERER=ffmpeg).

Reproduces render_segments.seg_html()'s look — blurred cover background +
veil, a framed card (normal / planned sub-crop / tall scroll-pan) with a soft
two-layer shadow, a 0.4s fade-and-grow entrance, Ken Burns — without a
headless browser. The HyperFrames path screenshots Chrome once per frame,
which is where ~all of its render time goes; here the static layers are built
ONCE per clip and each frame is a couple of sub-pixel affine warps and a
blend, streamed as raw frames into ffmpeg.

Output matches the HyperFrames clips' stream parameters exactly (h264 High
yuv420p 30fps, timebase 1/15360, AAC-LC 48k stereo) so the export can keep
stream-copying a mix of clips from both renderers.

Motion values mirror seg_html's GSAP timeline one-for-one; if you change one,
change the other (test_fast_render.py compares the two frame by frame).
"""
import math
import os
import subprocess

import cv2
import numpy as np

W, H, FPS = 1920, 1080, 30
ROOT_BGR = (227, 230, 232)           # #e8e6e3 in BGR
VEIL_BGR = np.array(ROOT_BGR, np.float32)
RADIUS = 6                           # card border-radius (px)
# box-shadow: 0 30px 70px rgba(0,0,0,.38), 0 8px 20px rgba(0,0,0,.22)
# (a CSS blur radius B is a Gaussian with sigma B/2)
SHADOWS = ((30, 35.0, 0.38), (8, 10.0, 0.22))
PAD = 150                            # sprite margin that holds the shadow
ENTRANCE = 0.4                       # tl.from("#card", {opacity:0, scale:.94})


def _read_bgr(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"cannot read panel image {path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:            # transparent PNG -> on the card's white
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3] * a + 255.0 * (1 - a)).astype(np.uint8)
    return img


def _background(img):
    """#bg: object-fit:cover, filter blur(42px) saturate(.5) brightness(1.08).
    Blurred at quarter resolution (sigma scaled to match) — visually
    identical at this blur strength and ~16x cheaper."""
    ph, pw = img.shape[:2]
    s = max(W / pw, H / ph)
    rw, rh = max(W, round(pw * s)), max(H, round(ph * s))
    big = cv2.resize(img, (rw, rh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    x0, y0 = (rw - W) // 2, (rh - H) // 2
    cover = big[y0:y0 + H, x0:x0 + W]
    small = cv2.resize(cover, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 42 / 4, borderType=cv2.BORDER_REFLECT)
    bg = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    # CSS saturate(s) matrix (Filter Effects spec), in BGR order
    sat = 0.5
    r, g, b = bg[:, :, 2], bg[:, :, 1], bg[:, :, 0]
    nr = (0.213 + 0.787 * sat) * r + (0.715 - 0.715 * sat) * g + (0.072 - 0.072 * sat) * b
    ng = (0.213 - 0.213 * sat) * r + (0.715 + 0.285 * sat) * g + (0.072 - 0.072 * sat) * b
    nb = (0.213 - 0.213 * sat) * r + (0.715 - 0.715 * sat) * g + (0.072 + 0.928 * sat) * b
    out = np.stack([nb, ng, nr], axis=2) * 1.08
    return np.clip(out, 0, 255).astype(np.uint8)


def _veil():
    """#veil: radial-gradient(ellipse at center, .25 -> .72) of #e8e6e3.
    'ellipse' defaults to farthest-corner, whose radii are sqrt(2)*half-size."""
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    rx, ry = math.sqrt(2) * W / 2, math.sqrt(2) * H / 2
    d = np.sqrt(((xs + 0.5 - W / 2) / rx) ** 2 + ((ys + 0.5 - H / 2) / ry) ** 2)
    a = 0.25 + 0.47 * np.clip(d, 0, 1)
    return (1.0 - a).astype(np.float32), a.astype(np.float32)


_VEIL = None


def _rounded_mask(w, h, r=RADIUS):
    m = np.zeros((h, w), np.uint8)
    cv2.rectangle(m, (r, 0), (w - 1 - r, h - 1), 255, -1)
    cv2.rectangle(m, (0, r), (w - 1, h - 1 - r), 255, -1)
    for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
        cv2.circle(m, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
    return m.astype(np.float32) / 255.0


def _shadow(w, h):
    """Premultiplied-black shadow alpha for a w x h card, on the padded sprite."""
    sw, sh = w + 2 * PAD, h + 2 * PAD
    mask = np.zeros((sh, sw), np.float32)
    mask[PAD:PAD + h, PAD:PAD + w] = _rounded_mask(w, h)
    total = np.zeros_like(mask)
    for dy, sigma, alpha in SHADOWS:
        m = np.zeros_like(mask)
        m[dy:, :] = mask[:sh - dy, :]
        s = cv2.GaussianBlur(m, (0, 0), sigma) * alpha
        total = total + s * (1 - total)        # later shadow paints over earlier
    return total


class _Sprite:
    """A card on a padded canvas: straight BGR colour (black outside the card,
    which is also the shadow's colour) + alpha = card over shadow."""

    def __init__(self, w, h, content=None):
        self.w, self.h = w, h
        self.card_a = _rounded_mask(w, h)
        shadow = _shadow(w, h)
        sw, sh = w + 2 * PAD, h + 2 * PAD
        a = np.zeros((sh, sw), np.float32)
        a[PAD:PAD + h, PAD:PAD + w] = self.card_a
        self.alpha = a + shadow * (1 - a)
        self.color = np.zeros((sh, sw, 3), np.uint8)
        self.color[PAD:PAD + h, PAD:PAD + w] = 255 if content is None else content


def _blit(frame, color, alpha, scale, opacity, offset=0.0):
    """Composite a sprite onto frame (uint8, in place), scaled about the frame
    centre with the sprite's (padded) centre on the frame centre — where both
    the entrance tween (on the full-frame #card) and Ken Burns (on the card,
    also centred) put their transform origin. `offset` shifts a sub-layer that
    starts `offset` px inside the padded canvas (the tall strip's content)."""
    if opacity <= 0:
        return
    sh, sw = alpha.shape
    full_w, full_h = sw + 2 * offset, sh + 2 * offset
    ox = W / 2 - scale * full_w / 2 + scale * offset
    oy = H / 2 - scale * full_h / 2 + scale * offset
    x0, y0 = max(0, int(math.floor(ox))), max(0, int(math.floor(oy)))
    x1 = min(W, int(math.ceil(ox + scale * sw)))
    y1 = min(H, int(math.ceil(oy + scale * sh)))
    if x1 <= x0 or y1 <= y0:
        return
    M = np.float32([[scale, 0, ox - x0], [0, scale, oy - y0]])
    size = (x1 - x0, y1 - y0)
    c = cv2.warpAffine(color, M, size, flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    a = cv2.warpAffine(alpha, M, size, flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if opacity < 1:
        a *= opacity
    roi = frame[y0:y1, x0:x1]
    frame[y0:y1, x0:x1] = cv2.blendLinear(roi, c, 1.0 - a, a)


def _ease_out2(p):
    """GSAP "power2.out" — which is CUBIC (GSAP's power1 is quadratic)."""
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def render_frames(img, seg, crop, tall):
    """Yield BGR uint8 frames for one segment. Pure function of its inputs,
    so it can be tested without ffmpeg."""
    global _VEIL
    if _VEIL is None:
        inv, a = _veil()
        _VEIL = (inv, a, np.full((H, W, 3), ROOT_BGR, np.uint8))
    inv_veil, veil_a, veil_color = _VEIL

    dur = float(seg["dur"])
    n = max(1, math.ceil(dur * FPS - 1e-6))
    amp = 0.075 if dur < 4 else (0.05 if dur < 8 else 0.03)
    z0, z1 = (1.0, 1.0 + amp) if seg["seg_index"] % 2 == 0 else (1.0 + amp, 1.0)
    bg = _background(img)
    ph, pw = img.shape[:2]

    if tall:
        vw, vh = int(W * 0.40), int(H * 0.92)
        strip = cv2.resize(img, (vw, int(ph * vw / pw)), interpolation=cv2.INTER_AREA)
        pan = max(0, strip.shape[0] - vh)
        sprite = _Sprite(vw, vh)                 # shadow + white viewport, static
        card_mask = sprite.card_a
    else:
        if crop is not None:
            from shot_planner import get_crop_layout
            lay = get_crop_layout(crop, pw, ph)
            cw, ch = max(1, round(lay["w"])), max(1, round(lay["h"]))
            x0, y0, x1, y1 = crop
            ry0, rx0 = int(round(y0 * ph)), int(round(x0 * pw))
            region = img[ry0:max(int(round(y1 * ph)), ry0 + 1),
                         rx0:max(int(round(x1 * pw)), rx0 + 1)]
            content = cv2.resize(region, (cw, ch), interpolation=cv2.INTER_AREA
                                 if region.shape[1] > cw else cv2.INTER_CUBIC)
        else:
            ar = ph / pw
            mw, mh = (46, 97) if ar >= 1.25 else ((56, 93) if ar >= 0.85 else (68, 93))
            f = min(1.0, W * mw / 100 / pw, H * mh / 100 / ph)   # never upscales
            cw, ch = max(1, round(pw * f)), max(1, round(ph * f))
            content = cv2.resize(img, (cw, ch), interpolation=cv2.INTER_AREA
                                 if f < 1 else cv2.INTER_LINEAR)
        sprite = _Sprite(cw, ch, content)

    for i in range(n):
        t = i / FPS
        prog = min(t / dur, 1.0) if dur > 0 else 1.0
        bs = 1.15 + (1.22 - 1.15) * prog              # #bg scale tween
        M = np.float32([[bs, 0, (1 - bs) * W / 2], [0, bs, (1 - bs) * H / 2]])
        bgw = cv2.warpAffine(bg, M, (W, H), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REFLECT)
        frame = cv2.blendLinear(bgw, veil_color, inv_veil, veil_a)
        e = _ease_out2(t / ENTRANCE)
        opacity, enter = e, 0.94 + 0.06 * e
        if tall:
            _blit(frame, sprite.color, sprite.alpha, enter, opacity)
            y = pan * prog                              # tl.fromTo("#panimg", {y:0}, {y:-pan})
            win = cv2.warpAffine(strip, np.float32([[1, 0, 0], [0, 1, -y]]), (vw, vh),
                                 flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=(255, 255, 255))
            _blit(frame, win, card_mask, enter, opacity, offset=PAD)
        else:
            _blit(frame, sprite.color, sprite.alpha,
                  enter * (z0 + (z1 - z0) * prog), opacity)
        yield frame


def render(seg, panel_path, audio_plan, dst, crop=None, tall=False):
    """Render one segment to dst. audio_plan: [(mp3_path, offset_s), ...],
    already checked against the window by render_segments.audio_plan()."""
    img = _read_bgr(panel_path)
    dur = float(seg["dur"])
    n = max(1, math.ceil(dur * FPS - 1e-6))
    vdur = n / FPS
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"]
    for path, _off in audio_plan:
        cmd += ["-i", path]
    if audio_plan:
        parts, labels = [], []
        for k, (_p, off) in enumerate(audio_plan, start=1):
            ms = max(0, int(round(off * 1000)))
            # Narration is mono: duplicate it into both channels at unity.
            # aformat's own mono->stereo upmix applies a -3dB pan law, which
            # made these clips 3dB quieter than the HyperFrames ones.
            parts.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,"
                         f"pan=stereo|c0=c0|c1=c0,adelay={ms}|{ms}[a{k}]")
            labels.append(f"[a{k}]")
        if len(labels) > 1:   # beats never overlap, so no normalising gain
            mix = f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest"
        else:
            mix = f"{labels[0]}anull"
        graph = ";".join(parts) + f";{mix},apad,atrim=0:{vdur:.6f}[aout]"
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo"]
        graph = f"[1:a]atrim=0:{vdur:.6f}[aout]"
    cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-preset", os.environ.get("FAST_RENDER_PRESET", "veryfast"),
            "-crf", os.environ.get("FAST_RENDER_CRF", "18"),
            "-r", str(FPS), "-video_track_timescale", "15360",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
            "-t", f"{vdur:.6f}", "-movflags", "+faststart", dst]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in render_frames(img, seg, crop, tall):
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"fast render failed for seg_{seg['seg_index']:03d}: {err[-800:]}")
    return dst
