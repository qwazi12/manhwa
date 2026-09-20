"""A narration line spread over several panels must carry SLICED audio.

`expand_units` deliberately spreads one sentence across several panels for
visual progression. Each resulting shot got its own narrow window but kept the
SAME beat index, and with no explicit file the renderer falls back to
`beat_<index>.mp3` — the WHOLE sentence. A 5.544s file was scheduled inside a
5.099s window, so render_segments.py refused the whole chapter.

Measured on the live project i-am-the-fated-villain_352-lab-claude: **52 of 79
segments** failed, because a multi-panel unit is the normal case, not an edge
case. `repair_slices` — which the error told the operator to run — fixed 5 of
the 52, because it re-binds existing slices and there were none to re-bind.

The fix gives the lab what production's hand-edits already had: cut the mp3 at
the slot boundaries and record a file per part.

Uses real ffmpeg-generated audio, because the whole bug is about durations on
disk disagreeing with durations in the manifest.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hyperframes"))

R = []
COVER_TOL = 0.12          # same tolerance render_segments.py uses


def check(name, ok):
    R.append((name, bool(ok)))


def tone(path, seconds):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-q:a", "4", path],
                   check=True, capture_output=True)
    return path


def main():
    import claude_lab as LAB
    from segments import build_segments

    if not LAB.shutil.which("ffmpeg"):
        print("ffmpeg unavailable — cannot test audio slicing")
        return 1

    tmp = tempfile.mkdtemp(prefix="labaud_")
    audio = os.path.join(tmp, "audio")
    os.makedirs(audio, exist_ok=True)

    # beat 0 = 11.016s spread over TWO panels (the live seg 0 / seg 1 case)
    # beat 1 = 4.0s on ONE panel (must stay whole)
    tone(os.path.join(audio, "beat_000.mp3"), 11.016)
    tone(os.path.join(audio, "beat_001.mp3"), 4.0)

    beats = [{"index": 0, "start": 0.0, "end": 11.016, "text": "a long line"},
             {"index": 1, "start": 11.616, "end": 15.616, "text": "a short one"}]
    descs = [{"panel_id": "page001_panel_001", "file": "a.png",
              "width": 800, "height": 1200},
             {"panel_id": "page001_panel_002", "file": "b.png",
              "width": 800, "height": 1200},
             {"panel_id": "page002_panel_001", "file": "c.png",
              "width": 800, "height": 1200}]
    slots = [{"beat_index": 0, "panel_index": 0, "start": 0.0, "end": 5.508},
             {"beat_index": 0, "panel_index": 1, "start": 5.508, "end": 11.016},
             {"beat_index": 1, "panel_index": 2, "start": 11.616, "end": 15.616}]

    segs = LAB._build_shots(os.path.join(tmp, "crops"), descs, beats, slots,
                            {}, build_segments, audio_dir=audio)

    allbeats = [(s, b) for s in segs for b in s["beats"]]
    shared = [b for _, b in allbeats if b["index"] == 0]
    lone = [b for _, b in allbeats if b["index"] == 1]

    check("a sentence spread over two panels produces two beat records",
          len(shared) == 2)
    check("...and EVERY one names its own audio slice",
          all(b.get("file") for b in shared))
    check("...with different files, not the same one twice",
          len({b["file"] for b in shared}) == 2)
    check("a sentence that stays on ONE panel is not sliced",
          lone and not lone[0].get("file"))
    check("...so it still plays the whole mp3", len(lone) == 1)

    for b in shared:
        p = os.path.join(audio, b["file"])
        check(f"the slice {b['file']} was actually written", os.path.exists(p))

    # THE ACTUAL CONTRACT: replay render_segments.py's own fit check.
    bad = []
    for s, b in allbeats:
        fname = b.get("file") or f"beat_{b['index']:03d}.mp3"
        p = os.path.join(audio, fname)
        if not os.path.exists(p):
            continue
        off = round(b["start"] - s["start"], 3)
        adur = LAB._audio_dur(p)
        if off < -COVER_TOL or off + adur > s["dur"] + COVER_TOL:
            bad.append((s["seg_index"], fname, off, adur, s["dur"]))
    check("every beat now fits its window — the render gate passes", not bad)
    if bad:
        for x in bad:
            print("   still broken:", x)

    # And prove the OLD behaviour would have failed this same check, so the
    # test cannot pass vacuously.
    old_bad = []
    for s, b in allbeats:
        p = os.path.join(audio, f"beat_{b['index']:03d}.mp3")
        off = round(b["start"] - s["start"], 3)
        adur = LAB._audio_dur(p)
        if off + adur > s["dur"] + COVER_TOL:
            old_bad.append(s["seg_index"])
    check("...and the whole-file fallback WOULD have failed it",
          len(old_bad) >= 2)

    # The last part must not clip the tail of the sentence.
    total = sum(LAB._audio_dur(os.path.join(audio, b["file"])) for b in shared)
    check("the slices together still carry the whole sentence",
          abs(total - 11.016) < 0.35)

    # Production's own shots never set beat_file, so its output is unchanged.
    plain = build_segments([
        {"index": 0, "start": 0.0, "end": 3.0, "beat_text": "x",
         "panel_id": "p1", "panel_file": "p1.png", "crop_bbox_norm": [0, 0, 1, 1]}])
    check("a producer that does not slice is completely unaffected",
          "file" not in plain[0]["beats"][0])

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
