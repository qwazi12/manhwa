"""Publishing fixes from the owner's ch.30 test (2026-10-04): every publish is
a queue row (a video posted from Video review stayed 'queued'), a stranded
row settles from the publish record, private YouTube posts get a youtu.be
link from their id, and switching the schedule on doesn't fire slots that
already passed. Temp dirs; no network."""
import json
import os
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []
ET = ZoneInfo("America/New_York")


def check(name, ok):
    R.append((name, bool(ok)))


def at(hm, day=4):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=ET).timestamp()


def main():
    import publish_queue as pq
    import server
    import studio_settings as ss
    root = tempfile.mkdtemp(prefix="pf_")
    pq.add(root, [{"project": "ch30", "name": "v.mp4"}])
    it = pq.ensure_posting(root, "ch30", "v.mp4", "job1", day="2026-10-04")
    d = pq.load(root)
    check("publishing a queued video marks ITS row posting (no second row)",
          len(d["items"]) == 1 and it["status"] == "posting" and it["job"] == "job1")
    pq.ensure_posting(root, "other", "w.mp4", "job2")
    check("publishing a video that was never queued creates its row",
          any(x["project"] == "other" and x["status"] == "posting" for x in pq.load(root)["items"]))

    # the live case: a row left 'queued' while the video was posted elsewhere
    root2 = tempfile.mkdtemp(prefix="pf_")
    pq.add(root2, [{"project": "ch30", "name": "v.mp4"}])
    rec = {"status": "published", "results": [{"status": "published", "url": "https://youtu.be/mCRcBjKWiEI"}]}
    pq.sync(root2, lambda p, n: rec)
    check("a queued row whose video was already posted settles as posted",
          pq.load(root2)["items"][0]["status"] == "posted")
    root3 = tempfile.mkdtemp(prefix="pf_")
    pq.add(root3, [{"project": "ch30", "name": "v.mp4"}])
    pq.sync(root3, lambda p, n: {"status": "failed", "error": "old failure"})
    check("...but an old failure doesn't touch a queued row", pq.load(root3)["items"][0]["status"] == "queued")

    check("a private YouTube post gets a youtu.be link from its id",
          server._post_url({"post_url": "Post uploaded as Private. No public URL available.",
                            "platform_post_id": "mCRcBjKWiEI"}, "youtube") == "https://youtu.be/mCRcBjKWiEI")
    check("...a real URL is kept as it is",
          server._post_url({"post_url": "https://www.youtube.com/watch?v=x", "platform_post_id": "x"}, "youtube")
          == "https://www.youtube.com/watch?v=x")

    S = {"enabled": True, "times": ["11:00", "19:00"], "tz": "America/New_York", "per_channel_per_day": 1,
         "enabled_at": at("15:51")}
    q = {"items": [{"id": "a", "status": "queued"}]}
    check("switching on at 15:51 does not fire the 11:00 slot that day",
          pq.decide(S, q, at("15:57"), lambda x: ["mk:youtube"])["action"] == "wait")
    check("...19:00 still fires", pq.decide(S, q, at("19:01"), lambda x: ["mk:youtube"])["action"] == "post")
    check("...and the next day 11:00 fires again",
          pq.decide(S, q, at("11:01", day=5), lambda x: ["mk:youtube"])["action"] == "post")

    import ingest
    saved = ingest.PROJECTS
    ingest.PROJECTS = tempfile.mkdtemp(prefix="pf_")
    try:
        ss.update({"schedule": {"enabled": False}})
        _b, after = ss.update({"schedule": {"enabled": True}})
        check("switching the schedule on records when", after["schedule"].get("enabled_at"))
    finally:
        ingest.PROJECTS = saved
    import seo
    sp = tempfile.mkdtemp(prefix="seo_")
    json.dump({"series": "A Regressor's Tale", "chapter": "30"}, open(os.path.join(sp, "project.json"), "w"))
    json.dump([{"seg_index": 0, "beats": [{"text": "Eunhyun watched. Then Makli Refining began. Young Kim Kim Young laughed. Makli Makli."}]}],
              open(os.path.join(sp, "segments.json"), "w"))
    json.dump({"characters": [{"name": "Seo Eunhyun", "role": "protagonist"}, {"name": "Kim Young-hoon"}]},
              open(os.path.join(sp, "series_bible.json"), "w"))
    card = seo.truth_card(sp)
    check("SEO characters come from the cast list only (no capital-word guesses)",
          card["characters"] == ["Seo Eunhyun", "Kim Young-hoon"])

    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    i = src.index("def os_publish")
    check("Video review's publish goes through the queue row", "ensure_posting(" in src[i:i + 2500])


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
