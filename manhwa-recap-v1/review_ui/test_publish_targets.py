"""Publishing goes only to the channels the operator picked, one Upload-Post
request per profile. No real network calls — the uploader is mocked."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server  # noqa: E402

R = []


def check(name, ok):
    R.append((name, bool(ok)))


class FakeUP:
    def __init__(self, fail_profile=None):
        self.calls, self.fail = [], fail_profile

    def upload_video_post(self, video, md, targets, thumbnail_path=None, on_step=None):
        self.calls.append(list(targets))
        if self.fail and targets[0].startswith(self.fail + ":"):
            return {"success": False, "error": "boom"}
        return {"success": True, "request_id": "r-" + targets[0]}


def run(targets, fake):
    pdir = tempfile.mkdtemp(prefix="pubt_")
    rec = {}
    saved = {k: getattr(server, k) for k in
             ("_get_publish_backend", "publish_eligibility", "load_publish",
              "publish_defaults", "_publish_record", "_control_gate", "_persist_job")}
    server._get_publish_backend = lambda: ("upload_post", fake)
    server.publish_eligibility = lambda p, n: {"ready": True, "blockers": []}
    server.load_publish = lambda p: {"v.mp4": {"targets": targets, "title": "t"}}
    server.publish_defaults = lambda p: {}
    server._publish_record = lambda p, n, **kw: rec.update(kw)
    server._control_gate = lambda *a, **k: None
    server._persist_job = lambda *a, **k: None
    server.JOBS["t1"] = {}
    try:
        server._run_publish_job("t1", pdir, "v.mp4")
    finally:
        for k, v in saved.items():
            setattr(server, k, v)
    return rec


def main():
    f = FakeUP()
    rec = run(["mk:youtube", "default:youtube"], f)
    check("two profiles -> two separate uploads", len(f.calls) == 2)
    check("each upload carries only its own profile's channel",
          sorted(f.calls) == [["default:youtube"], ["mk:youtube"]])
    check("both recorded published", rec.get("status") == "published"
          and len(rec.get("results", [])) == 2)

    f = FakeUP()
    rec = run(["default:youtube"], f)
    check("one picked channel -> only that channel is uploaded",
          f.calls == [["default:youtube"]])

    f = FakeUP(fail_profile="default")
    rec = run(["mk:youtube", "default:youtube"], f)
    st = {r["account_id"]: r["status"] for r in rec.get("results", [])}
    check("one profile failing is reported as partial, not published",
          rec.get("status") == "partial" and st.get("default:youtube") == "failed"
          and st.get("mk:youtube") == "published")

    src = open(os.path.join(HERE, "review_page.py"), encoding="utf-8").read()
    check("channel picker does not pre-tick accounts",
          "accounts.map(function(a){ return a.account_id; })" not in src
          and "md.targets : []" in src)
    check("confirm dialog no longer hardcodes Outstand",
          "uploaded to Outstand" not in src)
    check("confirm dialog names only picked targets",
          "picked.indexOf(a.account_id)" in src)

    for n, ok in R:
        print(("PASS " if ok else "FAIL ") + n)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
