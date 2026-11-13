"""Range compilations — spec 06 Part B3 (Track B).

Every ~20-30 chapters, one long video stitched from chapter recaps the studio
already made (each chapter's latest APPROVED export), published as a NEW video
— a previous range's title is never edited in place.

  * The range is stored as TWO INTEGERS, chapters_start and chapters_end; the
    title is built from them each time, never parsed from a title string.
  * Title (2026-10-12): "[a-b] {Series} Chapters a-b | Flamingo Recap", or
    "[a-b] {hook} — {Series} | Flamingo Recap" once the series has a locked
    hook; <= 100 characters.
  * The range lives in its own project folder ("<series>_ch<start>-<end>",
    project.json kind="range"), so review, the posting schedule and publishing
    treat it like any video, while chapter lists (Library, tracker) skip it.
"""
import json
import re
import os
import subprocess
import time

LIMIT = 100
KIND = "range"


def duration_label(seconds):
    """'Full 3H' rounds to the nearest hour, never below 1."""
    return max(1, int(round((seconds or 0) / 3600.0)))


def title(series, start, end, seconds=0, genre="", year=None, limit=LIMIT):
    """Default merged-video title, from the two integers (owner, 2026-10-12):
    "[a-b] {Series} Chapters a-b | Flamingo Recap" — no "Full 1H", no
    "| Manhwa Recap | Best Manhwa {Year}" tail. A series with a locked hook
    gets "[a-b] {hook} — {Series} | Flamingo Recap" from chapter_seo instead.
    Returns (title, dropped_parts). seconds/genre/year are kept for callers."""
    start, end = int(start), int(end)
    t = f"[{start}-{end}] {series} Chapters {start}-{end} | Flamingo Recap"
    dropped = []
    if len(t) > limit:
        t, dropped = f"[{start}-{end}] {series} Chapters {start}-{end}", ["| Flamingo Recap"]
    if len(t) > limit:
        t, dropped = f"[{start}-{end}] {series}"[:limit], dropped + [f"Chapters {start}-{end}"]
    return t, dropped


def project_id(series_id, start, end):
    return f"{series_id}_ch{int(start)}-{int(end)}"


def is_range(meta):
    return (meta or {}).get("kind") == KIND


def plan(projects_root, series_id, start, end, approved_export, specific_chapters=None):
    """Which export each chapter contributes. approved_export(pdir) -> name or
    None. Returns {"chapters": [(n, pdir, name)], "missing": [n, ...]}."""
    if specific_chapters:
        ch_list = sorted(list(set(int(x) for x in specific_chapters)))
    else:
        start, end = int(start), int(end)
        if start < 1 or end < start:
            raise ValueError("chapters_start must be >= 1 and <= chapters_end")
        ch_list = list(range(start, end + 1))
    got, missing = [], []
    for n in ch_list:
        pdir = os.path.join(projects_root, f"{series_id}_{n}")
        name = approved_export(pdir) if os.path.isdir(pdir) else None
        if name:
            got.append((n, pdir, name))
        else:
            missing.append(n)
    return {"chapters": got, "missing": missing}


def _probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", path], text=True)
    return float(out.strip() or 0)


OUTRO_SECONDS = 3.0      # render_segments._title_card default length, before speed-up
CUT_MARGIN = 0.04        # ~1 frame at 30 fps


def outro_seconds(pdir, name, _probe_fn=None):
    """Length of the "To be continued..." card at the end of a chapter export,
    in the export's own time (after its speed-up). 0 when the export's manifest
    does not record an outro: never cut story that might not be a card."""
    for p in (os.path.join(pdir, "exports", "_qa", name + ".manifest.json"),
              os.path.join(pdir, "exports", name + ".manifest.json")):
        try:
            with open(p, encoding="utf-8") as f:
                ctx = (json.load(f) or {}).get("context") or {}
        except (OSError, ValueError):
            continue
        if ctx.get("kind") == "range":
            # a merged video ends with its LAST chapter's card (2026-11-01:
            # merging a merge left its "To be continued" mid-video)
            last = (ctx.get("components") or [None])[-1]
            return outro_seconds(last["project"], last["name"], _probe_fn) if last else 0.0
        card = next((c for c in ctx.get("cards") or [] if c.get("role") == "outro"), None)
        if not card:
            return 0.0
        speed = float(ctx.get("speed") or 1.0) or 1.0
        raw = OUTRO_SECONDS
        try:
            if card.get("path") and os.path.exists(card["path"]):
                raw = (_probe_fn or _probe)(card["path"]) or OUTRO_SECONDS
        except Exception:
            raw = OUTRO_SECONDS
        return round(raw / speed, 3)
    return 0.0


