"""Renderer-epoch staleness tests (Session 23).

Why this exists: the render step only ever rebuilt clips whose FILE was
missing, so shipping a renderer change (new framing/motion) silently reused
old-looking clips — the export never showed the fix. Clips are now stamped
with render_segments.RENDER_EPOCH and a mismatch counts as "needs render".

Run: python3 test_render_epoch.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "hyperframes"))
import render_segments as rs
import server as srv


def main():
    def _ck(name, ok):
        results.append((name, bool(ok)))

    pdir = tempfile.mkdtemp(prefix="epochtest_")
    os.makedirs(os.path.join(pdir, "clips"))
    segs = [{"seg_index": i, "clip": f"clips/seg_{i:03d}.mp4"} for i in range(3)]
    for s in segs:
        open(os.path.join(pdir, s["clip"]), "wb").write(b"x")
    srv.active_project_dir = lambda: pdir

    results = [
        ("unstamped existing clips need render",
         srv.needs_render(segs, pdir) == [0, 1, 2]),
    ]
    for s in segs:
        srv._stamp_epoch(s["seg_index"])
    results.append(("stamped at current epoch -> none need render",
                    srv.needs_render(segs, pdir) == []))
    rs.RENDER_EPOCH += 1
    results.append(("renderer bump invalidates every clip",
                    srv.needs_render(segs, pdir) == [0, 1, 2]))
    rs.RENDER_EPOCH -= 1
    os.remove(os.path.join(pdir, segs[1]["clip"]))
    results.append(("missing clip still detected",
                    srv.needs_render(segs, pdir) == [1]))

    # ---- the renderer binary must be the PINNED one, not resolved at runtime
    # `npx --yes hyperframes` cost ~0.88s/clip over the installed binary
    # (measured) and resolved the package fresh on every render, so a release
    # could change output with no commit here.
    import render_segments as _rs
    cmd = _rs._hyperframes_cmd()
    _ck("a renderer command resolves", bool(cmd))
    here = os.path.dirname(os.path.abspath(_rs.__file__))
    installed = os.path.join(here, "node_modules", ".bin", "hyperframes")
    if os.path.exists(installed):
        _ck("the pinned local binary is preferred over npx", cmd == [installed])
        _ck("...so renders do not shell out to npx", cmd[0] != "npx")
    else:
        _ck("npx remains the fallback when nothing is installed",
              cmd[:1] == ["npx"])
    _ck("the version is pinned exactly (no range) in package.json",
          '"hyperframes": "' in open(os.path.join(here, "package.json")).read()
          and not any(c in open(os.path.join(here, "package.json")).read()
                          .split('"hyperframes": "')[1].split('"')[0]
                      for c in "^~*x"))
    _ck("a lockfile is committed so the image installs exactly that",
          os.path.exists(os.path.join(here, "package-lock.json")))

    # ---- thumbnails must not be built inside /api/project
    # Cold cache was 103 ffmpeg spawns (~4.1s measured) serialized in one GET,
    # and the cache key includes the crop so any re-crop re-paid it.
    import inspect as _insp, server as _srv
    src = _insp.getsource(_srv.project)
    # Match the CALL, not the word: the handler carries a comment explaining
    # why the call was removed, and that comment is not a call.
    import re as _re
    _ck("/api/project no longer generates thumbnails in the request path",
          not _re.search(r"^\s*ensure_thumb\(", src, _re.M))
    _ck("...but /thumb still builds them on demand",
          "ensure_thumb" in _insp.getsource(_srv.thumb))

    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in results if ok)
    print(f"\n{n}/{len(results)} passed")
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
