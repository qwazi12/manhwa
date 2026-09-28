"""A lab build must not claim to be ready when it cannot render.

`ready` used to mean only "segments.json exists". That is how
353-lab-yolo reached the operator with a green light and then failed at render:
beat 6 was 9.144s of audio scheduled inside a 4.699s window. A chapter that
cannot render is not a finished chapter.

The build now checks its OWN timeline, runs the mechanical repair (slicing
audio that several segments each claimed in full — the fix that took
352-lab-claude from 52 errors to 20), re-checks, and reports what survives.
Errors that survive are a DIFFERENT fault needing a pacing decision, so they
are surfaced rather than papered over.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import claude_lab as LAB

    # ---- the validator reports honestly on a clean and a broken timeline.
    # The logic now lives in storyboard_edit.check_and_repair (one copy, shared
    # with the Gemini ingest); patch its collaborators on the REAL module.
    import storyboard_edit as SE
    calls = {"repair": 0}
    state = {"errors": 0}
    saved = (SE.validate_timeline, SE.repair_all)

    def fake_validate(pdir, segs=None):
        return {"errors": [{"seg": i} for i in range(state["errors"])]}

    def fake_repair_all(pdir, dry_run=False):
        calls["repair"] += 1
        state["errors"] = 2          # repair fixes some, not all
        return {"total": 3}

    SE.validate_timeline, SE.repair_all = fake_validate, fake_repair_all
    try:
        pdir = tempfile.mkdtemp(prefix="ready_")

        state["errors"] = 0
        r = LAB._validate_own_timeline(pdir)
        check("a clean timeline reports zero errors", r["errors"] == 0)
        check("...and is marked as actually checked", r["checked"])
        check("...and no repair is attempted when nothing is wrong",
              calls["repair"] == 0 and r["repaired"] == 0)

        state["errors"] = 7
        r = LAB._validate_own_timeline(pdir)
        check("a broken timeline triggers the repair", calls["repair"] == 1)
        check("...reporting how many errors it started with",
              r["errors_before"] == 7)
        check("...how many faults it repaired", r["repaired"] == 3)
        check("...and how many SURVIVED, rather than claiming success",
              r["errors"] == 2)
        check("the lab and the Gemini ingest share ONE check-and-repair",
              "check_and_repair" in open(os.path.join(HERE, "ingest.py")).read())
    finally:
        SE.validate_timeline, SE.repair_all = saved

    # ---- the validator must never crash a finished build
    r = LAB._validate_own_timeline("/nonexistent/path/xyz")
    check("an unvalidatable project degrades instead of failing the build",
          isinstance(r, dict))

    # ---- ready means renderable
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    i = src.index('"ready": segs > 0')
    blk = src[i - 500:i + 200]
    check("ready requires a timeline with no errors",
          '(man.get("timeline") or {}).get("errors")' in blk)
    check("...and still requires segments to exist", "segs > 0 and" in blk)
    check("a build predating the check keeps the old meaning, rather than "
          "being wrongly marked broken", "in (None, 0)" in blk)
    check("the operator can see the error count, not just the verdict",
          '"timeline_errors"' in src)

    lab = open(os.path.join(HERE, "claude_lab.py"), encoding="utf-8").read()
    check("the build validates before it finishes",
          '_validate_own_timeline(pdir)' in lab)
    check("...and records the result in the manifest",
          'man["timeline"]' in lab)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
