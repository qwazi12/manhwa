"""Phase B tests — publish PREPARATION only (Session 27).

Nothing here posts anything. The rules worth protecting: a package can only be
built from an APPROVED, CURRENT cut, privacy defaults to private, and metadata
that YouTube would reject never reaches a package.

Run: python3 test_publish_prep.py
"""
import io
import json
import os
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import ingest as ing
import server as srv


def _seg(i, panel="p1", dur=3.0):
    return {"seg_index": i, "panel_id": panel, "dur": dur, "start": 0.0, "end": dur,
            "user_included": True, "crop_bbox_norm": None,
            "panel_file": os.path.join(HERE, "does_not_exist.png"),
            "beats": [{"index": i, "text": "x", "start": 0.0, "end": dur}]}


def _project(root, name, exports=("final_x.mp4",)):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    os.makedirs(os.path.join(d, "crops"), exist_ok=True)
    json.dump([_seg(0), _seg(1, "p2")], open(os.path.join(d, "segments.json"), "w"))
    json.dump({"series": "Overgeared", "chapter": "338"},
              open(os.path.join(d, "project.json"), "w"))
    for e in exports:
        open(os.path.join(d, "exports", e), "wb").write(b"\0" * 100)
    return d


def _err_code(fn, *a, **kw):
    try:
        fn(*a, **kw); return None
    except Exception as e:
        return getattr(e, "status_code", "raised")


def main():
    r = []
    root = tempfile.mkdtemp(prefix="pub_")
    ing.PROJECTS = root
    pd = _project(root, "proj")
    srv.active_project_dir = lambda: pd
    NAME = "final_x.mp4"

    # ---- defaults come from the project
    md = srv.publish_defaults(pd)
    r.append(("the title is seeded from series and chapter",
              md["title"] == "Overgeared Chapter 338 — Recap"))
    r.append(("privacy defaults to PRIVATE, never public", md["privacy"] == "private"))
    r.append(("synthetic-narration disclosure defaults ON",
              md["synthetic_disclosure"] is True))
    r.append(("made-for-kids defaults off", md["made_for_kids"] is False))

    # ---- validation mirrors the platform's limits
    ok = dict(md)
    r.append(("clean metadata has no problems", srv.validate_publish(ok) == []))
    r.append(("an empty title is a problem",
              srv.validate_publish({**ok, "title": "  "}) != []))
    r.append(("an over-long title is a problem",
              srv.validate_publish({**ok, "title": "x" * 101}) != []))
    r.append(("an over-long description is a problem",
              srv.validate_publish({**ok, "description": "x" * 5001}) != []))
    r.append(("too many tag characters is a problem",
              srv.validate_publish({**ok, "tags": ["x" * 100] * 8}) != []))
    r.append(("an unknown privacy value is a problem",
              srv.validate_publish({**ok, "privacy": "semi-public"}) != []))
    r.append(("an unknown category is a problem",
              srv.validate_publish({**ok, "category_id": "999"}) != []))
    r.append(("scheduling requires privacy=private",
              srv.validate_publish({**ok, "privacy": "public",
                                    "publish_at": "2026-09-20T15:00:00Z"}) != []))

    # ---- save round-trip
    out = srv.api_publish_save(srv.PublishIn(
        project="proj", name=NAME,
        metadata={"title": "My Recap", "tags": "a, b , c", "privacy": "unlisted"}))
    r.append(("metadata saves", out["metadata"]["title"] == "My Recap"))
    r.append(("a comma string becomes a tag list",
              out["metadata"]["tags"] == ["a", "b", "c"]))
    r.append(("it persists to publish.json",
              json.load(open(os.path.join(pd, "publish.json")))[NAME]["title"] == "My Recap"))
    r.append(("unset fields fall back to defaults",
              out["metadata"]["synthetic_disclosure"] is True))

    # ---- readiness is gated on the REVIEW verdict
    rd = srv.publish_readiness(pd, NAME)
    r.append(("an unreviewed export cannot be packaged", rd["ready"] is False))
    r.append(("...and says why", any("not been approved" in b for b in rd["blockers"])))
    r.append(("packaging an unreviewed export is refused (409)",
              _err_code(srv.api_publish_package, project="proj", name=NAME) == 409))

    srv.api_review_save(srv.ReviewIn(project="proj", name=NAME, status="approved"))
    r.append(("an approved export is ready",
              srv.publish_readiness(pd, NAME)["ready"] is True))

    # ---- a changed cut blocks packaging again
    json.dump([_seg(0), _seg(1, "CHANGED")], open(os.path.join(pd, "segments.json"), "w"))
    rd = srv.publish_readiness(pd, NAME)
    r.append(("editing the cut blocks packaging again", rd["ready"] is False))
    r.append(("...naming the stale approval",
              any("cut changed" in b for b in rd["blockers"])))
    json.dump([_seg(0), _seg(1, "p2")], open(os.path.join(pd, "segments.json"), "w"))
    r.append(("restoring the cut unblocks it",
              srv.publish_readiness(pd, NAME)["ready"] is True))

    # ---- invalid metadata never reaches a package
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "x" * 200}))
    r.append(("a package is refused while metadata is invalid (400)",
              _err_code(srv.api_publish_package, project="proj", name=NAME) == 400))

    # ---- the package itself
    srv.api_publish_save(srv.PublishIn(
        project="proj", name=NAME,
        metadata={"title": "Overgeared 338 Recap", "tags": ["overgeared"],
                  "privacy": "private"}))
    resp = srv.api_publish_package(project="proj", name=NAME)
    z = zipfile.ZipFile(io.BytesIO(resp.body))
    names = z.namelist()
    r.append(("the package contains machine-readable metadata", "metadata.json" in names))
    r.append(("...and a human checklist", "upload-checklist.txt" in names))
    meta = json.loads(z.read("metadata.json"))
    r.append(("the metadata in the zip is what was saved",
              meta["title"] == "Overgeared 338 Recap"))
    txt = z.read("upload-checklist.txt").decode()
    r.append(("the checklist states the privacy setting", "PRIVACY: private" in txt))
    r.append(("...and carries the synthetic-content disclosure",
              "SYNTHETIC" in txt))
    r.append(("...and warns about rights before uploading",
              "rights to publish" in txt))
    r.append(("the video itself is NOT bundled",
              not any(n.endswith(".mp4") for n in names)))
    r.append(("it downloads as a zip",
              resp.media_type == "application/zip"))

    # ---- bad input
    r.append(("a non-mp4 name is refused",
              _err_code(srv.api_publish_save,
                        srv.PublishIn(project="proj", name="notavideo",
                                      metadata={})) == 400))
    r.append(("an unknown project is refused",
              _err_code(srv.api_publish, project="nope") == 404))

    # ---- a project with no export explains itself
    _project(root, "empty_proj", exports=())
    b = srv.api_publish(project="empty_proj")
    r.append(("a project with no export explains itself", b.get("missing") is True))

    for name, ok_ in r:
        print(("PASS " if ok_ else "FAIL ") + name)
    n = sum(1 for _, ok_ in r if ok_)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
