"""Chapter identity QA (owner, 2026-10-06): "make sure the chapters that are done
are what they are".

Read-only. For every chapter project it checks, from files on disk:
  * url      — the project's source URL names the same series/chapter as its folder
  * own      — every picture the video uses (segments.json panel_file) lives in
               THIS chapter's folder, not another chapter's
  * files    — those pictures and the per-segment clips exist
  * twin     — its source pages / panel pictures are not byte-copies of the
               previous or next chapter's (a scrape that fetched the wrong
               chapter, or a copied folder)
  * export   — the newest video's length is close to the storyboard's length
Fingerprints hash the first 64 KB + size of each file, so a whole library is
checked in seconds without reading every image in full.
"""
import glob
import hashlib
import json
import os
import re
import subprocess

TWIN_SHARE = 0.30          # this share of identical files with a neighbour = a twin
LEN_TOLERANCE = 0.20       # export vs storyboard length


def _read(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _fp(path):
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            head = f.read(65536)
        return hashlib.sha1(head + str(st.st_size).encode()).hexdigest()
    except OSError:
        return None


def fingerprints(pdir, sub):
    files = sorted(glob.glob(os.path.join(pdir, sub, "*")))
    return {fp for fp in (_fp(f) for f in files if os.path.isfile(f) and not os.path.basename(f).startswith("_"))
            if fp}


def _duration(path):
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                       "-of", "csv=p=0", path], text=True, timeout=20)
        return float(out.strip() or 0)
    except Exception:
        return None


def _chapter_num(pid):
    m = re.match(r"^(.*)_(\d+)$", pid)
    return (m.group(1), int(m.group(2))) if m else (None, None)


def check(projects_root, pid, expected_pid=None, cache=None):
    """One chapter's report: {"project", "status": ok|warn|bad, "issues": [...], ...}."""
    pdir = os.path.join(projects_root, pid)
    cache = cache if cache is not None else {}
    meta = _read(os.path.join(pdir, "project.json"), {}) or {}
    issues, bad = [], False
    if expected_pid and expected_pid != pid:
        issues.append(f"its source URL belongs to '{expected_pid}', but the folder is '{pid}'")
        bad = True
    segs = _read(os.path.join(pdir, "segments.json"), []) or []
    real = os.path.realpath(pdir)
    foreign, missing, no_clip = {}, 0, 0
    for s in segs:
        pf = s.get("panel_file") or ""
        if pf:
            rp = os.path.realpath(pf)
            if not os.path.exists(pf):
                missing += 1
            if not rp.startswith(real + os.sep):
                other = os.path.relpath(rp, os.path.realpath(projects_root)).split(os.sep)[0]
                foreign[other] = foreign.get(other, 0) + 1
        if s.get("user_included") and s.get("clip") and not os.path.exists(os.path.join(pdir, s["clip"])):
            no_clip += 1
    if foreign:
        bad = True
        issues.append("pictures come from another chapter's folder: " +
                      ", ".join(f"{k} ({v})" for k, v in sorted(foreign.items())))
    if missing:
        issues.append(f"{missing} picture file(s) missing")
    in_video = [s for s in segs if s.get("user_included")]
    series, n = _chapter_num(pid)
    for sub in ("pages", "crops"):
        mine = cache.setdefault((pid, sub), fingerprints(pdir, sub))
        if not mine or n is None:
            continue
        for nb in (n - 1, n + 1):
            npid = f"{series}_{nb}"
            if not os.path.isdir(os.path.join(projects_root, npid)):
                continue
            theirs = cache.setdefault((npid, sub), fingerprints(os.path.join(projects_root, npid), sub))
            if theirs:
                share = len(mine & theirs) / float(len(mine))
                if share >= TWIN_SHARE:
                    bad = True
                    issues.append(f"{int(share * 100)}% of its {sub} are identical to ch.{nb}'s")
    exp = None
    ed = os.path.join(pdir, "exports")
    try:
        mp4s = sorted((f for f in os.listdir(ed) if f.endswith(".mp4")),
                      key=lambda f: -os.path.getmtime(os.path.join(ed, f)))
        exp = mp4s[0] if mp4s else None
    except OSError:
        exp = None
    vid_len = board_len = None
    if exp:
        if no_clip:
            issues.append(f"{no_clip} segment clip(s) missing (the next render rebuilds them)")
        vid_len = _duration(os.path.join(ed, exp))
        board_len = round(sum(float(s.get("dur") or 0) for s in in_video), 1)
        speed = float(meta.get("export_speed") or 0) or None
        if vid_len and board_len:
            expected = [board_len] + ([board_len / speed] if speed else []) + [board_len / 1.25]
            if min(abs(vid_len - e) / e for e in expected if e) > LEN_TOLERANCE:
                issues.append(f"video is {vid_len:.0f}s but the storyboard is {board_len:.0f}s — "
                              "the video may be from an older cut")
    status = "bad" if bad else ("warn" if issues else "ok")
    return {"project": pid, "series": meta.get("series"), "chapter": meta.get("chapter"),
            "status": status, "issues": issues, "segments": len(segs), "in_video": len(in_video),
            "export": exp, "video_seconds": round(vid_len, 1) if vid_len else None,
            "board_seconds": board_len}
