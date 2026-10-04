"""⚙️ Settings & Channels (owner, 2026-10-04): export at 1.25x, post to
Flamingo Remix (mk) as PUBLIC by default, every setting visible on one page.
Temp projects root, upload-post status faked; no network, no money."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
os.environ.pop("EXPORT_SPEED", None)
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import ingest
    import server
    import studio_settings as ss
    from fastapi.testclient import TestClient
    proj = tempfile.mkdtemp(prefix="set_")
    saved = (ingest.PROJECTS, server.os_status)
    ingest.PROJECTS = proj
    server.os_status = lambda: {"state": "ok", "detail": "connected", "accounts": [
        {"account_id": "mk:youtube", "network": "youtube", "username": "Flamingo Remix", "active": True},
        {"account_id": "default:youtube", "network": "youtube", "username": "Screen Central", "active": True}]}
    try:
        check("default export speed is 1.25x", ss.export_speed() == 1.25)
        check("default posting: Flamingo Remix (mk), private until the owner saves public",
              ss.publish_defaults() == {"targets": ["mk:youtube"], "privacy": "private"})
        os.environ["EXPORT_SPEED"] = "1.5"
        check("a Railway EXPORT_SPEED still wins", ss.export_speed() == 1.5)
        os.environ.pop("EXPORT_SPEED")

        c = TestClient(server.app)
        d = c.get("/api/settings/overview").json()
        check("overview has every card", all(k in d for k in
              ("connection", "spending", "autopilot", "channels", "voice", "export", "storage")))
        check("overview lists the connected channels",
              [a["account_id"] for a in d["channels"]["accounts"]] == ["mk:youtube", "default:youtube"])
        check("overview scheduler shape is what the page reads",
              set(d["connection"]["scheduler"]) == {"running", "last_run", "last_error"})
        check("overview export speed", d["export"]["speed"] == 1.25 and d["export"]["env_override"] is False)

        r = c.post("/api/settings", json={"export_speed": 1.1,
                                          "publish": {"targets": ["default:youtube"], "privacy": "unlisted"}})
        check("saving works", r.status_code == 200 and r.json()["settings"]["export_speed"] == 1.1)
        check("saved speed is used by export", ss.export_speed() == 1.1)
        check("publish defaults follow the saved channels",
              server.publish_defaults(proj)["targets"] == ["default:youtube"]
              and server.publish_defaults(proj)["privacy"] == "unlisted")
        check("speed outside 1.0-2.0 is refused", c.post("/api/settings", json={"export_speed": 3}).status_code == 400)
        check("unknown privacy is refused",
              c.post("/api/settings", json={"publish": {"privacy": "friends"}}).status_code == 400)
        check("a refused save changes nothing", ss.export_speed() == 1.1)
    finally:
        ingest.PROJECTS, server.os_status = saved

    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    th = open(os.path.join(HERE, "theme.py"), encoding="utf-8").read()
    check("the page is wired: nav item, view, drawer, loader",
          'd="settings"' in th and "'settings'" in sb and 'id="d_settings"' in sb
          and "if (name === 'settings') loadSettings();" in sb)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
