"""Phase A review-flow tests (Session 27).

The rule worth protecting: an approval belongs to the CUT it judged. If the
timeline changes afterwards, the verdict must show as superseded rather than
silently continuing to look like a current approval.

Run: python3 test_review.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import ingest as ing
import server as srv
import review_page


def _seg(i, panel="p1", dur=3.0, included=True, crop=None, beatfile=None):
    return {"seg_index": i, "panel_id": panel, "dur": dur, "start": 0.0, "end": dur,
            "user_included": included, "crop_bbox_norm": crop,
            "beats": [{"index": i, "text": "x", "start": 0.0, "end": dur,
                       "file": beatfile}]}


def _project(root, name, segs, exports=()):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    json.dump(segs, open(os.path.join(d, "segments.json"), "w"))
    json.dump({"match_method": "gemini-embeddings+provenance+dp", "n_pages": 12},
              open(os.path.join(d, "project.json"), "w"))
    for i, e in enumerate(exports):
        p = os.path.join(d, "exports", e)
        open(p, "wb").write(b"\0" * 1000)
        os.utime(p, (time.time() - (len(exports) - i) * 60,) * 2)
    return d


def main():
    r = []
    root = tempfile.mkdtemp(prefix="review_")
    ing.PROJECTS = root
    segs = [_seg(0), _seg(1, panel="p2")]
    pa = _project(root, "proj_a", segs, ["final_old.mp4", "final_new.mp4"])
    _project(root, "proj_b", [_seg(0, panel="zzz")], ["final_b.mp4"])
    srv.active_project_dir = lambda: pa

    # ---- latest export
    name, pid = srv.latest_export("proj_a")
    r.append(("the newest export is picked by default", name == "final_new.mp4"))
    r.append(("...and it is attributed to the right project", pid == "proj_a"))
    r.append(("a project with no exports returns nothing, not an error",
              srv.latest_export("proj_b")[0] == "final_b.mp4"))

    # ---- signature reacts to the things you can see and hear
    sig0 = srv.cut_signature(pdir=pa)
    r.append(("a signature is stable across repeat calls", sig0 == srv.cut_signature(pdir=pa)))
    changed = [_seg(0), _seg(1, panel="DIFFERENT")]
    json.dump(changed, open(os.path.join(pa, "segments.json"), "w"))
    r.append(("swapping a panel changes the signature", srv.cut_signature(pdir=pa) != sig0))
    json.dump([_seg(0), _seg(1, panel="p2", dur=9.9)], open(os.path.join(pa, "segments.json"), "w"))
    r.append(("retiming a segment changes the signature", srv.cut_signature(pdir=pa) != sig0))
    json.dump([_seg(0), _seg(1, panel="p2", included=False)],
              open(os.path.join(pa, "segments.json"), "w"))
    r.append(("unticking a segment changes the signature", srv.cut_signature(pdir=pa) != sig0))
    json.dump(segs, open(os.path.join(pa, "segments.json"), "w"))
    r.append(("restoring the cut restores the signature", srv.cut_signature(pdir=pa) == sig0))

    # ---- record round-trip
    out = srv.api_review_save(srv.ReviewIn(project="proj_a", name="final_new.mp4",
                                           status="approved", notes="looks good"))
    r.append(("a verdict is stored", out["review"]["status"] == "approved"))
    r.append(("...with its notes", out["review"]["notes"] == "looks good"))
    r.append(("...pinned to the cut it judged", out["review"]["cut_signature"] == sig0))
    r.append(("...and is not superseded yet", out["review"]["superseded"] is False))
    r.append(("the record persists to reviews.json",
              json.load(open(os.path.join(pa, "reviews.json")))["final_new.mp4"]["status"] == "approved"))

    # ---- THE important one: editing the cut supersedes the approval
    json.dump([_seg(0), _seg(1, panel="p2", dur=7.5)],
              open(os.path.join(pa, "segments.json"), "w"))
    st = srv.review_state(pa, "final_new.mp4")
    r.append(("editing the timeline marks the approval superseded", st["superseded"] is True))
    r.append(("...while the stored status is unchanged", st["status"] == "approved"))
    json.dump(segs, open(os.path.join(pa, "segments.json"), "w"))
    r.append(("reverting the edit clears superseded",
              srv.review_state(pa, "final_new.mp4")["superseded"] is False))

    # ---- notes save independently of a verdict
    srv.api_review_save(srv.ReviewIn(project="proj_a", name="final_old.mp4",
                                     notes="halfway through"))
    st = srv.review_state(pa, "final_old.mp4")
    r.append(("notes save without a verdict", st["notes"] == "halfway through"))
    r.append(("...leaving the status pending", st["status"] == "review_pending"))
    r.append(("...and not pinning a cut signature", st["cut_signature"] is None))

    # ---- changing a verdict keeps the old one in history
    srv.api_review_save(srv.ReviewIn(project="proj_a", name="final_new.mp4",
                                     status="sent_back", notes="fix seg 4"))
    st = srv.review_state(pa, "final_new.mp4")
    r.append(("a changed verdict is recorded", st["status"] == "sent_back"))
    r.append(("...and the previous one is kept in history",
              any(h["status"] == "approved" for h in st["history"])))

    # ---- explicit project routing, not the active project
    srv.api_review_save(srv.ReviewIn(project="proj_b", name="final_b.mp4", status="approved"))
    r.append(("a review writes to the project named in the request, not the active one",
              os.path.exists(os.path.join(root, "proj_b", "reviews.json"))
              and "final_b.mp4" in json.load(open(os.path.join(root, "proj_b", "reviews.json")))))
    r.append(("...and does not leak into the active project's file",
              "final_b.mp4" not in json.load(open(os.path.join(pa, "reviews.json")))))

    # ---- bad input is refused
    for kw, why in ((dict(project="proj_a", name="x.mp4", status="bogus"), "bad status"),
                    (dict(project="proj_a", name="notavideo", status="approved"), "bad filename"),
                    (dict(project="../etc", name="x.mp4"), "path traversal")):
        try:
            srv.api_review_save(srv.ReviewIn(**kw)); ok = False
        except Exception:
            ok = True
        r.append((f"{why} is refused", ok))

    # ---- a deleted export keeps its record and is reported missing
    os.remove(os.path.join(pa, "exports", "final_new.mp4"))
    b = srv.api_review(project="proj_a", name="final_new.mp4")
    r.append(("a deleted export still returns its review record",
              b["review"]["status"] == "sent_back"))
    r.append(("...and is flagged as having no file", b["file_present"] is False))

    # ---- a project with no exports at all
    _project(root, "proj_empty", [_seg(0)], [])
    b = srv.api_review(project="proj_empty")
    r.append(("a project with no export explains itself instead of erroring",
              b.get("missing") is True and "no export" in b.get("reason", "")))

    # ---- the bundle carries the QC signals the page shows
    b = srv.api_review(project="proj_a", name="final_old.mp4")
    qc = b.get("qc", {})
    r.append(("the bundle carries the QC signals",
              "match_method" in qc and "validation" in qc and "long_holds" in qc))
    r.append(("...including whether matching was semantic", qc.get("semantic") is True))

    # ---- the page's own JavaScript must parse
    html = review_page.build_review_html()
    body = max(re.findall(r"<script>(.*?)</script>", html, re.S), key=len)
    js = os.path.join(tempfile.mkdtemp(), "r.js")
    open(js, "w").write(body)
    p = subprocess.run(["node", "--check", js], capture_output=True, text=True)
    r.append(("the review page's inline JS parses", p.returncode == 0))
    if p.returncode:
        print(p.stderr[:400])

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
