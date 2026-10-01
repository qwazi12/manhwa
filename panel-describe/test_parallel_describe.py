"""Describe runs several panels at once, keeps panel order, retries transient
failures, and keeps per-thread token counts apart. No network: every Gemini
call is mocked."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import describe  # noqa: E402

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    from PIL import Image
    d = tempfile.mkdtemp(prefix="pardesc_")
    crops = os.path.join(d, "crops")
    os.makedirs(crops)
    names = [f"page001_panel_{i:03d}.png" for i in range(1, 25)]
    for n in names:
        Image.new("RGB", (300, 400), "white").save(os.path.join(crops, n))

    # ---- retry: a timeout then success -> ok; a non-transient error -> no retry
    calls = {"n": 0}

    def flaky(path, key, model, prompt=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("The read operation timed out")
        return "ocr", "desc", []
    orig = describe.describe_with_gemini
    sleep = describe.time.sleep
    describe.time.sleep = lambda s: None
    describe.describe_with_gemini = flaky
    rec = describe.describe_panel(os.path.join(crops, names[0]), "AQ.x", "m")
    check("a timed-out panel is retried and then described", rec["ok"] and calls["n"] == 2)

    calls["n"] = 0

    def bad(path, key, model, prompt=None):
        calls["n"] += 1
        raise ValueError("bad json")
    describe.describe_with_gemini = bad
    rec = describe.describe_panel(os.path.join(crops, names[0]), "AQ.x", "m")
    check("a non-transient error is not retried", not rec["ok"] and calls["n"] == 1)
    describe.describe_with_gemini = orig
    describe.time.sleep = sleep

    # ---- per-thread token stash
    seen = {}

    def worker(i):
        describe._stash_usage({"usageMetadata": {"promptTokenCount": i}})
        time.sleep(0.01)
        seen[i] = describe.LAST_USAGE.get("prompt")
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(1, 9)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("token counts stay with their own thread", all(seen[i] == i for i in seen))

    # ---- the runner: parallel, ordered (mocked describe via sitecustomize)
    shim = os.path.join(d, "shim")
    os.makedirs(shim)
    with open(os.path.join(shim, "sitecustomize.py"), "w") as f:
        f.write('''
import threading, time, os, json
import describe as _d
_lock = threading.Lock(); _cur = [0]; _peak = [0]
def _fake(path, key, model, series_bible=None):
    with _lock:
        _cur[0] += 1; _peak[0] = max(_peak[0], _cur[0])
    time.sleep(0.2)
    with _lock:
        _cur[0] -= 1
    pid = os.path.splitext(os.path.basename(path))[0]
    open(os.environ["PEAK_FILE"], "w").write(str(_peak[0]))
    return {"panel_id": pid, "file": os.path.basename(path), "width": 300,
            "height": 400, "bbox": [0,0,300,400], "ocr_text": pid,
            "visual_description": "d", "lines": [], "source": "gemini", "ok": True}
_d.describe_panel = _fake
''')
    out = os.path.join(d, "descriptions.json")
    peak = os.path.join(d, "peak")
    env = dict(os.environ, PYTHONPATH=shim + os.pathsep + HERE, GEMINI_API_KEY="AQ.test",
               DESCRIBE_WORKERS="6", PEAK_FILE=peak)
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "--input", crops,
                        "--out", out], env=env, capture_output=True, text=True, cwd=HERE)
    el = time.time() - t0
    if p.returncode != 0:
        print(p.stdout[-800:], p.stderr[-800:])
    recs = json.load(open(out)) if os.path.exists(out) else []
    check("the runner describes every panel", len(recs) == len(names))
    check("output stays in panel order", [r["panel_id"] for r in recs]
          == [os.path.splitext(n)[0] for n in names])
    check("panels are described concurrently (up to DESCRIBE_WORKERS)",
          os.path.exists(peak) and 2 <= int(open(peak).read()) <= 6)
    check("24 panels x 0.2s finish well under the serial 4.8s", el < 3.5)
    print("   runner wall time %.2fs, peak concurrency %s" % (el, open(peak).read() if os.path.exists(peak) else "?"))

    # ---- a usage cap mid-run still halts the whole run with the ingest's marker
    if describe.usage:
        with open(os.path.join(shim, "sitecustomize.py"), "a") as f:
            f.write('''
_n = [0]
_prev = _d.describe_panel
def _capped(path, key, model, series_bible=None):
    with _lock:
        _n[0] += 1; k = _n[0]
    if k == 5:
        raise _d.usage.UsageCapExceeded("MAX_DAILY_SPEND_USD would be exceeded")
    return _prev(path, key, model, series_bible)
_d.describe_panel = _capped
''')
        out2 = os.path.join(d, "capped.json")
        p = subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "--input", crops,
                            "--out", out2], env=env, capture_output=True, text=True, cwd=HERE)
        check("a usage cap stops the run with USAGE CAP EXCEEDED",
              p.returncode != 0 and "USAGE CAP EXCEEDED" in (p.stdout + p.stderr))

    for n, ok in R:
        print(("PASS " if ok else "FAIL ") + n)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
