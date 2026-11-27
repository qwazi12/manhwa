#!/usr/bin/env python3
"""YouTube A/B-test runner helper (launchd com.flamingo.abtest).

Owner rule (2026-11-15, restated 2026-11-26 "It's your job"): every new posted
Flamingo Recap video gets a YouTube Studio "Title and thumbnail" test.
YouTube has no API for tests, so a headless Claude + Chrome does the Studio
clicks; this helper does everything else so that session never sees a secret.

  abtest.py check            exit 3 if a public video needs a test, else 0
                             (writes status.json; macOS alert if one is stuck > 3 h)
  abtest.py due              JSON of due videos + downloaded thumbnail paths
  abtest.py mark <video_id> [running|failed|not_available|skipped] [note]
  abtest.py session <exit>   record the last Claude session result in status.json

Secret: SHARED_SECRET read from `railway variables --json` (cwd ~/dev/manhwa),
kept in memory only, never printed or written.
Holds: video ids in holds.txt (one per line, # comments) are never tested.
"""
import json, os, ssl, subprocess, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://recap-studio-production.up.railway.app"
REPO = os.path.expanduser("~/dev/manhwa")
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
STATUS = os.path.join(HERE, "status.json")
HOLDS = os.path.join(HERE, "holds.txt")
STUCK_AFTER_S = 3 * 3600
DAYS = 60
_secret = None
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _CTX = ssl.create_default_context()


def _now():
    return datetime.now(timezone.utc)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def secret():
    global _secret
    if _secret is None:
        out = subprocess.run(["railway", "variables", "--json"], cwd=REPO,
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            sys.exit("abtest: `railway variables` failed: " + (out.stderr or out.stdout).strip()[-200:].replace(chr(10), " "))
        _secret = json.loads(out.stdout).get("SHARED_SECRET") or ""
        if not _secret:
            sys.exit("abtest: SHARED_SECRET missing from Railway variables")
    return _secret


def api(path, body=None, raw=False, tries=3):
    for i in range(tries):
        req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"x-shared-secret": secret(), "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60, context=_CTX) as r:
                data = r.read()
                return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
        time.sleep(3 * 2 ** i)


def holds():
    try:
        return {l.split("#")[0].strip() for l in open(HOLDS) if l.split("#")[0].strip()}
    except FileNotFoundError:
        return set()


def is_public(vid):
    u = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
    try:
        with urllib.request.urlopen(u, timeout=20, context=_CTX) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False         # network trouble: not due this round, next check retries


def due_items():
    q = api(f"/api/abtest/queue?days={DAYS}")
    held = holds()
    return q, [x for x in q.get("todo", []) if x["video_id"] not in held and is_public(x["video_id"])]


def notify(msg):
    msg = msg.replace('"', "'")[:180]
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "FLAMINGO A/B tests" sound name "Glass"'])


def _status():
    try:
        return json.load(open(STATUS))
    except Exception:
        return {}


def _save(st):
    tmp = STATUS + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATUS)


def cmd_check():
    q, due = due_items()
    now = _now()
    stuck = []
    for x in due:
        try:
            pub = datetime.strptime(x.get("publish_at_utc", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if (now - pub).total_seconds() > STUCK_AFTER_S:
                stuck.append(x["video_id"])
        except ValueError:
            pass
    st = _status()
    prev_stuck = set(st.get("stuck_over_3h") or [])
    st.update({"last_check_utc": _iso(now), "todo_total": len(q.get("todo", [])), "have": len(q.get("have", [])),
               "due_now": [x["video_id"] for x in due], "held": sorted(holds()), "stuck_over_3h": stuck})
    _save(st)
    if stuck and set(stuck) - prev_stuck:
        notify(f"{len(stuck)} public video(s) over 3 h without an A/B test: {', '.join(stuck)}")
    print(f"{_iso(now)} check: due={st['due_now']} stuck={stuck} todo={st['todo_total']} have={st['have']}")
    sys.exit(3 if due else 0)


def cmd_due():
    _, due = due_items()
    os.makedirs(WORK, exist_ok=True)
    res = []
    for x in due:
        p = x.get("plan") or {}
        thumbs = {}
        for v, u in sorted((p.get("thumbnail_urls") or {}).items()):
            f = os.path.join(WORK, f"{x['video_id']}_{v}.jpg")
            with open(f, "wb") as fh:
                fh.write(api(u, raw=True))
            thumbs[v] = f
        res.append({"video_id": x["video_id"], "edit_url": f"https://studio.youtube.com/video/{x['video_id']}/edit",
                    "current_title": x.get("title"), "publish_at_utc": x.get("publish_at_utc"),
                    "test_type": p.get("type"), "titles": p.get("titles"), "thumbnails": thumbs})
    print(json.dumps(res, indent=1))


def cmd_mark(vid, status="running", note=""):
    if status not in ("running", "failed", "not_available", "skipped"):
        sys.exit("abtest: status must be running|failed|not_available|skipped")
    q = api(f"/api/abtest/queue?days={DAYS}")
    x = next((t for t in q.get("todo", []) if t["video_id"] == vid), None)
    if x is None:
        sys.exit(f"abtest: {vid} is not in the todo queue (already marked?)")
    body = {"video_id": vid, "status": status, "by": "launchd-abtest", "project": x.get("project", ""),
            "name": x.get("name", ""), "test_type": (x.get("plan") or {}).get("type") or "both",
            "variants": ["A", "B", "C"],
            "note": (note or "Title and thumbnail, 3 pairs; Studio showed Running after Save")[:500]}
    r = api("/api/abtest/mark", body)
    print(json.dumps({"ok": r.get("ok"), "video_id": vid, "status": status}))


def cmd_session(code):
    st = _status()
    st.update({"last_session_utc": _iso(_now()), "last_session_exit": int(code)})
    _save(st)
    if int(code) != 0:
        notify(f"A/B test session ended with exit {code}; see ~/dev/manhwa-ops/abtest/sessions.log")


if __name__ == "__main__":
    a = sys.argv[1:] or ["check"]
    cmds = {"check": cmd_check, "due": cmd_due, "mark": lambda: cmd_mark(*a[1:4]),
            "session": lambda: cmd_session(a[1])}
    if a[0] not in cmds:
        sys.exit(__doc__)
    cmds[a[0]]()