class StitchCheckFailed(RuntimeError):
    """The pieces cannot be joined safely, or the joined file is not what the
    pieces add up to. Nothing was written to the output path."""


def _stream_signature(path):
    """What must match for a stream copy to play: codec, frame size, pixel
    format and frame rate of the picture; codec, sample rate and channels of
    the sound."""
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,sample_rate,channels",
         "-of", "json", path], text=True)
    sig = {}
    for st in json.loads(out).get("streams") or []:
        if st.get("codec_type") == "video" and "video" not in sig:
            sig["video"] = (st.get("codec_name"), st.get("width"), st.get("height"),
                            st.get("pix_fmt"), st.get("r_frame_rate"))
        elif st.get("codec_type") == "audio" and "audio" not in sig:
            sig["audio"] = (st.get("codec_name"), st.get("sample_rate"), st.get("channels"))
    return sig


def _num(n):
    try:
        return float(n)
    except (TypeError, ValueError):
        return None


def check_pieces(chapters, _sig_fn=None):
    """Before joining: pieces in rising chapter order with none repeated, and
    every piece encoded the same way. Raises StitchCheckFailed with the piece
    at fault. `_sig_fn=False` skips the encoding check (unit tests with fake
    files)."""
    nums = [_num(n) for n, _p, _name in chapters]
    if all(x is not None for x in nums):
        for a, b, (n, _p, name) in zip(nums, nums[1:], chapters[1:]):
            if b <= a:
                raise StitchCheckFailed(
                    f"the pieces are out of order or repeated: chapter {n} ({name}) comes after "
                    f"chapter {a:g}")
    if _sig_fn is False:
        return
    sig_fn = _sig_fn or _stream_signature
    first = None
    for n, pdir, name in chapters:
        src = os.path.join(pdir, "exports", name)
        sig = sig_fn(src)
        if not sig.get("video") or not sig.get("audio"):
            raise StitchCheckFailed(f"chapter {n} ({name}) has no picture or no sound")
        if first is None:
            first = (n, name, sig)
        elif sig != first[2]:
            raise StitchCheckFailed(
                f"chapter {n} ({name}) is encoded differently from chapter {first[0]} ({first[1]}): "
                f"{sig} vs {first[2]} — joining them without re-encoding would break playback")


# The joined file may differ from the sum of its pieces by a few frames per
# join (a stream-copy cut lands on a packet). A missing, repeated or truncated
# chapter is minutes off, far outside this.
JOIN_TOLERANCE = 1.0      # seconds per join
MIN_TOLERANCE = 2.0


def stitch(chapters, out_path, _run=None, _probe_fn=None, drop_inner_outros=True, _sig_fn=None):
    """Concatenate the chapter exports (same encoder settings, so stream copy)
    into out_path. Returns [(chapter, start_seconds)] — the arc markers.

    drop_inner_outros (owner, 2026-10-28): a merged video doesn't need "To be
    continued..." between chapters, so every chapter but the last ends where
    its story ends (concat outpoint; no re-encode).

    Checked (2026-11-13; nothing was checked before): the pieces are in rising
    chapter order, none repeated, all encoded alike; and the joined file is as
    long as the pieces add up to. On a failed check StitchCheckFailed is raised
    and out_path is left as it was."""
    run = _run or (lambda cmd: subprocess.run(cmd, check=True, capture_output=True))
    probe = _probe_fn or _probe
    real = _run is None                    # tests pass a fake runner and fake files
    check_pieces(chapters, _sig_fn if (real or _sig_fn) else False)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if real and os.environ.get("STITCH_ENGINE", "clean").lower() != "copy":
        try:
            return _stitch_clean(chapters, out_path, probe, drop_inner_outros)
        except StitchCheckFailed:
            raise
        except (ValueError, subprocess.CalledProcessError, OSError) as e:
            # a piece the clean join cannot take (e.g. its picture track does
            # not start at zero): join the old way rather than fail the merge
            print(f"[stitch] clean join not possible ({type(e).__name__}: {str(e)[:200]}) — "
                  f"using the packet-copy join for {os.path.basename(out_path)}", flush=True)
    lst = out_path + ".txt"
    marks, t = [], 0.0
    with open(lst, "w", encoding="utf-8") as f:
        for i, (n, pdir, name) in enumerate(chapters):
            src = os.path.join(pdir, "exports", name)
            f.write("file '%s'\n" % src.replace("'", "'\\''"))
            marks.append((n, t))
            dur = probe(src)
            cut = outro_seconds(pdir, name, probe) if drop_inner_outros and i < len(chapters) - 1 else 0.0
            if 0 < cut < dur:
                # one frame of margin: a stream-copy outpoint keeps the frame
                # that sits exactly on the cut (the card's first frame)
                end = dur - cut - CUT_MARGIN
                f.write("outpoint %.3f\n" % end)
                dur = end
            t += dur
    tmp = out_path + ".part.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", "-movflags", "+faststart", tmp])
    if real:
        _check_total(tmp, t, len(chapters), probe, extra=(lst,))
    os.replace(tmp, out_path)
    os.remove(lst)
    return marks


