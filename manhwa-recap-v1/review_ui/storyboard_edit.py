"""Storyboard editor v2 op engine (P1-P7, 2026-07-19 approved plan).

The narration audio is a fixed timeline (beat MP3s, plus deliberate silent
holds); panels are a VISUAL TRACK laid over it. Every operation here rewrites
the project's segments.json — the render manifest — so what the storyboard
shows is exactly what renders, with no agent in the loop.

Invariants maintained by every op:
  - segments are a contiguous timeline: starts are cumulative in list order
  - each beat lies wholly inside its segment's window; when an edit puts an
    image cut mid-sentence, the beat's mp3 is SLICED (ffmpeg) into parts and
    each part carries an explicit "file" so clips still embed their own audio
  - seg_index is a STABLE id (clip filenames); list order is playback order
  - edited segments' stale clips are deleted so incremental render redoes
    only them (reorder/ripple never invalidates clips — offsets are relative)

Every op appends a line to edits.log.jsonl (who-needs-it: forensics + undo
research); ops raise ValueError with a human message on invalid input.
"""
import json
import os
import subprocess
import time

MIN_SEG = 0.8          # smallest allowed hold, seconds
GAP = 0.35             # narration gap used when appending new lines
DEFAULT_HOLD = 2.5     # silent hold for script-less panels


# ---------------------------------------------------------------- io helpers
def _segs_path(pdir):
    return os.path.join(pdir, "segments.json")


def load(pdir):
    with open(_segs_path(pdir), encoding="utf-8") as f:
        return json.load(f)


