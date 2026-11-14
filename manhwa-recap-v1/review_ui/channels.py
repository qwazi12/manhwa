"""Channel registry: which series go to which channel, and what each channel
looks and sounds like (2026-11-14).

Before this the studio knew one channel, as a default in the settings; a
second channel meant editing each series by hand. The registry is one file,
<projects>/_channels.json:

  {"version": 1, "default_channel": "flamingo", "channels": [
     {"id": "flamingo", "name": "Flamingo Remix",
      "targets": ["mk:youtube"],                  # Upload-Post "<profile>:<network>"
      "api_key_env": "UPLOADPOST_API_KEY",        # NAME of the variable — never a key
      "genres": ["murim", "fantasy"],             # genre focus (used when a series is unassigned)
      "series": ["the-stellar-swordmaster-5988"], # explicit assignments
      "voice": {"provider": "gemini", "voice": "Charon", "style": ""},
      "branding": {"thumbnail_style": "D", "badge_colour": "#FF5F1F", "title_suffix": "| Flamingo Recap"},
      "packaging": {"privacy": "public", "category_id": "24", "description_footer": ""},
      "schedule": {"timezone": "America/New_York", "slots": ["10:00", "14:00", "22:00"], "daily_max": 4}}]}

No file -> one implicit channel made from the studio's current default
targets, so nothing changes until a registry is saved.

What uses it today: default upload targets for a series (server
publish defaults) and the release calendar's per-channel view. Voice and
branding are recorded here and validated; applying them to voice selection and
thumbnails is not wired yet (see docs/AUDIT_2026-11-13.md)."""
import json
import os
import re
import time

NAME = "_channels.json"
VERSION = 1
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_TARGET = re.compile(r"^[A-Za-z0-9_.-]+:[a-z*]+$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SLOT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_KEYS = {"id", "name", "targets", "api_key_env", "genres", "series", "voice", "branding", "packaging", "schedule",
         "notes"}
# things that look like a credential pasted where a variable NAME belongs
_SECRETISH = re.compile(r"(sk_live_|AIza|ghp_|xoxb-|-----BEGIN|eyJ[A-Za-z0-9_-]{10,}\.)")


class RegistryError(ValueError):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def _path(root):
    return os.path.join(root, NAME)


def problems(data):
    """Every reason this registry cannot be used ([] = valid)."""
    out = []
    if not isinstance(data, dict):
        return ["the registry must be an object"]
    if _SECRETISH.search(json.dumps(data)):
        out.append("a value looks like a credential — the registry holds variable NAMES only (api_key_env)")
    chans = data.get("channels")
    if not isinstance(chans, list) or not chans:
        return out + ["`channels` must be a non-empty list"]
    ids, owners = [], {}
    for i, c in enumerate(chans):
        tag = f"channel #{i + 1}"
        if not isinstance(c, dict):
            out.append(f"{tag} must be an object")
            continue
        cid = c.get("id")
        tag = f"channel {cid!r}" if cid else tag
        if not isinstance(cid, str) or not _ID.match(cid):
            out.append(f"{tag}: `id` must be lower-case letters, digits, - or _")
        elif cid in ids:
            out.append(f"{tag}: the id is used twice")
        ids.append(cid)
        extra = sorted(set(c) - _KEYS)
        if extra:
            out.append(f"{tag}: unknown field(s) {', '.join(extra)}")
        if not isinstance(c.get("name"), str) or not c["name"].strip():
            out.append(f"{tag}: `name` is required")
        t = c.get("targets")
        if not isinstance(t, list) or not t or not all(isinstance(x, str) and _TARGET.match(x) for x in t):
            out.append(f"{tag}: `targets` must be a list like [\"profile:youtube\"]")
        env = c.get("api_key_env", "UPLOADPOST_API_KEY")
        if not isinstance(env, str) or not _ENV.match(env):
            out.append(f"{tag}: `api_key_env` must be the NAME of an environment variable")
        for field in ("genres", "series"):
            v = c.get(field, [])
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                out.append(f"{tag}: `{field}` must be a list of names")
        for sid in c.get("series") or []:
            if isinstance(sid, str):
                if sid in owners and owners[sid] != cid:
                    out.append(f"series {sid!r} is assigned to both {owners[sid]!r} and {cid!r}")
                owners.setdefault(sid, cid)
        for field in ("voice", "branding", "packaging", "schedule"):
            if field in c and not isinstance(c[field], dict):
                out.append(f"{tag}: `{field}` must be an object")
        sch = c.get("schedule") if isinstance(c.get("schedule"), dict) else {}
        if "slots" in sch and (not isinstance(sch["slots"], list) or not all(isinstance(x, str) and _SLOT.match(x) for x in sch["slots"])):
            out.append(f"{tag}: schedule slots must be HH:MM")
        if "timezone" in sch:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(str(sch["timezone"]))
            except Exception:  # noqa
                out.append(f"{tag}: unknown timezone {sch['timezone']!r}")
        if "daily_max" in sch and not (isinstance(sch["daily_max"], int) and 1 <= sch["daily_max"] <= 24):
            out.append(f"{tag}: daily_max must be 1-24")
        priv = (c.get("packaging") or {}).get("privacy") if isinstance(c.get("packaging"), dict) else None
        if priv is not None and priv not in ("private", "unlisted", "public"):
            out.append(f"{tag}: packaging.privacy must be private, unlisted or public")
    default = data.get("default_channel")
    if default is not None and default not in ids:
        out.append(f"default_channel {default!r} is not one of the channels")
    return out


