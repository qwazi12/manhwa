"""Custom thumbnails: validation, storage, endpoints, and the honesty note.

The point of most of these is that a thumbnail is refused EARLY and for a
stated reason. A file that slips through here is not caught until YouTube
rejects it, which is after a publish — far too late to be useful.
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")))

import ingest as ing              # noqa: E402
import server as srv              # noqa: E402
import thumbnail as tb            # noqa: E402

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def img(w=1280, h=720, fmt="JPEG", color=(40, 80, 160)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format=fmt)
    return buf.getvalue()


def err(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except tb.ThumbnailError as e:
        return str(e)
    except Exception as e:
        return "UNEXPECTED:" + type(e).__name__


def project(root):
    pdir = os.path.join(root, "proj_t")
    os.makedirs(os.path.join(pdir, "exports"), exist_ok=True)
    open(os.path.join(pdir, "exports", "final.mp4"), "wb").write(b"0" * 32)
    return pdir


def main():
    root = tempfile.mkdtemp(prefix="thumb_")
    ing.PROJECTS = root
    pdir = project(root)
    srv.active_project_dir = lambda: pdir

    # ---- what is refused, and whether the reason is usable
    check("an empty file is refused", err(tb.inspect, b"") is not None)
    m = err(tb.inspect, b"not an image at all")
    check("a non-image is refused", m is not None and "JPEG or PNG" in (m or ""))

    big = b"\xff\xd8\xff" + b"\x00" * (tb.MAX_BYTES + 1)
    m = err(tb.inspect, big)
    check("an over-2MB file is refused", m is not None)
    check("...and the message names the 2 MB limit", "2 MB" in (m or ""))

    m = err(tb.inspect, img(320, 180))
    check("an image narrower than YouTube's minimum is refused", m is not None)
    check("...and the message says how wide it must be",
          str(tb.MIN_WIDTH) in (m or ""))

    # A renamed file must not pass: the browser's MIME type is never trusted,
    # the bytes are sniffed.
    m = err(tb.inspect, img(1280, 720, fmt="GIF"))
    check("a GIF is refused even though it is a real image", m is not None)

    fmt, w, h = tb.inspect(img())
    check("a 1280x720 JPEG is accepted", (fmt, w, h) == ("JPEG", 1280, 720))
    fmt2, _, _ = tb.inspect(img(1280, 720, fmt="PNG"))
    check("a PNG is accepted too", fmt2 == "PNG")

    # ---- advisories are notes, not refusals
    check("an ideal image draws no advisories", tb.advisories(1280, 720) == [])
    adv = tb.advisories(1920, 1080)
    check("a non-ideal but valid size is allowed, with a note", len(adv) == 1)
    adv43 = tb.advisories(1024, 768)
    check("...and a non-16:9 image warns about cropping",
          any("16:9" in a for a in adv43))

    # ---- storage
    rec = tb.save(pdir, "final.mp4", img())
    check("saving records the real dimensions and size",
          rec["width"] == 1280 and rec["height"] == 720 and rec["bytes"] > 0)
    check("...under the export's own name", rec["file"].startswith("final.mp4"))
    got = tb.get(pdir, "final.mp4")
    check("the record reads back", got.get("file") == rec["file"])
    check("the file is really on disk", os.path.exists(tb.path_for(pdir, "final.mp4")))

    # Replacing with a different FORMAT must not strand the old file — that is
    # how a stale jpg would keep being served after a png replaced it.
    old_path = tb.path_for(pdir, "final.mp4")
    rec2 = tb.save(pdir, "final.mp4", img(1280, 720, fmt="PNG"))
    check("replacing switches to the new format", rec2["file"].endswith(".png"))
    check("...and deletes the previous file", not os.path.exists(old_path))

    # An entry whose file vanished must not be reported as present, or the
    # page renders a broken preview.
    os.remove(tb.path_for(pdir, "final.mp4"))
    check("an index entry whose file vanished is dropped, not served",
          tb.get(pdir, "final.mp4") == {})

    tb.save(pdir, "final.mp4", img())
    check("deleting removes it", tb.delete(pdir, "final.mp4") is True)
    check("...and deleting again is not an error", tb.delete(pdir, "final.mp4") is False)

    # Path traversal must not escape the project.
    tb.save(pdir, "../../escape.mp4", img())
    check("a traversing export name cannot write outside the project",
          not os.path.exists(os.path.join(root, "escape.mp4.jpg")))

    # ---- the honesty note: Outstand documents no thumbnail field
    n0 = tb.publish_note(False)
    n1 = tb.publish_note(True)
    check("with no thumbnail, the note says YouTube picks a frame",
          n0["sent_with_post"] is False and "auto-pick" in n0["detail"])
    check("with a thumbnail, the note STILL says it is not sent",
          n1["sent_with_post"] is False)
    check("...and says where to set it instead",
          "YouTube Studio" in n1["detail"])

    # ---- endpoints
    import asyncio

    class Req:
        def __init__(self, data):
            self._d = data

        async def body(self):
            return self._d

    def post(data, name="final.mp4", project=""):
        return asyncio.get_event_loop().run_until_complete(
            srv.thumbnail_upload(Req(data), project=project, name=name))

    def status(fn, *a, **kw):
        try:
            fn(*a, **kw)
            return 200
        except Exception as e:
            return getattr(e, "status_code", type(e).__name__)

    out = post(img())
    check("POST stores and returns the record", out["thumbnail"]["width"] == 1280)
    check("...and returns the not-sent note with it",
          out["note"]["sent_with_post"] is False)

    check("POST without an export name is a 400",
          status(lambda: post(img(), name="")) == 400)
    # 422, not 500: understood and refused for a reason worth showing.
    check("a refused image is a 422, not a server error",
          status(lambda: post(img(320, 180))) == 422)

    check("GET serves the stored file", status(
        srv.thumbnail_get, project="proj_t", name="final.mp4") == 200)

    class Body:
        project = "proj_t"
        name = "final.mp4"

    d = srv.thumbnail_delete(Body())
    check("delete endpoint removes it", d["removed"] is True)
    check("GET after delete is a 404", status(
        srv.thumbnail_get, project="proj_t", name="final.mp4") == 404)

    # ---- the publish form is built from ONE payload; the thumbnail is in it
    tb.save(pdir, "final.mp4", img())
    pay = srv._publish_payload(pdir, "proj_t", "final.mp4", {})
    check("the publish payload carries the thumbnail",
          (pay.get("thumbnail") or {}).get("width") == 1280)
    check("...its limits, so the UI states them without hardcoding",
          pay["thumbnail_limits"]["max_bytes"] == tb.MAX_BYTES)
    check("...and the not-sent note",
          pay["thumbnail_note"]["sent_with_post"] is False)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
