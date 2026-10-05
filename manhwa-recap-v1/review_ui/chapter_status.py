"""One status for every chapter (rebuild step 2, owner plan 2026-10-04).

Before this, Projects, the Publishing Studio and the Tracker each worked out a
chapter's state from different files and called it different things. Now the
server decides once, here, and every page shows the same word, colour and icon.

    Found → Making → To review → Rendering → Video ready → Scheduled → Posting
          → Posted → Archived          (+ Waiting, Failed on the side)

`decide(facts)` is pure: it takes what is known about a chapter and returns the
status. `gather(...)` collects those facts from the files and live jobs.
"""

import json
import os

# key -> (label, tone, what it means / what happens next)
STATUSES = {
    "found":       ("Found",       "muted", "A new chapter on the source; not made yet."),
    "making":      ("Making",      "info",  "Pages → panels → script → voice → timeline are running."),
    "waiting":     ("Waiting",     "warn",  "Stopped part-way (budget, a pause or a restart); it can resume."),
    "failed":      ("Failed",      "bad",   "A step failed; the reason is shown and it can be retried."),
    "to_review":   ("To review",   "info",  "The board is ready: check it, then Render video."),
    "rendering":   ("Rendering",   "info",  "Clips and the final video are being made."),
    "video_ready": ("Video ready", "info",  "Watch it, fix the details, then Approve & schedule."),
    "scheduled":   ("Scheduled",   "ok",    "Approved; it posts at the next free slot (or when you press Post now)."),
    "posting":     ("Posting",     "warn",  "Uploading; waiting for YouTube to confirm."),
    "posted":      ("Posted",      "ok",    "Live on the channel."),
    "archived":    ("Archived",    "muted", "Posted and put away; deleted later unless kept."),
}
ORDER = list(STATUSES)
WAITING_INGEST = ("budget_paused", "interrupted", "paused", "pausing", "cancelled")
LIVE_INGEST = ("queued", "running")


def view(key, **extra):
    label, tone, hint = STATUSES[key]
    return {"key": key, "label": label, "tone": tone, "hint": hint, **extra}


def decide(f):
    """f: {archived, posted, posting, scheduled, approved_video, has_video,
    superseded, rendering, has_board, ingest_status, ingest_error}"""
    if f.get("archived"):
        return view("archived")
    if f.get("posted"):
        return view("posted")
    if f.get("posting"):
        return view("posting")
    if f.get("scheduled"):
        return view("scheduled")
    if f.get("rendering"):
        return view("rendering")
    ist = f.get("ingest_status")
    if ist in LIVE_INGEST:
        return view("making", stage=f.get("ingest_stage"))
    if f.get("has_video"):
        if f.get("approved_video") and not f.get("superseded"):
            return view("scheduled")          # approved but its queue row is missing: treated as scheduled
        return view("video_ready", superseded=bool(f.get("superseded")))
    if f.get("has_board"):
        return view("to_review")
    if ist in WAITING_INGEST:
        return view("waiting", reason=ist)
    if ist == "error":
        return view("failed", reason=f.get("ingest_error"))
    return view("making" if ist else "failed", reason=None if ist else "no board was built")


def _latest_export(pdir):
    d = os.path.join(pdir, "exports")
    try:
        files = [f for f in os.listdir(d) if f.endswith(".mp4")]
    except OSError:
        return None
    return max(files, key=lambda f: os.path.getmtime(os.path.join(d, f))) if files else None


def gather(pdir, ingest_rec=None, rendering=False, queue_rows=None, review_state=None,
           load_publishes=None, archived=None):
    """Facts for one project folder. Callers pass the live pieces they own
    (ingest record, rendering flag, queue rows for this project, and the
    server's review_state / load_publishes helpers)."""
    name = _latest_export(pdir)
    f = {"has_board": os.path.exists(os.path.join(pdir, "segments.json")),
         "has_video": bool(name), "video": name, "rendering": rendering,
         "archived": bool(archived)}
    if ingest_rec:
        f["ingest_status"] = ingest_rec.get("status")
        f["ingest_stage"] = ingest_rec.get("stage")
        f["ingest_error"] = (ingest_rec.get("error") or "")[:200] or None
    pubs = (load_publishes(pdir) if load_publishes else {}) or {}
    for rec in pubs.values():
        if any(r.get("status") == "published" for r in rec.get("results") or []):
            f["posted"] = True
        elif rec.get("status") == "in_progress":
            f["posting"] = True
    for row in queue_rows or []:
        if row.get("status") == "posted":
            f["posted"] = True
        elif row.get("status") == "posting":
            f["posting"] = True
        elif row.get("status") == "queued" and (not name or row.get("name") == name):
            f["scheduled"] = True
    if name and review_state:
        try:
            rv = review_state(pdir, name)
            f["approved_video"] = rv.get("status") == "approved"
            f["superseded"] = bool(rv.get("superseded"))
        except Exception:
            pass
    return f


def clean_title(text, url=""):
    """WEBTOON series names carry their title number ("Fog Land 9299"); readers
    don't need it."""
    import re
    if "webtoons.com" in (url or ""):
        return re.sub(r"\s+\d{3,6}(?=(\s+Ch\.|$))", "", text or "")
    return text or ""


def project_meta(pdir):
    try:
        with open(os.path.join(pdir, "project.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}
