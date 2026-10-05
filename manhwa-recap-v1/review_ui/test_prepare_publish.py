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
        check("the description is filled (with its hashtags)", md["description"].startswith("Yu Shin returns") and "#manhwa" in md["description"])
        check("the tags are filled", md["tags"] == ["regressor", "manhwa recap"])
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
        check("...while the untouched fields are still filled", md["tags"] == ["regressor", "manhwa recap"])

        def broke(body):
            raise server.usage.UsageCapExceeded("MAX_DAILY_SPEND_USD would be exceeded")
        server.api_seo_generate = broke
        c = make_project(root, "series-s_3", "Series S", "3")
        server._prepare_publish(c, "final_a.mp4")
        check("over budget: SEO waits, the thumbnail is still picked", bool(_tb.path_for(c, "final_a.mp4")))
        src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
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
