"""The background-registration splitter is now the production ingest path.

Evidence for the swap is the cut arbitration on the pinned Murim ch.43
fixture: of 161 cuts the two splitters disagree on, 143 are interior features
the legacy splitter was shredding and 18 are real gutters this one misses.
Better on net, not strictly better — the 18 are a recorded cost, not a hidden
one.

Two properties matter more than the panel count:
  1. FILENAME CONVENTION. describe/match/the board derive a panel_id from the
     crop filename, so `pageNNN_panel_NNN.png` is a contract. Break it and the
     pipeline silently mis-associates panels.
  2. A FALLBACK. A splitter failure must not abort an ingest that has already
     paid to scrape.
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIXTURE = os.path.join(ROOT, "fixtures", "murim_ch43", "pages")

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import splitlab as SL

    src = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    check("ingest calls the new splitter", "splitlab.split_into(" in src)
    check("...as the DEFAULT, with legacy as the opt-out",
          'os.environ.get("SPLIT_ENGINE", "new")' in src)
    check("...and falls back rather than aborting a paid scrape",
          "using legacy" in src and "use_legacy = True" in src)
    check("coverage reporting does not go dark on the new path",
          '"engine": "background-registration"' in src)

    if not os.path.isdir(FIXTURE):
        check("fixture present", False)
    else:
        pages = [os.path.join(FIXTURE, f) for f in sorted(os.listdir(FIXTURE))]
        out = tempfile.mkdtemp(prefix="prodsplit_")
        n, st = SL.split_into(pages, out, slug="murim-psychopath_43")
        names = sorted(f for f in os.listdir(out) if f.endswith(".png"))

        check("it produces panels", n > 0)
        check("EVERY crop matches production's filename contract",
              all(re.match(r"^page\d{3}_panel_\d{3}\.png$", f) for f in names))
        check("...numbered per page, so panel_id stays unique",
              len(set(names)) == len(names))
        check("page-format input is detected as pages", st["format"] == "page")
        check("stats carry per-page detail for the record",
              len(st.get("per_page") or []) == len(pages))
        check("count lands within the band of the recorded 125 baseline",
              abs(n - 125) <= 0.15 * 125)
        print("   %d crops from %d pages (baseline 125, band +/-15%%)"
              % (n, len(pages)))

        # A strip source must take the scroll path, not the page path.
        check("strip detection still keys on tile uniformity, not site name",
              SL.is_strip.__doc__ and "UNIFORMITY" in SL.is_strip.__doc__)

    # The regression that broke a live ingest: the new path sets a different
    # split_coverage shape, and the reporting line read a legacy-only key
    # unconditionally — so a split that cut every page correctly still failed
    # the whole run with KeyError: 'pages_below_85'.
    check("coverage reporting guards the legacy-only key",
          '"pages_below_85" in split_coverage' in src)
    check("...and has a branch for the new splitter's shape",
          "elif split_coverage:" in src)
    check("an empty crop set is caught explicitly",
          "the splitter produced no panel crops" in src)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
