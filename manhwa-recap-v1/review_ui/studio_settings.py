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
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=1)
        os.replace(tmp, _path())
    return before, cur
