"""YouTube title / thumbnail A/B tests (owner, 2026-11-15: "do 1, yes").

YouTube runs the test itself (YouTube Studio > the video > Test & compare: up
to 3 titles, 3 thumbnails or 3 pairs, judged by watch time). There is no API
and no Upload-Post option for it, so the test is SET UP in YouTube Studio, by a
person or by an agent driving the owner's browser. The studio's part is here:

  * which variants each posted video should test (plan)
  * a ledger of which videos already have a test, so nothing is set up twice
    and every video going out gets one (owner: test winners may change a
    posted video's title or thumbnail; that is YouTube's doing, not ours)

Thumbnails B and C are the video's own Thumbnail Studio concepts that look
most UNLIKE the thumbnail it went out with. Title B is the series' hook_b
(series search pack); a series without one gets a thumbnail test.

Pure rules plus a small file store. Nothing here calls YouTube or a model.
"""
import json
import os
import tempfile
import threading
import time

LEDGER = "_abtests.json"
STATUSES = ("running", "done", "not_available", "failed", "skipped")
MAX_VARIANTS = 3                     # YouTube's limit, the current one included
_LOCK = threading.RLock()
# Designs that read as the same picture at thumbnail size: the series cover with
# only a chapter number on it, cropped or centered.
_FAMILY = {"cover-badge": "cover", "cover-center": "cover"}


def _family(comp):
    return _FAMILY.get(comp or "", comp or "")


def thumb_variants(concepts_rec, n=MAX_VARIANTS - 1):
    """-> up to n concept dicts for thumbnails B, C: the concepts least like the
    one the video went out with (other design family first, then another focal
    panel), keeping the studio's ranking otherwise. One per design."""
    rec = concepts_rec or {}
    cs = [c for c in rec.get("concepts") or [] if c.get("id")]
    chosen = rec.get("chosen") or {}
    a_id = chosen.get("concept_id")
    a = next((c for c in cs if c["id"] == a_id), None) or {}
    a_fam = _family(chosen.get("composition") or a.get("composition"))
    a_panel = chosen.get("focal_panel") or a.get("focal_panel")
    rest = [c for c in cs if c["id"] != a_id]
    ranked = sorted(enumerate(rest), key=lambda ic: (
        _family(ic[1].get("composition")) == a_fam,          # same look as A last
        ic[1].get("focal_panel") == a_panel,                  # then same panel
        ic[0]))                                               # then the studio's order
    out, fams = [], {a_fam}
    for _, c in ranked:
        f = _family(c.get("composition"))
        if f in fams:
            continue
        fams.add(f)
        out.append(c)
        if len(out) >= n:
            break
    return out


def plan(current_title, title_b=None, thumbs=(), title_problems=()):
    """What to set up in Test & compare for one video.

    A title test when the series has title B (its hook_b), else a thumbnail
    test when there is at least one other thumbnail; one test per video, as
    YouTube allows. -> {"type": "title"|"thumbnail"|None, "titles": [...],
    "thumbnails": [concept ids for B, C], "why": "..."}"""
    thumbs = [t for t in thumbs if t][:MAX_VARIANTS - 1]
    if title_b and current_title and title_b.strip() != current_title.strip():
        return {"type": "title", "titles": [current_title, title_b], "thumbnails": [],
                "why": "the series has an approved title B (hook_b)"}
    if thumbs:
        why = "thumbnails from this video's own concepts"
        if title_problems:
            why += "; no title test: " + "; ".join(title_problems)
        return {"type": "thumbnail", "titles": [], "thumbnails": list(thumbs), "why": why}
    return {"type": None, "titles": [], "thumbnails": [],
            "why": "no title B for the series and no other thumbnail concept for this video"}


# ------------------------------------------------------------------ ledger
def _path(root):
    return os.path.join(root, LEDGER)


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get(root, video_id):
    return load(root).get(video_id)


def mark(root, video_id, status, actor="", **fields):
    """Record what happened to one video's test. -> the saved entry.
    Entries are kept per YouTube video id; each change keeps the one before it
    in "history" (who, when, status)."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if not video_id or len(video_id) != 11:
        raise ValueError("video_id must be the 11-character YouTube id")
    with _LOCK:
        d = load(root)
        prev = d.get(video_id) or {}
        hist = list(prev.get("history") or [])
        if prev:
            hist.append({k: prev.get(k) for k in ("status", "at", "by", "note")})
        entry = {**{k: v for k, v in prev.items() if k != "history"}, **{k: v for k, v in fields.items() if v is not None},
                 "status": status, "at": time.time(), "by": actor or "", "history": hist[-20:]}
        d[video_id] = entry
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".abtests.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, sort_keys=True)
        os.replace(tmp, _path(root))
        return entry
