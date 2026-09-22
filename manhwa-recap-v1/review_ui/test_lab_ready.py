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

    # ---- the validator reports honestly on a clean and a broken timeline
    calls = {"repair": 0}

    class FakeSE:
        state = {"errors": 0}

        @staticmethod
        def validate_timeline(pdir):
            return {"errors": [{"seg": i} for i in range(FakeSE.state["errors"])]}

        @staticmethod
        def repair_shared_beats(pdir, dry_run=False):
            calls["repair"] += 1
            FakeSE.state["errors"] = 2          # repair fixes some, not all
            return [{"seg": 1}, {"seg": 2}, {"seg": 3}]

    real = sys.modules.get("storyboard_edit")
    sys.modules["storyboard_edit"] = FakeSE
    try:
        pdir = tempfile.mkdtemp(prefix="ready_")

        FakeSE.state["errors"] = 0
        r = LAB._validate_own_timeline(pdir)
        check("a clean timeline reports zero errors", r["errors"] == 0)
        check("...and is marked as actually checked", r["checked"])
        check("...and no repair is attempted when nothing is wrong",
              calls["repair"] == 0 and r["repaired"] == 0)

        FakeSE.state["errors"] = 7
        r = LAB._validate_own_timeline(pdir)
        check("a broken timeline triggers the repair", calls["repair"] == 1)
        check("...reporting how many errors it started with",
              r["errors_before"] == 7)
        check("...how many segments it repaired", r["repaired"] == 3)
        check("...and how many SURVIVED, rather than claiming success",
              r["errors"] == 2)
    finally:
        if real is not None:
            sys.modules["storyboard_edit"] = real
        else:
            sys.modules.pop("storyboard_edit", None)

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