def implicit(default_targets):
    """The single channel the studio has without a registry."""
    return {"version": VERSION, "implicit": True, "default_channel": "default", "channels": [
        {"id": "default", "name": "Default channel", "targets": list(default_targets or []),
         "api_key_env": "UPLOADPOST_API_KEY", "genres": [], "series": []}]}


def load(root, default_targets=None):
    """The registry, validated. A file that is there and invalid raises
    RegistryError (fail loudly — never fall back to another channel)."""
    try:
        with open(_path(root), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return implicit(default_targets)
    except (OSError, ValueError) as e:
        raise RegistryError([f"{NAME} cannot be read: {e}"])
    bad = problems(data)
    if bad:
        raise RegistryError(bad)
    return data


def save(root, data, by="owner"):
    bad = problems(data)
    if bad:
        raise RegistryError(bad)
    old = None
    try:
        with open(_path(root), encoding="utf-8") as f:
            old = f.read()
    except OSError:
        pass
    out = {**data, "version": VERSION, "updated_at": time.time(), "updated_by": by}
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    if old is not None:                      # one step back, for undo
        with open(_path(root) + ".prev", "w", encoding="utf-8") as f:
            f.write(old)
    os.replace(tmp, _path(root))
    return out


def channel_for(data, series_id, genres=()):
    """The channel a series belongs to: its explicit assignment, else the
    first channel whose genre focus matches, else the default channel."""
    chans = data.get("channels") or []
    for c in chans:
        if series_id and series_id in (c.get("series") or []):
            return c
    want = {str(g).strip().lower() for g in genres or () if g}
    if want:
        for c in chans:
            if want & {str(g).strip().lower() for g in c.get("genres") or []}:
                return c
    default = data.get("default_channel")
    return next((c for c in chans if c.get("id") == default), chans[0] if chans else None)


def targets_for(data, series_id, genres=()):
    c = channel_for(data, series_id, genres)
    return list((c or {}).get("targets") or [])


def missing_credentials(data, environ=None):
    """Channels whose API-key variable is not set — names only, never values."""
    env = os.environ if environ is None else environ
    return [{"channel": c["id"], "variable": c.get("api_key_env", "UPLOADPOST_API_KEY")}
            for c in data.get("channels") or [] if not env.get(c.get("api_key_env", "UPLOADPOST_API_KEY"))]


def summary(data, series_ids=()):
    """Per channel: its series (explicit), targets and schedule — for the UI."""
    rows = []
    for c in data.get("channels") or []:
        rows.append({"id": c["id"], "name": c.get("name"), "targets": c.get("targets"),
                     "genres": c.get("genres") or [], "series": sorted(c.get("series") or []),
                     "schedule": c.get("schedule") or {}, "voice": c.get("voice") or {},
                     "branding": c.get("branding") or {}, "packaging": c.get("packaging") or {}})
    assigned = {s for r in rows for s in r["series"]}
    return {"implicit": bool(data.get("implicit")), "default_channel": data.get("default_channel"),
            "channels": rows, "unassigned_series": sorted(set(series_ids) - assigned)}