def _video_seconds(path):
    """Length of the picture track (what the join places pieces by)."""
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=duration", "-of", "csv=p=0", path], text=True)
    return float(out.strip() or 0)


def _video_start(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=start_time", "-of", "csv=p=0", path], text=True)
    try:
        return float(out.strip() or 0)
    except ValueError:
        return 0.0


def _zero_start(path, scratch):
    """The clean join places each piece by its picture track, which must start
    at zero. Videos made by the old packet-copy join start a few hundredths of
    a second late (0.021 s on a production merge): those are re-timed into
    `scratch` — packets copied, picture and sound shifted together, so their
    sync is unchanged. Returns the path to use."""
    start = _video_start(path)
    if abs(start) <= 0.001:
        return path
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-itsoffset", "%.6f" % -start, "-i", path,
                    "-c", "copy", scratch], check=True, capture_output=True)
    return scratch


def _packets_before_keyframe(path, when, window=0.75):
    """How many picture packets come before the keyframe closest to `when`
    (within `window` seconds), or 0 when there is no keyframe there. Reads the
    packet index only — nothing is decoded."""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path], text=True)
    except (subprocess.CalledProcessError, OSError):
        return 0
    best = None                                   # (distance, packets before it)
    for i, ln in enumerate(out.splitlines()):
        f = ln.split(",")
        if len(f) < 2 or "K" not in f[1]:
            continue
        try:
            d = abs(float(f[0]) - when)
        except ValueError:
            continue
        if d <= window and (best is None or d < best[0]):
            best = (d, i)
    return best[1] if best else 0


def _check_total(tmp, expected, pieces, probe, extra=()):
    got = probe(tmp)
    tol = max(MIN_TOLERANCE, JOIN_TOLERANCE * (pieces - 1))
    if abs(got - expected) > tol:
        for leftover in (tmp, *extra):
            try:
                os.remove(leftover)
            except OSError:
                pass
        raise StitchCheckFailed(
            f"the joined video is {got:.1f}s but its {pieces} pieces add up to {expected:.1f}s "
            f"(allowed difference {tol:.0f}s) — a piece is missing, repeated or cut short")


