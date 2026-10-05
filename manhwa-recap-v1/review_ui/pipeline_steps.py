"""Per-chapter pipeline strip (LongForm lesson, owner plan step 7, 2026-10-04).

Shows where one chapter stands in the pipeline (pages → panels → descriptions
→ script → voice → timeline → clips → video) and lets the owner re-run ONE
step: that step's outputs (and everything built from them) are removed, then
the chapter's ingest is queued again. Ingest reuses every cached output it
finds, so only the chosen step and what follows it are redone.

Splitting and the timeline (match, crops, segments) already re-run on every
ingest; render and export re-run through Approve, so they have no re-run here.
"""

import glob
import json
import os
import shutil
import time

# Files a re-run must clear, by the step it restarts from. A later step's set
# is always inside an earlier one's: re-describing re-writes the script, etc.
_BOARD = ["segments.json", "review.json", "storyboard.json", "edits.log.jsonl"]
_VOICE = ["tts.json"]
_SCRIPT = ["script.txt", "script.json", "direct_speech.json", "critique.json", "beatsheet.json"]
CLEARS = {
    "match": _BOARD,
    "voice": _VOICE + _BOARD,
    "narrate": _SCRIPT + _VOICE + _BOARD,
    # series_bible.json: the chapter's copy of the cast list, so a re-describe
    # picks up the series' current one
    "describe": ["descriptions.json", "series_bible.json"] + _SCRIPT + _VOICE + _BOARD,
}
RERUN = {
    "describe": {"label": "describe the panels again", "cost": "about $0.20 (Gemini vision), then the script and voice below"},
    "narrate": {"label": "rewrite the script", "cost": "about $0.10 (script) + $0.14 (voice)"},
    "voice": {"label": "record the voice again (current narrator)", "cost": "about $0.14 (voice)"},
    "match": {"label": "rebuild the timeline (panels, crops, timing)", "cost": "free"},
}


def _count(pattern):
    return len(glob.glob(pattern))


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def status(pdir):
    """The strip: one row per step with state 'done' | 'missing' | 'partial'."""
    j = lambda *p: os.path.join(pdir, *p)   # noqa: E731
    pages = _count(j("pages", "*.webp")) + _count(j("pages", "*.jpg")) + _count(j("pages", "*.png"))
    crops = _count(j("crops", "*.png"))
    descs = _json(j("descriptions.json"), []) or []
    roles = {}
    for d in descs if isinstance(descs, list) else []:
        r = d.get("role")
        if r and r != "art":
            roles[r] = roles.get(r, 0) + 1
    script = ""
    try:
        script = open(j("script.txt"), encoding="utf-8").read()
    except OSError:
        pass
    crit = _json(j("critique.json"), {}) or {}
    beats = _count(j("audio", "beat_*.mp3"))
    tts = _json(j("tts.json"), {}) or {}
    segs = _json(j("segments.json"), []) or []
    clips = _count(j("clips", "seg_*.mp4"))
    exports = sorted(glob.glob(j("exports", "*.mp4")), key=os.path.getmtime)
    rows = [
        ("scrape", "Pages", pages, f"{pages} page(s)", j("pages")),
        ("split", "Panels", crops, f"{crops} panel(s)", j("crops")),
        ("describe", "Descriptions", len(descs),
         f"{len(descs)} described" + (" · image check: " + ", ".join(f"{v} {k}" for k, v in sorted(roles.items())) if roles else ""),
         j("descriptions.json")),
        ("narrate", "Script", len(script.split()),
         f"{len(script.split())} words" + (f" · editor: {len(crit.get('issues') or [])} issue(s), "
                                           f"{len(crit.get('revised') or [])} rewritten" if crit else ""),
         j("script.txt")),
        ("voice", "Voice", beats,
         f"{beats} line(s)" + (f" · {tts.get('voice')}" + (f" ({tts.get('style')[:40]})" if tts.get("style") else "") if tts else ""),
         j("tts.json") if tts else j("audio")),
        ("match", "Timeline", len(segs), f"{len(segs)} segment(s)", j("segments.json")),
        ("render", "Clips", clips, f"{clips} of {len(segs)} clip(s) rendered", j("clips")),
        ("export", "Video", len(exports), (os.path.basename(exports[-1]) if exports else "not exported"),
         exports[-1] if exports else None),
    ]
    out = []
    for key, label, n, detail, path in rows:
        state = "done" if n else "missing"
        if key == "render" and segs and 0 < clips < len(segs):
            state = "partial"
        if key == "render" and not clips and exports and \
                (_json(j("drive.json"), {}) or {}).get("video") == os.path.basename(exports[-1]):
            # the clips were cleared after the video was copied to Drive (2026-10-05:
            # "0 of 71 · not yet" next to a finished video read as a failure)
            state, detail = "done", f"{len(segs)} clip(s) made; cleared after the Drive copy (rebuilt on the next render)"
        out.append({"key": key, "label": label, "state": state, "detail": detail,
                    "at": _mtime(path) if path else None,
                    "rerun": RERUN.get(key)})
    return out


def prepare_rerun(pdir, step):
    """Remove the step's outputs and everything built from them. Returns the
    list of removed paths (relative). Raises ValueError for an unknown step."""
    if step not in CLEARS:
        raise ValueError(f"'{step}' cannot be re-run on its own")
    removed = []
    for f in CLEARS[step]:
        p = os.path.join(pdir, f)
        if os.path.exists(p):
            os.remove(p)
            removed.append(f)
    if step in ("describe", "narrate", "voice"):
        for f in glob.glob(os.path.join(pdir, "audio", "beat_*.mp3")) + \
                glob.glob(os.path.join(pdir, "audio", "slices", "*.mp3")):
            os.remove(f)
            removed.append(os.path.relpath(f, pdir))
    if os.path.isdir(os.path.join(pdir, "clips")):
        shutil.rmtree(os.path.join(pdir, "clips"), ignore_errors=True)
        removed.append("clips/")
    with open(os.path.join(pdir, "rerun.log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.time(), "step": step, "removed": len(removed)}) + "\n")
    return removed