def save(pdir, segs):
    tmp = _segs_path(pdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(segs, f, indent=2)
    os.replace(tmp, _segs_path(pdir))


def _log(pdir, op, **kw):
    entry = {"ts": time.time(), "op": op, **kw}
    with open(os.path.join(pdir, "edits.log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _stale(pdir, seg_indexes):
    for si in set(seg_indexes):
        p = os.path.join(pdir, "clips", f"seg_{si:03d}.mp4")
        if os.path.exists(p):
            os.remove(p)


def _ffdur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True).strip()
    return round(float(out), 3)


def _slice_mp3(src, at, out_a, out_b):
    """Split src at `at` seconds into out_a (0..at) + out_b (at..end).
    Re-encodes (-q:a 4) so the cut lands accurately, not on a frame edge."""
    os.makedirs(os.path.dirname(out_a), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", src, "-t", f"{at:.3f}", "-q:a", "4", out_a],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-i", src, "-ss", f"{at:.3f}", "-q:a", "4", out_b],
                   check=True, capture_output=True)


# ------------------------------------------------------------- core plumbing
def _pos(segs, si):
    for p, s in enumerate(segs):
        if s["seg_index"] == si:
            return p
    raise ValueError(f"segment {si} not found")


def _occupied(seg):
    """Seconds of the segment's window actually covered by audio."""
    if not seg["beats"]:
        return 0.0
    return round(max(b["end"] for b in seg["beats"]) - seg["start"], 3)


def _ripple(segs):
    """Restore the cumulative-start invariant; shift each segment's beats by
    the same delta as their segment so relative offsets are untouched."""
    t = 0.0
    for s in segs:
        delta = round(t - s["start"], 3)
        if delta:
            s["start"] = round(s["start"] + delta, 3)
            for b in s["beats"]:
                b["start"] = round(b["start"] + delta, 3)
                b["end"] = round(b["end"] + delta, 3)
        s["end"] = round(s["start"] + s["dur"], 3)
        t = round(t + s["dur"], 3)
    return segs


def _next_seg_index(segs):
    return (max(s["seg_index"] for s in segs) + 1) if segs else 0


def _audio_dir(pdir):
    return os.path.join(pdir, "audio")


# ------------------------------------------------------------------ P2: time
def set_duration(pdir, si, dur):
    """Set a segment's on-screen duration. Extends/trims the silence after
    its narration; cannot cut into audio (use move_boundary for that)."""
    segs = load(pdir)
    s = segs[_pos(segs, si)]
    occ = _occupied(s)
    dur = round(float(dur), 3)
    if dur < max(MIN_SEG, occ):
        raise ValueError(
            f"duration {dur}s is below the minimum for this segment "
            f"({max(MIN_SEG, occ):.1f}s — its narration occupies {occ:.1f}s; "
            f"move the boundary instead to hand audio to a neighbour)")
    old = s["dur"]
    s["dur"] = dur
    _ripple(segs)
    save(pdir, segs)
    _stale(pdir, [si])
    _log(pdir, "set_duration", seg=si, frm=old, to=dur)
    return segs


def move_boundary(pdir, si, delta):
    """Move the cut between segment si and its NEXT neighbour by delta
    seconds (+ = si grows, - = si shrinks). Total runtime is conserved.
    Beats crossing the new cut are sliced (audio keeps playing seamlessly
    across the image change)."""
    segs = load(pdir)
    p = _pos(segs, si)
    if p + 1 >= len(segs):
        raise ValueError("last segment has no next boundary — use duration")
    a, b = segs[p], segs[p + 1]
    delta = round(float(delta), 3)
    if a["dur"] + delta < MIN_SEG or b["dur"] - delta < MIN_SEG:
        raise ValueError(f"boundary move would shrink a segment below {MIN_SEG}s")
    cut = round(a["start"] + a["dur"] + delta, 3)   # new absolute cut time
    adir = _audio_dir(pdir)

    def transfer(from_seg, to_seg, moving_left):
        """Move/slice beats of from_seg that fall on the wrong side of cut."""
        keep, moved = [], []
        for bt in from_seg["beats"]:
            if moving_left:      # si shrank: beats at/after cut go right
                wrong = bt["start"] >= cut
                straddle = bt["start"] < cut < bt["end"]
            else:                # si grew: nxt beats before cut go left
                wrong = bt["end"] <= cut
                straddle = bt["start"] < cut < bt["end"]
            if straddle:
                src = os.path.join(adir, bt.get("file") or f"beat_{bt['index']:03d}.mp3")
                tag = f"b{bt['index']:03d}_{int(cut*1000)}"
                fa = os.path.join("slices", f"{tag}_a.mp3")
                fb = os.path.join("slices", f"{tag}_b.mp3")
                _slice_mp3(src, round(cut - bt["start"], 3),
                           os.path.join(adir, fa), os.path.join(adir, fb))
                left = dict(bt, end=cut, file=fa)
                right = dict(bt, start=cut, file=fb)
                if moving_left:
                    keep.append(left); moved.append(right)
                else:
                    moved.append(left); keep.append(right)
            elif wrong:
                moved.append(bt)
            else:
                keep.append(bt)
        from_seg["beats"] = keep
        if moving_left:
            to_seg["beats"] = sorted(moved + to_seg["beats"], key=lambda x: x["start"])
        else:
            to_seg["beats"] = sorted(to_seg["beats"] + moved, key=lambda x: x["start"])

    if delta < 0:
        transfer(a, b, moving_left=True)
    else:
        transfer(b, a, moving_left=False)
    a["dur"] = round(a["dur"] + delta, 3)
    b["dur"] = round(b["dur"] - delta, 3)
    b["start"] = round(b["start"] + delta, 3)
    _ripple(segs)
    save(pdir, segs)
    _stale(pdir, [a["seg_index"], b["seg_index"]])
    _log(pdir, "move_boundary", seg=si, delta=delta)
    return segs


# ------------------------------------------------------- P3/P4: include panel
def include_panel(pdir, panel_id, scenes, descs, hold=DEFAULT_HOLD):
    """Give panel_id its own visual window.

    - Panel in a narration unit: carve the window of its unit's host segment
      (split at the nearest beat boundary to the midpoint; single-beat hosts
      are sliced mid-audio). Runtime unchanged.
    - Script-less panel (junk/left-out): insert an N-second SILENT hold at
      its reading-order position. Runtime grows by `hold` seconds.
    """
    segs = load(pdir)
    if any(s["panel_id"] == panel_id for s in segs):
        raise ValueError(f"{panel_id} is already on the timeline")
    d = next((x for x in descs if x["panel_id"] == panel_id), None)
    if d is None:
        raise ValueError(f"unknown panel {panel_id}")
    unit = next((sc for sc in scenes if panel_id in sc.get("panel_ids", [])), None)
    crops = os.path.join(pdir, "crops")
    new_si = _next_seg_index(segs)

    if unit:
        upids = unit["panel_ids"]
        k = upids.index(panel_id)
        host = None          # prefer nearest on-screen unit panel BEFORE this
        for j in range(k - 1, -1, -1):
            host = next((s for s in segs if s["panel_id"] == upids[j]), host)
            if host:
                break
        after = bool(host)
        if not host:
            for j in range(k + 1, len(upids)):
                host = next((s for s in segs if s["panel_id"] == upids[j]), None)
                if host:
                    break
        if not host:
            raise ValueError(f"unit ¶{unit['scene_id']} has no on-screen host to carve")
        hp = _pos(segs, host["seg_index"])
        if len(host["beats"]) >= 2:
            # split at the beat boundary nearest the midpoint
            mid = host["start"] + host["dur"] / 2
            cutb = min(range(1, len(host["beats"])),
                       key=lambda i: abs(host["beats"][i]["start"] - mid))
            # cut in the silence between sentences: halfway between the
            # previous beat's end and the next beat's start (clamped so no
            # audio ever falls outside its segment's window)
            prev_end = host["beats"][cutb - 1]["end"]
            nxt_start = host["beats"][cutb]["start"]
            cut = round(max(prev_end, (prev_end + nxt_start) / 2), 3)
            tail = [b for b in host["beats"] if b["start"] >= cut]
            host["beats"] = [b for b in host["beats"] if b["start"] < cut]
        else:
            cut = round(host["start"] + host["dur"] / 2, 3)
            tail = []
            if host["beats"]:
                bt = host["beats"][0]
                if bt["start"] < cut < bt["end"]:
                    adir = _audio_dir(pdir)
                    src = os.path.join(adir, bt.get("file") or f"beat_{bt['index']:03d}.mp3")
                    tag = f"b{bt['index']:03d}_{int(cut*1000)}"
                    fa = os.path.join("slices", f"{tag}_a.mp3")
                    fb = os.path.join("slices", f"{tag}_b.mp3")
                    _slice_mp3(src, round(cut - bt["start"], 3),
                               os.path.join(adir, fa), os.path.join(adir, fb))
                    host["beats"] = [dict(bt, end=cut, file=fa)]
                    tail = [dict(bt, start=cut, file=fb)]
                elif bt["start"] >= cut:
                    tail = [bt]; host["beats"] = []
        end = round(host["start"] + host["dur"], 3)
        new = {"seg_index": new_si, "panel_id": panel_id,
               "panel_file": os.path.join(crops, f"{panel_id}.png"),
               "start": cut, "end": end, "dur": round(end - cut, 3),
               "beats": tail, "clip": f"clips/seg_{new_si:03d}.mp4"}
        host["dur"] = round(cut - host["start"], 3)
        segs.insert(hp + 1, new) if after else segs.insert(hp, new)
        if not after:   # carved from the head of a later host: swap windows
            new["start"], host["start"] = host["start"], cut
        _ripple(segs)
        save(pdir, segs)
        _stale(pdir, [host["seg_index"], new_si])
        _log(pdir, "include", panel=panel_id, mode="carve", host=host["seg_index"])
    else:
        # silent hold at reading-order position
        order = sorted((x["panel_id"] for x in descs),
                       key=lambda pid: [int(t) if t.isdigit() else t
                                        for t in __import__("re").split(r"(\d+)", pid)])
        before = set(order[:order.index(panel_id)])
        at = 0
        for i, s in enumerate(segs):
            if s["panel_id"] in before:
                at = i + 1
        new = {"seg_index": new_si, "panel_id": panel_id,
               "panel_file": os.path.join(crops, f"{panel_id}.png"),
               "start": 0, "end": 0, "dur": round(float(hold), 3),
               "beats": [], "clip": f"clips/seg_{new_si:03d}.mp4",
               "silent_hold": True}
        segs.insert(at, new)
        _ripple(segs)
        save(pdir, segs)
        _stale(pdir, [new_si])
        _log(pdir, "include", panel=panel_id, mode="silent_hold", dur=hold)
    return segs


def exclude_panel(pdir, panel_id):
    """Remove a panel from the timeline. Narrated windows fold back into the
    previous neighbour (or next, at the head); silent holds just vanish."""
    segs = load(pdir)
    targets = [s for s in segs if s["panel_id"] == panel_id]
    if not targets:
        raise ValueError(f"{panel_id} is not on the timeline")
    stale = []
    for s in targets:
        p = _pos(segs, s["seg_index"])
        if s.get("silent_hold") or not s["beats"]:
            segs.pop(p)
            stale.append(s["seg_index"])
            continue
        host = segs[p - 1] if p > 0 else (segs[p + 1] if p + 1 < len(segs) else None)
        if host is None:
            raise ValueError("cannot exclude the only segment")
        host["beats"] = sorted(host["beats"] + s["beats"], key=lambda x: x["start"])
        host["dur"] = round(host["dur"] + s["dur"], 3)
        if host["start"] > s["start"]:      # folded into the NEXT neighbour
            host["start"] = s["start"]
        segs.pop(p)
        stale.extend([s["seg_index"], host["seg_index"]])
    _ripple(segs)
    save(pdir, segs)
    _stale(pdir, stale)
    _log(pdir, "exclude", panel=panel_id)
    return segs


# --------------------------------------------------------------- P5: reorder
def reorder(pdir, si, to_position):
    """Move a segment (visual + its narration) to a new playback position."""
    segs = load(pdir)
    p = _pos(segs, si)
    to_position = max(0, min(len(segs) - 1, int(to_position)))
    seg = segs.pop(p)
    segs.insert(to_position, seg)
    _ripple(segs)
    save(pdir, segs)          # no clips stale — offsets are segment-relative
    _log(pdir, "reorder", seg=si, frm=p, to=to_position)
    return segs


# -------------------------------------------------------------- P6: add line
def add_line(pdir, si, text, synth):
    """Append a NEW narration sentence to segment si (synth = callable
    (text, out_path) that produces the mp3 — the server's usage-gated TTS)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty narration text")
    segs = load(pdir)
    s = segs[_pos(segs, si)]
    idx = max((b["index"] for x in segs for b in x["beats"]), default=-1) + 1
    out = os.path.join(_audio_dir(pdir), f"beat_{idx:03d}.mp3")
    synth(text, out)
    adur = _ffdur(out)
    occ = _occupied(s)
    bstart = round(s["start"] + (occ + GAP if occ else 0.0), 3)
    beat = {"index": idx, "text": text, "start": bstart,
            "end": round(bstart + adur, 3)}
    s["beats"].append(beat)
    need = round(beat["end"] - s["start"], 3)
    if s["dur"] < need:
        s["dur"] = need
    s.pop("silent_hold", None)
    _ripple(segs)
    save(pdir, segs)
    _stale(pdir, [si])
    _log(pdir, "add_line", seg=si, beat=idx, chars=len(text))
    return segs


# ------------------------------------------------- P7: re-TTS ripple support
def coalesce_beat(pdir, beat_index):
    """Merge slice-parts of a beat back into one whole beat (called before a
    re-TTS overwrites beat_<index>.mp3, so edits always resynth the full
    sentence). Returns the seg_index now owning the whole beat."""
    segs = load(pdir)
    owners = [s for s in segs if any(b["index"] == beat_index for b in s["beats"])]
    if not owners:
        raise ValueError(f"beat {beat_index} not found")
    first = owners[0]
    parts = [b for s in owners for b in s["beats"] if b["index"] == beat_index]
    whole = {"index": beat_index,
             "text": parts[0]["text"],
             "start": min(b["start"] for b in parts),
             "end": max(b["end"] for b in parts)}
    for s in owners:
        s["beats"] = [b for b in s["beats"] if b["index"] != beat_index]
    first["beats"] = sorted(first["beats"] + [whole], key=lambda x: x["start"])
    save(pdir, segs)
    _stale(pdir, [s["seg_index"] for s in owners])
    return first["seg_index"]


def resize_after_tts(pdir, beat_index, new_dur):
    """After a beat's mp3 was re-synthesized (possibly a new length), shift
    that beat's end and everything after it so the timeline stays truthful,
    growing/shrinking the owning segment by the difference."""
    segs = load(pdir)
    for s in segs:
        for b in s["beats"]:
            if b["index"] == beat_index:
                old = round(b["end"] - b["start"], 3)
                diff = round(new_dur - old, 3)
                if not diff:
                    return segs
                b["end"] = round(b["end"] + diff, 3)
                later = [x for x in s["beats"] if x["start"] > b["start"]]
                for x in later:
                    x["start"] = round(x["start"] + diff, 3)
                    x["end"] = round(x["end"] + diff, 3)
                s["dur"] = round(max(s["dur"] + diff, _occupied(s), MIN_SEG), 3)
                _ripple(segs)
                save(pdir, segs)
                _stale(pdir, [s["seg_index"]])
                _log(pdir, "resize_after_tts", beat=beat_index, diff=diff)
                return segs
    raise ValueError(f"beat {beat_index} not found")