def _stitch_clean(chapters, out_path, probe, drop_inner_outros):
    """Join with continuous timestamps (2026-11-13).

    The packet-copy join above leaves two pictures with the same timestamp at
    every chapter boundary, and sound packets that overlap there: all 30 merged
    exports checked on production failed final-media QA with "non monotonically
    increasing dts", and the same fault reproduces on test clips. Here each
    inner chapter is first cut at its story's end (pictures and sound copied),
    then the pieces are joined the way a chapter's own clips are
    (media_concat.concatenate): pictures copied untouched, each soundtrack
    fitted to its pictures and encoded once. No picture is re-encoded.
    STITCH_ENGINE=copy restores the old join."""
    import sys
    import tempfile
    recap = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if recap not in sys.path:
        sys.path.insert(0, recap)
    from media_concat import concatenate
    marks, t = [], 0.0
    tmp = out_path + ".part.mp4"
    with tempfile.TemporaryDirectory(prefix=".stitch-", dir=os.path.dirname(out_path)) as work:
        parts = []
        for i, (n, pdir, name) in enumerate(chapters):
            src = os.path.join(pdir, "exports", name)
            dur = probe(src)
            cut = outro_seconds(pdir, name, probe) if drop_inner_outros and i < len(chapters) - 1 else 0.0
            part = src
            if 0 < cut < dur:
                part = os.path.join(work, "%03d.mp4" % i)
                # The card is its own clip, so it starts on a keyframe. Keeping
                # exactly the pictures that come before that keyframe ends the
                # story on a complete group of pictures. (A cut by time works on
                # decode order: it either keeps the card's first frames or
                # drops the story's last ones — the fault in the old join.)
                count = _packets_before_keyframe(src, dur - cut)
                cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src]
                cmd += ["-frames:v", str(count)] if count else ["-t", "%.3f" % (dur - cut - CUT_MARGIN)]
                subprocess.run(cmd + ["-c", "copy", part], check=True, capture_output=True)
                dur = _video_seconds(part)
            part = _zero_start(part, os.path.join(work, "%03dz.mp4" % i))
            dur = _video_seconds(part) if part != src else dur
            marks.append((n, t))
            t += dur
            parts.append(part)
        try:
            concatenate(parts, tmp)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    _check_total(tmp, t, len(chapters), probe)
    os.replace(tmp, out_path)
    return marks


def kept_seconds(marks, total):
    """How much of each piece is in the stitched file (its outro cut off),
    from stitch()'s markers and the finished file's length."""
    starts = [t for _n, t in marks] + [float(total)]
    return [round(starts[i + 1] - starts[i], 3) for i in range(len(marks))]


def arcs(marks, every=5):
    """Block 3 markers for a range. Chapters are grouped `every` at a time —
    the studio has no arc data, so these are chapter groups, not story arcs."""
    out = []
    for i in range(0, len(marks), every):
        grp = marks[i:i + every]
        a, b = grp[0][0], grp[-1][0]
        out.append((grp[0][1], f"Chapter {a}" if a == b else f"Chapters {a}-{b}"))
    return out


def write_project(projects_root, series_id, series, start, end, url=""):
    pid = project_id(series_id, start, end)
    pdir = os.path.join(projects_root, pid)
    os.makedirs(os.path.join(pdir, "exports"), exist_ok=True)
    meta = {"id": pid, "kind": KIND, "series": series, "series_id": series_id,
            "chapters_start": int(start), "chapters_end": int(end),
            "chapter": f"{int(start)}-{int(end)}", "url": url, "engine": "gemini",
            "created_at": time.time()}
    with open(os.path.join(pdir, "project.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return pdir, meta


def has_range_format(t: str, a: int, b: int) -> bool:
    """True if title contains range {a}-{b} in standard or abbreviated format:
    'Chapter 38-41', 'Chapters 38-41', 'Ch. 38-41', 'Ch 38-41', '[38-41]', '(38-41)',
    '[Ch. 38-41]', or '{a}-{b}' with optional spaces/dashes."""
    if not t or not isinstance(a, int) or not isinstance(b, int):
        return False
    dash = r'\s*[-–—]\s*'
    pat = re.compile(
        rf'(?:\[\s*(?:(?:ch|ep|chapter|episode)\.?\s*)?{a}{dash}{b}\s*\])|'
        rf'(?:\(\s*(?:(?:ch|ep|chapter|episode)\.?\s*)?{a}{dash}{b}\s*\))|'
        rf'(?:(?:chapters?|ch|episodes?|ep)\.?\s*{a}{dash}{b})|'
        rf'(?:\b{a}{dash}{b}\b)',
        re.I
    )
    return bool(pat.search(t))


def problems(t, meta):
    """Range-title rules (B3), as sentences."""
    out = []
    if len(t or "") > LIMIT:
        out.append(f"Title is {len(t)} characters; the limit is {LIMIT}.")
    a, b = (meta or {}).get("chapters_start"), (meta or {}).get("chapters_end")
    if not (isinstance(a, int) and isinstance(b, int)):
        out.append("The range must be stored as two integers (chapters_start, chapters_end).")
    elif not has_range_format(t, a, b):
        out.append(f'The title must say "Chapter {a}-{b}" (or "Ch. {a}-{b}", "[{a}-{b}]") — it is built from the stored range.')
    return out
