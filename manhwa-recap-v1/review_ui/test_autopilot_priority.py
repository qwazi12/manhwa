"""Make next (owner, 2026-10-05): the chapters the owner ticks, in their order,
are made before autopilot's round robin; made ones leave the list; a failing
one never holds up the rest; the limits and budget still apply.
Temp root, fake watchlist and queue; no network, no money."""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
from test_autopilot import Fake, series   # noqa: E402
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import autopilot as ap
    root = tempfile.mkdtemp(prefix="apn_")
    view = [series("alpha", 1, range(1, 11)), series("bravo", 2, range(1, 6)), series("charlie", 3, [1, 2])]
    fake = Fake(view)
    now = time.time()
    ap.update_settings(root, {"enabled": True, "per_day": 10, "budget_usd": 10})

    ap.priority_edit(root, "add", "charlie", ["2"])
    ap.priority_edit(root, "add", "alpha", ["3", "2"])       # an OLD chapter, outside the latest-3 window
    ap.priority_edit(root, "add", "alpha", ["3"])            # duplicate ignored
    check("entries keep the order they were ticked, no duplicates",
          [(e["series_id"], e["chapter"]) for e in ap.settings(root)["priority"]]
          == [("charlie", "2"), ("alpha", "3"), ("alpha", "2")])

    picks = []
    for _ in range(4):
        r = ap.tick(root, fake.deps(), now)
        picks.append((r["series"], r["chapter"]) if r else None)
    check("Make next goes first, in order, then round robin carries on (fewest made first)",
          picks == [("Charlie", "2"), ("Alpha", "3"), ("Alpha", "2"), ("Bravo", "3")])
    st = ap.status(root, fake.deps(), now)
    check("chapters waiting their turn show as in line (queued), not 'being made'",
          [p["state"] for p in st["priority"]] == ["queued", "queued", "queued"])
    stx = ap.load(root)
    p0 = next(e for e in stx["ledger"].values() if e["series_id"] == "charlie")["project"]
    dps = dict(fake.deps(), running_projects=lambda: {p0})
    check("the one actually running shows as being made",
          [p["state"] for p in ap.status(root, dps, now)["priority"]] == ["running", "queued", "queued"])

    for i in range(len(fake.queued)):
        ap.on_job_end(root, f"job{i + 1}", "done", None, 0.3)
    st = ap.status(root, fake.deps(), now)
    check("made chapters leave the list by themselves", st["priority"] == [])

    # a failing entry is skipped, not a roadblock
    ap.priority_edit(root, "add", "bravo", ["1", "2"])
    r = ap.tick(root, fake.deps(), now)
    check("next pick is the first entry", r and (r["series"], r["chapter"]) == ("Bravo", "1"))
    job = f"job{len(fake.queued)}"
    ap.on_job_end(root, job, "error", "boom", None)
    r = ap.tick(root, fake.deps(), now)
    check("after a failure the next entry runs (the failed one waits its cooldown)",
          r and (r["series"], r["chapter"]) == ("Bravo", "2"))
    rows = {p["chapter"]: p for p in ap.status(root, fake.deps(), now)["priority"]}
    check("the failed entry says why", rows["1"]["state"] == "waiting" and "failed" in rows["1"]["reason"])

    ap.priority_edit(root, "add", "alpha", ["99"])
    rows = {p["chapter"]: p for p in ap.status(root, fake.deps(), now)["priority"]}
    check("a chapter not released yet waits with the reason", rows["99"]["state"] == "waiting" and "not on the source" in rows["99"]["reason"])

    ap.priority_edit(root, "top", "alpha", ["99"])
    check("move to top", ap.settings(root)["priority"][0]["chapter"] == "99")
    ap.priority_edit(root, "set", order=[{"series_id": "bravo", "chapter": "2"}, {"series_id": "alpha", "chapter": "99"}])
    check("set a full order", [e["chapter"] for e in ap.settings(root)["priority"]][:2] == ["2", "99"])
    ap.priority_edit(root, "remove", "alpha", ["99"])
    check("remove", all(e["chapter"] != "99" for e in ap.settings(root)["priority"]))

    # limits still apply
    ap.priority_edit(root, "clear")
    ap.priority_edit(root, "add", "bravo", ["3"])
    fake.ap_spent = 100.0
    check("the budget still applies to Make next", ap.tick(root, fake.deps(), now) is None
          and "budget" in ap.runtime["last_result"])
    fake.ap_spent = 0.0
    ap.update_settings(root, {"enabled": False})
    check("autopilot off: nothing starts, even from Make next", ap.tick(root, fake.deps(), now) is None)
    # forecast: everything scheduled, in order, with its day
    root2 = tempfile.mkdtemp(prefix="apf_")
    ap.update_settings(root2, {"enabled": True, "per_day": 2, "budget_usd": 10})
    ap.priority_edit(root2, "add", "charlie", ["2"])
    f = ap.status(root2, fake.deps(), now)["forecast"]
    check("forecast starts with Make next, then round robin",
          [(x["series_id"], x["chapter"], x["from"]) for x in f[:4]]
          == [("charlie", "2", "make_next"), ("alpha", "8", "round_robin"), ("bravo", "3", "round_robin"), ("alpha", "9", "round_robin")])
    check("forecast gives each one a day from the chapters-a-day limit", [x["day"] for x in f[:4]] == [0, 0, 1, 1])
    check("a pinned chapter isn't listed twice", sum(1 for x in f if (x["series_id"], x["chapter"]) == ("charlie", "2")) == 1)
    # Mix (Scrapper-style) + undo + groups
    import random
    root3 = tempfile.mkdtemp(prefix="apm_")
    ap.priority_edit(root3, "add", "alpha", ["8", "9", "10"])
    ap.priority_edit(root3, "add", "bravo", ["3", "4"])
    ap.priority_edit(root3, "add", "charlie", ["1"])
    before = [(e["series_id"], e["chapter"]) for e in ap.settings(root3)["priority"]]
    ap.priority_edit(root3, "mix", mode="round_robin")
    after = [(e["series_id"], e["chapter"]) for e in ap.settings(root3)["priority"]]
    firsts = {sid for sid, _ in after[:3]}
    check("round-robin mix: one chapter from each series in turn", firsts == {"alpha", "bravo", "charlie"})
    check("...each series stays in story order", [c for s_, c in after if s_ == "alpha"] == ["8", "9", "10"])
    ap.priority_edit(root3, "undo")
    check("Undo restores the order before the mix", [(e["series_id"], e["chapter"]) for e in ap.settings(root3)["priority"]] == before)
    m = ap.mix([{"series_id": "a", "chapter": c} for c in ("1", "2", "3")] + [{"series_id": "b", "chapter": c} for c in ("7", "8")],
               "random", random.Random(3))
    check("random mix keeps story order within a series", [e["chapter"] for e in m if e["series_id"] == "a"] == ["1", "2", "3"])
    m = ap.mix([{"series_id": "a", "chapter": "1"}, {"series_id": "b", "chapter": "7"}, {"series_id": "a", "chapter": "2"}], "by_series", random.Random(1))
    check("by-series mix keeps each series together", [e["series_id"] for e in m] in (["a", "a", "b"], ["b", "a", "a"]))
    ap.priority_edit(root3, "group_top", "charlie")
    check("move a whole series to the top", ap.settings(root3)["priority"][0]["series_id"] == "charlie")
    ap.priority_edit(root3, "group_remove", "alpha")
    check("remove a whole series", all(e["series_id"] != "alpha" for e in ap.settings(root3)["priority"]))
    try:
        ap.priority_edit(root, "nope")
        check("unknown action refused", False)
    except ValueError:
        check("unknown action refused", True)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
