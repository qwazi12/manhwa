"""Regression guard for Session-24's live outage: a Python string-escaping
bug (`\n` written as a real Python newline instead of the literal two
characters `\n`) inside storyboard.py's JS template corrupted the inline
<script> block's syntax. The page still rendered (200 OK, no server error)
and looked correct in the "view source" sense, but EVERY button on the
board silently did nothing — the whole script block failed to parse in the
browser, so delSeg/dupSeg/swapPanel/editNarr/checkboxes/drag-and-drop/
Approve never got defined. Nothing in the existing suite (which drives
storyboard_edit's Python functions directly) could have caught this,
because the bug lived entirely in the HTML/JS the browser receives.

This test renders the REAL page HTML for a real project (build_storyboard_
html — the exact function every request hits) and syntax-checks the actual
extracted <script> bodies with `node --check`, so a broken f-string escape
fails CI/local runs instead of shipping silently to production again.

Run: python3 test_storyboard_js_syntax.py
Requires: a Node.js binary on PATH (checked; skips with a clear note if
absent rather than false-passing).
"""
import re
import shutil
import subprocess
import sys

sys.path.insert(0, "..")
import matcher          # noqa: E402
import storyboard        # noqa: E402

RESULTS = []


def check(name, cond, note=""):
    RESULTS.append((name, bool(cond), note))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({note})" if note else ""))


def main():
    node = shutil.which("node")
    if not node:
        print("SKIP: no `node` on PATH — cannot syntax-check browser JS locally.")
        sys.exit(0)

    # Any real project under review_ui/projects/ exercises the template with
    # live data (groups, rejected/omitted rows, badges) — the same shapes
    # that triggered the original bug. Fall back to an empty render if none
    # exist yet (still catches most static-template breakage).
    import os
    proj_root = "projects"
    candidates = [d for d in os.listdir(proj_root)
                  if os.path.isdir(os.path.join(proj_root, d))
                  and not d.startswith("_")] if os.path.isdir(proj_root) else []

    tested_any = False
    for approved in (False, True):
        for pid in (candidates[:1] or [None]):
            if pid is None:
                continue
            pdir = os.path.join(proj_root, pid)
            html = storyboard.build_storyboard_html(
                pdir, matcher, review={}, usage_summary={}, approved=approved)
            blocks = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)
            assert blocks, f"no <script> blocks found in rendered page for {pid}"
            js = max(blocks, key=len)
            r = subprocess.run([node, "--check", "-"], input=js, text=True,
                                capture_output=True)
            check(f"{pid} (approved={approved}) inline <script> parses as valid JS",
                  r.returncode == 0, (r.stderr or "").strip().splitlines()[-1]
                  if r.returncode else "")
            tested_any = True

    if not tested_any:
        print("SKIP: no projects under review_ui/projects/ to render against.")
        sys.exit(0)

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
