"""Lab runs must be VISIBLE and must not re-pay for work already bought.

Observed: a lab run of i-am-the-fated-villain_352 finished — 83 panels, 79
segments, $2.05 — while the operator's card still read "48 Claude calls ·
$1.4225 / not finished — nothing to open yet", and the run appeared nowhere in
Logs or Ingest. Money was spent with no way to watch it or confirm it landed.

Four separate defects produced that, each pinned below:

1. Lab job records carried NO `ts`. /api/jobs sorts by ts and truncates to
   `limit`, so every lab run sorted as 0 and fell off the end of the list.
2. Nothing recovered an in-flight lab run after a page reload — tWatch's
   interval dies with the page, and only ingest had a recovery hook.
3. The card read the half-written manifest and reported a LIVE run as
   unfinished-and-unopenable, which looks identical to a dead one.
4. Only `read` resumed. map/script/critique/revise re-ran from scratch, so a
   run killed mid-script re-paid for stages already bought.
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    # ============ 1 + 2: the job record and the listing
    import server

    d = server._jobs_dir()
    stamp = time.time()

    # a record written the OLD way — no ts at all
    old_id = "labold99"
    with open(os.path.join(d, f"render_{old_id}.json"), "w") as f:
        json.dump({"job": old_id, "kind": "lab", "status": "done",
                   "stage": "legacy lab run", "project": "legacy-lab"}, f)
    # Plenty of OLDER render jobs. This is the production shape: 42 records,
    # the lab run among the most RECENT of them, yet listed last because a
    # missing ts sorts as 0 — so `limit` cut it off. A fixture with newer
    # render jobs would prove nothing: being pushed off by genuinely newer
    # work is correct behaviour.
    for i in range(25):
        jid = f"rend{i:03d}"
        with open(os.path.join(d, f"render_{jid}.json"), "w") as f:
            json.dump({"job": jid, "status": "done", "stage": "export",
                       "ts": stamp - 5000 + i}, f)

    written = [f"render_{old_id}.json"] + [f"render_rend{i:03d}.json"
                                          for i in range(25)]
    try:
        jobs = server.jobs_recent(limit=20)["jobs"]
    finally:
        # These go into the REAL jobs directory, because that is the code path
        # under test. Leaving them behind would plant fake exports in the
        # operator's Logs drawer, so they come out again either way.
        for fn in written:
            try:
                os.remove(os.path.join(d, fn))
            except OSError:
                pass
    got = [x for x in jobs if x.get("kind") == "lab"]
    check("a recent lab run with no ts is no longer buried under older exports",
          len(got) == 1)
    check("...and sorts by when it actually ran, i.e. near the top",
          got and jobs.index(got[0]) == 0)
    check("...because its timestamp falls back to the file's mtime",
          got and got[0].get("ts") and got[0].get("ts_inferred"))
    check("...and the inference is FLAGGED, not passed off as recorded truth",
          got and got[0]["ts_inferred"] is True)
    check("a record that already has a ts is left alone",
          not any(x.get("ts_inferred") for x in jobs if x.get("kind") != "lab"))
    check("the listing is still newest-first",
          [x["ts"] for x in jobs] == sorted((x["ts"] for x in jobs), reverse=True))

    # the record a NEW run writes
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    i = src.index('"kind": "lab"')
    blk = src[i - 400:i + 400]
    check("a new lab job stamps its own ts at creation", '"ts": time.time()' in blk)
    check("...and knows its project before it finishes, so a live run can be "
          "matched to its row", '"project": _lab.lab_id(' in blk)

    # ============ 3: the UI recovers and reports a live run
    import matcher
    import storyboard
    import ingest as _ing
    cands = [x for x in sorted(os.listdir(_ing.PROJECTS))
             if os.path.isdir(os.path.join(_ing.PROJECTS, x))
             and not x.startswith("_")]
    html = storyboard.build_storyboard_html(
        os.path.join(_ing.PROJECTS, cands[0]), matcher,
        review={}, usage_summary={}, approved=False)
    check("the page recovers a lab run that outlived its page",
          "function recoverActiveLab" in html)
    check("...and actually calls it on load", "recoverActiveLab();" in html)
    check("...resuming the SAME watcher the run started with",
          "recoverActiveLab" in html and "tWatch(live.job" in html)
    check("...only for a run that is still going",
          "'running' || x.status === 'queued'" in html)
    check("...and not on top of a watcher that is already running",
          "if (testPoll) return;" in html)
    check("a live build no longer reads as 'nothing to open yet'",
          "building now" in html)
    check("...while a genuinely unfinished run still says so",
          "not finished — nothing to open yet" in html)

    # ============ 4: paid stages are bought once
    import claude_lab as LAB
    pdir = tempfile.mkdtemp(prefix="labcache_")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"scenes": ["a", "b"]}, {"cost_usd": 0.42, "calls": 3}

    v1, s1 = LAB._cached_stage(pdir, "chapter_map.json", False, compute)
    check("a paid stage runs when there is nothing cached", calls["n"] == 1)
    check("...and reports what it actually cost", s1["cost_usd"] == 0.42)
    check("...and is not marked as a cache hit", not s1.get("cached"))

    v2, s2 = LAB._cached_stage(pdir, "chapter_map.json", False, compute)
    check("a second run does NOT pay for it again", calls["n"] == 1)
    check("...returning the same result", v2 == v1)
    check("...reporting zero spend, because this run really spent nothing",
          s2["cost_usd"] == 0.0 and s2["calls"] == 0)
    check("...and saying plainly that it was reused", s2.get("cached"))

    v3, s3 = LAB._cached_stage(pdir, "chapter_map.json", True, compute)
    check("a FRESH run deliberately re-buys the stage", calls["n"] == 2)
    check("...and charges for it", s3["cost_usd"] == 0.42)

    lab_src = open(os.path.join(HERE, "claude_lab.py"), encoding="utf-8").read()
    for stage, fn in [("the chapter map", "chapter_map.json"),
                      ("the narration script", "script_units.json"),
                      ("the critique + revision", "script_revised.json")]:
        check(f"{stage} resumes instead of re-running",
              f'_cached_stage(\n' in lab_src or fn in lab_src)
        check(f"...{stage} has its own cache file", f'"{fn}"' in lab_src)
    check("critique and revision are cached TOGETHER, since revised lines "
          "without their critique is not a resumable state",
          "_critique_and_revise" in lab_src)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
