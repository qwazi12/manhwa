"""Give back file cache the container is billed for (owner, 2026-10-20).

Railway bills the container's cgroup memory, and that includes the Linux
file cache. A render reads and writes gigabytes of panels, clips and MP4s;
with a 32 GB limit the kernel never needs to drop that cache, so after a
render the bill stayed at 2-4 GB for hours with nothing running.

Measured on production 2026-10-20: reading 2.09 GB of exports raised the
billed memory 0.18 -> 2.28 GB and it stayed there; posix_fadvise(DONTNEED)
on the same files brought it back to 0.18 GB within a minute.

While the studio is idle, this asks the kernel to drop its cached pages for
the files under the data folders. Files are not touched; a later read simply
comes from disk again. Linux only (posix_fadvise); a no-op elsewhere.
CACHE_RELEASE=off disables it; CACHE_RELEASE_SECONDS sets the interval.
"""
import os
import threading
import time

CGROUP = "/sys/fs/cgroup"
MIN_FILE_CACHE = 256 * 1024 * 1024      # below this, not worth a pass


def supported():
    return hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED")


def cgroup_memory():
    """{'current', 'anon', 'file'} in bytes, or {} when not in a cgroup v2."""
    try:
        with open(os.path.join(CGROUP, "memory.stat")) as f:
            st = dict(line.split() for line in f if line.strip())
        with open(os.path.join(CGROUP, "memory.current")) as f:
            cur = int(f.read())
        return {"current": cur, "anon": int(st.get("anon", 0)), "file": int(st.get("file", 0))}
    except (OSError, ValueError):
        return {}


def _roots(paths):
    """Existing, de-duplicated real directories (a symlinked folder inside
    another root is walked once)."""
    real = sorted({os.path.realpath(p) for p in paths if p and os.path.isdir(p)})
    out = []
    for r in real:
        if not any(r == o or r.startswith(o + os.sep) for o in out):
            out.append(r)
    return out


def release(paths):
    """Drop cached pages for every regular file under `paths`.
    Returns (files_advised, errors)."""
    if not supported():
        return 0, 0
    n = errs = 0
    for root in _roots(paths):
        for d, _dirs, files in os.walk(root):
            for name in files:
                p = os.path.join(d, name)
                try:
                    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                except OSError:
                    errs += 1
                    continue
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                    n += 1
                except OSError:
                    errs += 1
                finally:
                    os.close(fd)
    return n, errs


def run_once(paths, is_busy, log=print, min_file_cache=MIN_FILE_CACHE):
    """One idle check. Returns a summary dict (also logged when it acted)."""
    before = cgroup_memory()
    if not before or before["file"] < min_file_cache:
        return {"acted": False, "reason": "little file cache", "before": before}
    try:
        if is_busy():
            return {"acted": False, "reason": "work running", "before": before}
    except Exception as exc:              # unsure -> do nothing this round
        return {"acted": False, "reason": f"busy check failed: {exc}", "before": before}
    t0 = time.time()
    files, errs = release(paths)
    after = cgroup_memory()
    out = {"acted": True, "files": files, "errors": errs, "seconds": round(time.time() - t0, 2),
           "before": before, "after": after}
    gb = lambda b: f"{b / 1e9:.2f}GB"
    log(f"[cache-release] idle: file cache {gb(before['file'])} -> {gb(after.get('file', 0))}, "
        f"billed memory {gb(before['current'])} -> {gb(after.get('current', 0))} "
        f"({files} files, {errs} skipped, {out['seconds']}s)", flush=True)
    return out


def start(paths, is_busy, interval=None):
    """Background loop (daemon). Returns the thread, or None when disabled."""
    if os.environ.get("CACHE_RELEASE", "on").lower() in ("0", "off", "false") or not supported():
        return None
    every = interval or float(os.environ.get("CACHE_RELEASE_SECONDS", 300))

    def loop():
        while True:
            time.sleep(every)
            try:
                run_once(paths, is_busy)
            except Exception as exc:          # never take the server down
                print(f"[cache-release] skipped: {exc}", flush=True)

    t = threading.Thread(target=loop, name="cache-release", daemon=True)
    t.start()
    return t
