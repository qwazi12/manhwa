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


def stitch(chapters, out_path, _run=None, _probe_fn=None, drop_inner_outros=True):
    """Concatenate the chapter exports (same encoder settings, so stream copy)
    into out_path. Returns [(chapter, start_seconds)] — the arc markers.

    drop_inner_outros (owner, 2026-10-28): a merged video doesn't need "To be
    continued..." between chapters, so every chapter but the last ends where
    its story ends (concat outpoint; no re-encode)."""
    run = _run or (lambda cmd: subprocess.run(cmd, check=True, capture_output=True))
    probe = _probe_fn or _probe
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
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
    os.replace(tmp, out_path)
    os.remove(lst)
    return marks


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
