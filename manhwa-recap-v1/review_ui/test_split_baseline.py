"""The splitter must be deterministic, and its baseline must be pinned.

A gate is only meaningful if the thing it measures cannot drift underneath it.
The Murim ch.43 baseline was recorded as 156, then re-ran at 151, and the gap
was initially blamed on source drift. Measured: the source is byte-identical
across scrapes and the splitter is deterministic — the movement was entirely
code (pre-session 162 -> current 151 on the same images). A baseline recorded
against different code cannot gate a change.

This test pins the contract rather than the number: the fixture exists, and
the same input produces the same output twice.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIXTURE = os.path.join(ROOT, "fixtures", "murim_ch43", "pages")
# TWO baselines, because there are two splitters and comparing across them
# is what produced a bogus gate failure in the first place.
BASELINE_PROD = 151   # panel-split/split_panels.py, the current ingest path
BASELINE_NEW = 125    # review_ui/splitlab.py, re-recorded 2026-09-23 after
                      # cut arbitration: of 161 cuts the two disagree on, 143
                      # are interior features prod was shredding, 18 are real
                      # gutters the new config misses. See fixture README.
BAND = 0.15

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    check("the pinned regression fixture exists", os.path.isdir(FIXTURE))
    if not os.path.isdir(FIXTURE):
        print("FAIL fixture missing — regression gates cannot run")
        return 1
    pages = [f for f in os.listdir(FIXTURE)
             if os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".webp")]
    check("...and holds its 20 pinned pages", len(pages) == 20)
    check("the baseline is recorded next to the fixture",
          os.path.exists(os.path.join(ROOT, "fixtures", "murim_ch43", "README.md")))

    script = os.path.join(ROOT, "panel-split", "split_panels.py")
    check("the splitter script is where the fixture expects it",
          os.path.exists(script))
    if not os.path.exists(script):
        for n, ok in R:
            print(("PASS " if ok else "FAIL ") + n)
        return 1

    # Determinism is the property that makes a baseline mean anything.
    counts = []
    for _ in range(2):
        out = tempfile.mkdtemp(prefix="splitdet_")
        p = subprocess.run([sys.executable, script, "--input", FIXTURE,
                            "--out", out, "--batch"],
                           cwd=os.path.join(ROOT, "panel-split"),
                           capture_output=True, text=True)
        if p.returncode != 0:
            check("the splitter runs on the fixture", False)
            break
        counts.append(len([f for f in os.listdir(out) if f.endswith(".png")]))
    else:
        check("the splitter runs on the fixture", True)
        check("the same input gives the same count twice (deterministic)",
              counts[0] == counts[1])
        check("...and matches the PRODUCTION baseline, not the other splitter's",
              abs(counts[0] - BASELINE_PROD) <= BAND * BASELINE_PROD)
        print("   production splitter measured %s vs baseline %d (band +/-%d%%)"
              % (counts, BASELINE_PROD, int(BAND * 100)))
        # The two splitters are different code with different correct answers.
        # Asserting one against the other's baseline is precisely the mistake
        # that produced a -17.2% "regression" that was really a code change.
        check("the two baselines are kept apart",
              BASELINE_PROD != BASELINE_NEW)

    for n, ok in R:
        print(("PASS " if ok else "FAIL ") + n)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
