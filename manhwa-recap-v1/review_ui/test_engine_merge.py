"""One ingest entry point, engine chosen per run and recorded per project.

The Claude pipeline lived behind a separate TEST tab producing `-lab-*`
projects: two entry points, two naming schemes, and the engine encoded in a
FOLDER NAME. This merges the entry point and retires the name-as-metadata.

Deliberately a dispatch, not a stage-level merge: `claude_lab.run_lab` is
monolithic, and refactoring a paid path to interleave its stages is a much
larger change than the operator-facing win requires.

Gemini stays the default. A newer path earns default status with data, not by
being newer.
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
    import ingest

    check("both engines are declared", set(ingest.ENGINES) == {"gemini", "claude"})

    import inspect
    sig = inspect.signature(ingest.run_ingest)
    check("run_ingest takes an engine", "engine" in sig.parameters)
    check("...defaulting to GEMINI, not the newer path",
          sig.parameters["engine"].default == "gemini")

    # an unknown engine must be refused, not silently treated as gemini
    try:
        ingest.run_ingest("https://example.com/x", lambda *a: None, engine="bogus")
        check("an unknown engine is refused", False)
    except ValueError as e:
        check("an unknown engine is refused", True)
        check("...naming the valid options", "gemini" in str(e))
    except Exception:
        check("an unknown engine is refused", False)

    src = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    check("the claude path is reached through the same entry point",
          "_run_claude_engine(url, progress" in src)
    check("gemini projects record their engine", '"engine": "gemini",' in src)
    check("claude projects record theirs too", 'meta["engine"] = "claude"' in src)
    check("run_lab's single-string progress is adapted, not reshaped upstream",
          "def adapt(line)" in src)

    # backfill: engine must come from a FIELD, with the folder name only as
    # the legacy fallback for projects that predate the field.
    check("legacy -lab- projects are backfilled as claude",
          '"claude" if "-lab-" in pid else "gemini"' in src)
    check("...without renaming their folders (paths are referenced elsewhere)",
          "folder is NOT renamed" in src)

    srv = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    check("the ingest route accepts an engine", 'engine: str = "gemini"' in srv)
    check("...validates it against the allowed list",
          "engine must be one of" in srv)
    check("...and refuses claude with no key rather than failing mid-run",
          "no Claude API key on this server" in srv)
    check("the engine reaches the worker", "engine=engine)" in srv)

    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("the board offers the choice", 'id="ingengine"' in sb)
    check("...with gemini pre-selected", '"gemini" selected' in sb)
    check("...and sends it with the request", "url, fresh, engine" in sb)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
