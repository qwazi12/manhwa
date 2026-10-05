"""Studio-wide settings the owner changes from ⚙️ Settings & Channels.

One small JSON file on the volume (`projects/_studio_settings.json`). Defaults
are the owner's decisions of 2026-10-04: export at 1.25×; publish to
🦩 Flamingo Remix (Upload-Post profile "mk", YouTube). Privacy stays PRIVATE
until the owner saves "public" here: the studio's standing guarantee is that
nothing goes public by omission (CLAUDE.md: rights gating before public).
Environment variables still win where they exist (EXPORT_SPEED), so a Railway
setting is never silently overridden.
"""

import json
import os
import threading

NAME = "_studio_settings.json"
DEFAULTS = {
    "export_speed": 1.25,
    "publish": {"targets": ["mk:youtube"], "privacy": "private"},
    # Step 5: built but OFF until the owner switches it on (2026-10-04).
    "schedule": {"enabled": False, "times": ["12:00", "18:00"], "tz": "America/New_York",
                 "per_channel_per_day": 1},
}
PRIVACY = ("private", "unlisted", "public")
_lock = threading.Lock()


def _path():
    import ingest
    return os.path.join(ingest.PROJECTS, NAME)


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def export_speed():
    env = os.environ.get("EXPORT_SPEED")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return float(load().get("export_speed") or 1.0)


def publish_defaults():
    return load()["publish"]


def update(patch):
    """Validate and save. Returns (before, after)."""
    with _lock:
        before = load()
        cur = json.loads(json.dumps(before))
        if patch.get("export_speed") is not None:
            v = float(patch["export_speed"])
            if not 1.0 <= v <= 2.0:
                raise ValueError("export speed must be between 1.0 and 2.0")
            cur["export_speed"] = round(v, 2)
        pub = patch.get("publish") or {}
        if pub.get("privacy") is not None:
            if pub["privacy"] not in PRIVACY:
                raise ValueError("privacy must be private, unlisted or public")
            cur["publish"]["privacy"] = pub["privacy"]
        if pub.get("targets") is not None:
            if not isinstance(pub["targets"], list) or not all(isinstance(t, str) for t in pub["targets"]):
                raise ValueError("targets must be a list of account ids")
            cur["publish"]["targets"] = pub["targets"]
        sch = patch.get("schedule") or {}
        if sch:
            import re
            if "enabled" in sch:
                was = bool(cur["schedule"].get("enabled"))
                cur["schedule"]["enabled"] = bool(sch["enabled"])
                if cur["schedule"]["enabled"] and not was:
                    import time as _t
                    cur["schedule"]["enabled_at"] = _t.time()
            if sch.get("times") is not None:
                times = sch["times"]
                if (not isinstance(times, list) or not 1 <= len(times) <= 6
                        or not all(isinstance(t, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) for t in times)):
                    raise ValueError("posting times must be 1-6 times like 12:00 (24-hour)")
                cur["schedule"]["times"] = sorted(set(times))
            if sch.get("per_channel_per_day") is not None:
                n = int(sch["per_channel_per_day"])
                if not 1 <= n <= 5:
                    raise ValueError("posts per channel per day must be 1-5")
                cur["schedule"]["per_channel_per_day"] = n
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=1)
        os.replace(tmp, _path())
    return before, cur
