"""Range compilations — spec 06 Part B3 (Track B).

Every ~20-30 chapters, one long video stitched from chapter recaps the studio
already made (each chapter's latest APPROVED export), published as a NEW video
— a previous range's title is never edited in place.

  * The range is stored as TWO INTEGERS, chapters_start and chapters_end; the
    title is built from them each time, never parsed from a title string.
  * Title: "Full {DURATION}H | {Series} Chapter {start}-{end} | Manhwa Recap |
    {Genre} | Best Manhwa {Year}", <= 100 characters, checked every time.
    Overflow: drop "| Best Manhwa {Year}" first, then "| {Genre}".
  * The range lives in its own project folder ("<series>_ch<start>-<end>",
    project.json kind="range"), so review, the posting schedule and publishing
    treat it like any video, while chapter lists (Library, tracker) skip it.
"""
import json
import os
import subprocess
import time

LIMIT = 100
KIND = "range"


def duration_label(seconds):
    """'Full 3H' rounds to the nearest hour, never below 1."""
    return max(1, int(round((seconds or 0) / 3600.0)))


def title(series, start, end, seconds, genre="", year=None, limit=LIMIT):
    """The B3 title from the two integers. Returns (title, dropped_parts)."""
    start, end = int(start), int(end)
    year = year or time.gmtime().tm_year
    parts = [f"Full {duration_label(seconds)}H", f"{series} Chapter {start}-{end}", "Manhwa Recap"]
    tail = ([f"{genre}"] if genre else []) + [f"Best Manhwa {year}"]
    dropped = []
    while True:
        t = " | ".join(parts + tail)
        if len(t) <= limit or not tail:
            break
        dropped.append(tail.pop())        # "Best Manhwa {Year}" first, then "{Genre}"
    if len(t) > limit:
        raise ValueError(f"range title is {len(t)} characters even without genre/year — shorten the series name")
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


def stitch(chapters, out_path, _run=None, _probe_fn=None):
    """Concatenate the chapter exports (same encoder settings, so stream copy)
    into out_path. Returns [(chapter, start_seconds)] — the arc markers."""
    run = _run or (lambda cmd: subprocess.run(cmd, check=True, capture_output=True))
    probe = _probe_fn or _probe
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lst = out_path + ".txt"
    marks, t = [], 0.0
    with open(lst, "w", encoding="utf-8") as f:
        for n, pdir, name in chapters:
            src = os.path.join(pdir, "exports", name)
            f.write("file '%s'\n" % src.replace("'", "'\\''"))
            marks.append((n, t))
            t += probe(src)
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


def problems(t, meta):
    """Range-title rules (B3), as sentences."""
    out = []
    if len(t or "") > LIMIT:
        out.append(f"Title is {len(t)} characters; the limit is {LIMIT}.")
    a, b = (meta or {}).get("chapters_start"), (meta or {}).get("chapters_end")
    if not (isinstance(a, int) and isinstance(b, int)):
        out.append("The range must be stored as two integers (chapters_start, chapters_end).")
    elif f"Chapter {a}-{b}" not in (t or ""):
        out.append(f"The title must say \"Chapter {a}-{b}\" — it is built from the stored range.")
    return out
