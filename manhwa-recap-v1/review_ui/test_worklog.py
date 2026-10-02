"""Work log + evidence (2026-09-30): the owner sees every change in the tool.

Pins: entries come from memory.md newest-first with dates, commits and
evidence links; evidence is stored/served only under allowlisted names and
types (no traversal, no executables), with a size cap; the board shows a
"built with" line from project.json; the drawer exists in the UI.

Run: python3 test_worklog.py
"""
import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..")]
import worklog as WL

R = []


def check(name, ok):
    R.append((name, bool(ok)))


SAMPLE = """# MEMORY
intro text
### Session 1 — 2026-07-03 — Initial Setup
- first thing, commit abc1234
### 2026-09-30 — cutter refinement shipped
- did the cutter, commit d515822 and 9dea88c
**Evidence:** cutter-2026-09-30, ch44-listening
### 2026-09-30 — something NOT deployed yet
- branch only
"""


class _Req:
    def __init__(self, body):
        self._b = body

    async def body(self):
        return self._b


def main():
    es = WL.parse(SAMPLE)
    check("entries come newest first", es[0]["title"].startswith("2026-09-30 — something"))
    check("each entry carries its date", es[-1]["date"] == "2026-07-03")
    check("commits are picked out of the text", es[1]["commits"] == ["9dea88c", "d515822"])
    check("evidence links are parsed from the **Evidence:** line",
          es[1]["evidence"] == ["cutter-2026-09-30", "ch44-listening"])
    check("the real memory.md parses into entries", len(WL.entries(500)) > 100)

    for slug, name, ok in (("cutter-2026-09-30", "ch43_cuts.png", True),
                           ("cutter-2026-09-30", "clip.mp3", True),
                           ("../etc", "passwd.txt", False),
                           ("cutter", "../../server.py", False),
                           ("cutter-x", "evil.py", False),
                           ("cutter-x", "run.sh", False),
                           ("Cutter", "a.png", False)):
        check(f"evidence path {'allowed' if ok else 'refused'}: {slug}/{name}",
              (WL.evidence_path(slug, name) is not None) == ok)

    import server as srv
    tmp = tempfile.mkdtemp(prefix="evid_")
    saved = WL.EVIDENCE
    WL.EVIDENCE = tmp
    try:
        r = asyncio.run(srv.api_evidence_put("cutter-test", "sheet.png", _Req(b"\x89PNG fake")))
        check("an evidence file can be stored through the API", r["ok"] and r["bytes"] == 9)
        check("...and is listed for its slug", WL.evidence_files("cutter-test") == ["sheet.png"])
        resp = srv.api_evidence_get("cutter-test", "sheet.png")
        check("...and served with the right type", resp.media_type == "image/png")
        for bad in (("cutter-test", "x.py", b"a"), ("cutter-test", "big.png", b"")):
            try:
                asyncio.run(srv.api_evidence_put(bad[0], bad[1], _Req(bad[2]))); refused = False
            except srv.HTTPException:
                refused = True
            check(f"refused: {bad[1]} ({'bad type' if bad[1].endswith('.py') else 'empty'})", refused)
        try:
            srv.api_evidence_get("cutter-test", "missing.png"); nf = False
        except srv.HTTPException as e:
            nf = e.status_code == 404
        check("a missing file is a 404, not an error", nf)
    finally:
        WL.EVIDENCE = saved

    import storyboard as sb
    line = sb._built_with({"engine": "gemini", "variant": "v3",
                           "split_coverage": {"refine": {"cuts": 8, "text_slivers_merged": 25}},
                           "direct_speech": {"enabled": True, "lines_quoted": 2, "quotes_unapproved": 0},
                           "timeline": {"checked": True, "errors": 0}})
    check("the board's 'built with' line states the recorded facts",
          "version <b>v3</b>" in line and "<b>8</b> missed splits cut" in line
          and "direct speech <b>on</b>" in line)
    check("an old project says it predates the refinement pass",
          "before the refinement pass" in sb._built_with({"split_coverage": {"panels": 3}}))
    src = open(os.path.join(HERE, "storyboard.py")).read()
    check("the work log lives in the Logs & Activity page and loads the log",
          'id="lt_work"' in src and 'id="worklist"' in src
          and "if (which === 'work') loadWork();" in src and "/api/worklog" in src)
    check("old links to the Work page land on its tab",
          "if (name === 'work') name = 'logs';" in src)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print(f"\n{n}/{len(R)} passed")
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
