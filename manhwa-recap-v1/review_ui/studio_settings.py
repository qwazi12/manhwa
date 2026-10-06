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
    # Owner, 2026-10-05: "we need uniformity for titles, especially with many
    # chapters". The SEO writer only writes the {hook}; every title is built
    # from this format, so all chapters of a series read the same way.
    "title_template": "{hook} | {series} Ch.{chapter}",
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


def title_template():
    return load().get("title_template") or DEFAULTS["title_template"]


def build_title(template, hook, series, chapter, limit=100):
    """The full title from the format; the hook is shortened (at a word) to fit."""
    t = template or DEFAULTS["title_template"]
    fixed = t.replace("{series}", series or "").replace("{chapter}", str(chapter or "")).strip()
    hook = " ".join((hook or "").split())
    room = limit - len(fixed.replace("{hook}", ""))
    if len(hook) > room:
        cut = hook[:max(0, room - 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
        hook = cut + "…" if cut else ""
    out = fixed.replace("{hook}", hook)
    out = " ".join(out.split()).strip(" |-")
    return out[:limit]


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
        if patch.get("title_template") is not None:
            t = str(patch["title_template"]).strip()
            if "{hook}" not in t:
                raise ValueError("the title format must contain {hook}")
            bad = set(__import__("re").findall(r"\{(\w+)\}", t)) - {"hook", "series", "chapter"}
            if bad:
                raise ValueError("unknown part in the title format: " + ", ".join("{" + b + "}" for b in sorted(bad)))
            if len(t.replace("{hook}", "").replace("{series}", "").replace("{chapter}", "")) > 40:
                raise ValueError("the fixed text in the title format is too long")
            cur["title_template"] = t
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
