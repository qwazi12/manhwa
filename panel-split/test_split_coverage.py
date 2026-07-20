"""S3: splitter coverage regression tests.

Guards the 2026-07-19 failure class: a panel detector that returns *some*
boxes while silently dropping most of a page's art. Fixtures are the three
worst pages of dungeon-odyssey ch2 (23-42% coverage under the old
YOLO-only logic; the user's missing panels were in the dropped bands).

Fixtures are the ORIGINAL page images, fetched once via the system scraper
into panel-split/test_fixtures/ (checked in — they're <2MB each and the
whole point is deterministic regression).

Run: python3 test_split_coverage.py    (PASS/FAIL table, exit 1 on fail)
"""
import os
import sys

import numpy as np
from PIL import Image

sys.argv = ["split_panels.py"]
import split_panels as sp

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fixtures")

# (file, min_panels, min_coverage) — thresholds deliberately below the
# current 99-100% results so style drift doesn't false-alarm, but far above
# the 23-42% failure mode this guards against.
CASES = [
    ("do2_page002.webp", 6, 0.90),
    ("do2_page007.webp", 4, 0.90),
    ("do2_page009.webp", 5, 0.90),
]

RESULTS = []


def check(name, cond, note=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({note})" if note else ""))


def independent_coverage(src, panels):
    """Coverage computed OUTSIDE the splitter (parallel verification)."""
    im = Image.open(src)
    gray = np.array(im.convert("L"))
    bg = sp._estimate_background_color(gray)
    content = (np.abs(gray.astype(np.int16) - bg) > sp.BG_COLOR_TOLERANCE).mean(axis=1) >= 0.04
    cov = np.zeros(im.height, bool)
    for p in panels:
        x0, y0, x1, y1 = p["bbox"]
        cov[y0:y1] = True
    denom = max(1, int(content.sum()))
    return float((content & cov).sum() / denom)


def main():
    missing = [f for f, _, _ in CASES if not os.path.exists(os.path.join(FIX, f))]
    if missing:
        sys.exit(f"fixtures missing: {missing} — run fetch_fixtures.py first")
    for fname, min_panels, min_cov in CASES:
        src = os.path.join(FIX, fname)
        img, panels, stats = sp.detect_panels(src)
        check(f"{fname}: panels >= {min_panels}", len(panels) >= min_panels,
              f"got {len(panels)}")
        check(f"{fname}: reported coverage >= {min_cov:.0%}",
              stats["coverage_final"] >= min_cov,
              f"got {stats['coverage_final']:.0%}")
        indep = independent_coverage(src, panels)
        check(f"{fname}: independent coverage >= {min_cov:.0%}",
              indep >= min_cov, f"got {indep:.0%}")
        check(f"{fname}: self-report honest (|Δ| <= 3%)",
              abs(indep - stats["coverage_final"]) <= 0.03,
              f"self {stats['coverage_final']:.0%} vs indep {indep:.0%}")
    n_ok = sum(RESULTS)
    print(f"\n{n_ok}/{len(RESULTS)} passed")
    sys.exit(0 if n_ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
