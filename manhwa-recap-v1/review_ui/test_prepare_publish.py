"""Ready to publish by itself (owner, 2026-10-05, "like the scrapper"): after a
video exists its title, description and tags come from the SEO suggestions and
a thumbnail is picked — without a click — and nothing the owner typed is ever
replaced. The SEO model call is faked; thumbnails are the real local copilot."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import ingest
    import server
    import seo as _seo
    import thumbnail as _tb
    from test_thumbnail_studio import make_project
    root = tempfile.mkdtemp(prefix="prep_")
    saved = (ingest.PROJECTS, server.api_seo_generate)
    ingest.PROJECTS = root
    calls = []

    def fake_seo(body):
        calls.append(body.name)
        pdir = server.project_dir_for(body.project)
        _seo.put(pdir, body.name, {"titles": [{"text": "He Came Back 1000 Years Stronger", "recommended": True}],
                                   "description": "Yu Shin returns to the sect…", "description_short": "Short",
                                   "tags": ["regressor", "manhwa recap"], "hashtags": ["#manhwa"]})
        return {"ok": True}
    server.api_seo_generate = fake_seo
    try:
        a = make_project(root, "series-q_1", "Series Q", "1")
        server._prepare_publish(a, "final_a.mp4")
        md = {**server.publish_defaults(a), **(server.load_publish(a).get("final_a.mp4") or {})}
        check("the title is filled from the suggestions", md["title"] == "He Came Back 1000 Years Stronger")
        # Spec 06 B2: block 1 (hook + series + chapter) leads; the writer's
        # summary is block 2; hashtags close it. B4: aliases follow the tags.
        blocks = md["description"].split("\n\n")
        check("the description is filled (with its hashtags)",
              len(blocks) >= 3 and blocks[1].startswith("Yu Shin returns") and "#manhwa" in blocks[-1])
        check("the tags are filled", md["tags"][:2] == ["regressor", "manhwa recap"])
        check("a thumbnail is picked", bool(_tb.path_for(a, "final_a.mp4")))
        server._prepare_publish(a, "final_a.mp4")
        check("running it again makes no second paid call", calls == ["final_a.mp4"])

        b = make_project(root, "series-r_2", "Series R", "2")
        store = server.load_publish(b)
        store["final_a.mp4"] = {**server.publish_defaults(b), "title": "My own title"}
        json.dump(store, open(os.path.join(b, server.PUBLISH_NAME), "w"))
        server._prepare_publish(b, "final_a.mp4")
        md = {**server.publish_defaults(b), **(server.load_publish(b).get("final_a.mp4") or {})}
        check("a title the owner typed is kept", md["title"] == "My own title")
        check("...while the untouched fields are still filled", md["tags"][:2] == ["regressor", "manhwa recap"])

        def broke(body):
            raise server.usage.UsageCapExceeded("MAX_DAILY_SPEND_USD would be exceeded")
        server.api_seo_generate = broke
        c = make_project(root, "series-s_3", "Series S", "3")
        server._prepare_publish(c, "final_a.mp4")
        check("over budget: SEO waits, the thumbnail is still picked", bool(_tb.path_for(c, "final_a.mp4")))
        # As Scrapper: SEO while making (no video yet), reused by the render.
        server.api_seo_generate = fake_seo
        calls.clear()
        d = make_project(root, "series-t_4", "Series T", "4")
        os.remove(os.path.join(d, "exports", "final_a.mp4"))
        server._prepare_publish(d, server.DRAFT)
        dm = server.publish_defaults(d)
        check("a chapter gets its title/description/tags while making, before any video",
              dm["title"] == "He Came Back 1000 Years Stronger" and calls == [server.DRAFT])
        open(os.path.join(d, "exports", "final_b.mp4"), "wb").write(b"0" * 64)
        server._prepare_publish(d, "final_b.mp4")
        md = {**server.publish_defaults(d), **(server.load_publish(d).get("final_b.mp4") or {})}
        check("...the render reuses them: no second paid call, and the thumbnail is picked",
              calls == [server.DRAFT] and md["title"] == "He Came Back 1000 Years Stronger"
              and bool(_tb.path_for(d, "final_b.mp4")))

        # Catch-up step: with no budget left it never calls the model.
        e = make_project(root, "series-u_5", "Series U", "5")
        calls.clear()
        real_left, real_rows = server._budget_left, server._chapter_rows
        server._budget_left = lambda: 0.0
        server._chapter_rows = lambda: [{"id": "series-u_5", "status": {"key": "video_ready"}}]
        try:
            server._publish_prep_pass()
            check("catch-up with no budget: thumbnail made, no paid call",
                  not calls and bool(_tb.path_for(e, "final_a.mp4")))
            server._budget_left = lambda: 5.0
            server._publish_prep_pass()
            check("catch-up with budget: SEO filled", calls == ["final_a.mp4"])
        finally:
            server._budget_left, server._chapter_rows = real_left, real_rows

        import thumbnail_studio as ts
        from PIL import Image
        im = Image.new("RGBA", (500, 1000))
        out = ts._trim_watermark_bands(im)
        # the trims are settings (THUMB_COVER_TRIM_TOP/BOTTOM; bottom raised to 32% on
        # 2026-10-06 so a cover's title band never shows): assert the configured cut
        check("the cover's watermark/title bands are trimmed at the configured %",
              out.size == (500, 1000 - int(1000 * ts.COVER_TRIM_TOP) - int(1000 * ts.COVER_TRIM_BOTTOM)))
        src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
        check("a finished ingest prepares the draft SEO", "args=(project_dir_for(meta[\"id\"]), DRAFT)" in src)
        check("every new render prepares itself, then copies to Drive",
              "threading.Thread(target=_after_export, args=(pdir, res.get(\"output\"))" in src)
    finally:
        ingest.PROJECTS, server.api_seo_generate = saved


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
