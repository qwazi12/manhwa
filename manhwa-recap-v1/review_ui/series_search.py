"""The series search pack: the name people search, the hook, the other names.

Owner, 2026-11-14 (INSTR_FLAMINGO_20261114T1400Z, SEO_SERIES_PACK_20261114):
a video's title uses the name people SEARCH for the series, which is not always
the library's name; the other names go in the description's "Also known as"
line; each series has one locked hook; the description carries exactly three
hashtags. One record per series, keyed by series id, kept for series that have
no chapter made yet as well (the older per-series record needs a project).

    search_title   the name used in titles
    hook           the locked hook
    hook_status    "locked"
    aka_names      every other name, in order
    characters     main characters
    hashtags       exactly three, in order
    evidence       "supported" | "partly" | "none found"

Pure text rules plus a small file store. Nothing here calls a model or a site.
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time

NAME = "_series_search_pack.json"
TITLE_MAX = 100
FIELDS = ("search_title", "hook", "hook_status", "aka_names", "characters", "hashtags", "evidence",
          "library_title", "notes")
_LOCK = threading.RLock()
_APOS = str.maketrans({"’": "'", "‘": "'", "`": "'"})
_TAG = re.compile(r"#[^\s#]+")


class PackError(ValueError):
    pass


def straight(s):
    """Straight apostrophes (people type both; titles use the straight one)."""
    return (s or "").translate(_APOS)


def norm(series_id):
    """One key for the ids a series goes by: the watchlist's
    ('the-extra-s-academy-survival-guide') and a project's
    ('the-extras-academy-survival-guide-6465')."""
    s = re.sub(r"[^a-z0-9]+", "-", (series_id or "").lower()).strip("-")
    s = re.sub(r"-\d{3,}$", "", s)
    return re.sub(r"-([st])-", r"\1-", s)


def _path(root):
    return os.path.join(root, NAME)


def load(root):
    for p in (_path(root), _path(root) + ".bak"):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("series"), dict):
                return d
        except (OSError, ValueError):
            continue
    return {"series": {}}


def _save(root, data):
    os.makedirs(root, exist_ok=True)
    p = _path(root)
    fd, tmp = tempfile.mkstemp(prefix=NAME + ".", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(p):
            shutil.copyfile(p, p + ".bak")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def get(root, series_id):
    return (load(root).get("series") or {}).get(norm(series_id))


def _clean(rec):
    if not isinstance(rec, dict) or not str(rec.get("series_id") or "").strip():
        raise PackError("every record needs a series_id")
    sid = str(rec["series_id"]).strip()
    name = straight(" ".join(str(rec.get("search_title") or "").split()))
    hook = straight(" ".join(str(rec.get("hook") or "").split()))
    if not name or not hook:
        raise PackError(f"{sid}: search_title and hook are both required")
    for ch in "<>":
        if ch in name or ch in hook:
            raise PackError(f"{sid}: YouTube refuses {ch} in a title")
    tags = [str(t).strip() for t in rec.get("hashtags") or []]
    if len(tags) != 3 or not all(_TAG.fullmatch(t) for t in tags) or len({t.lower() for t in tags}) != 3:
        raise PackError(f"{sid}: exactly three different hashtags like #Word are required")
    if len(f"[1] {hook} | {name}") > TITLE_MAX:
        raise PackError(f"{sid}: the hook and name alone are over {TITLE_MAX} characters")

    def names(key):
        out, seen = [], {name.lower()}
        for x in rec.get(key) or []:
            x = straight(" ".join(str(x or "").split())).strip(" ,;")
            if x and x.lower() not in seen:
                seen.add(x.lower())
                out.append(x)
        return out
    return sid, {"series_id": sid, "search_title": name, "hook": hook,
                 "hook_status": str(rec.get("hook_status") or "locked"),
                 "aka_names": names("aka_names"), "characters": names("characters"), "hashtags": tags,
                 "evidence": str(rec.get("evidence") or ""), "library_title": str(rec.get("library_title") or ""),
                 "notes": str(rec.get("notes") or "")}


def upsert(root, records, source=""):
    """Save these records (all or none). Returns the ids saved."""
    if not isinstance(records, list) or not records:
        raise PackError("nothing to save: `records` must be a non-empty list")
    cleaned = [_clean(r) for r in records]
    keys = [norm(sid) for sid, _ in cleaned]
    if len(set(keys)) != len(keys):
        raise PackError("a series is listed twice")
    with _LOCK:
        data = load(root)
        for (sid, rec), k in zip(cleaned, keys):
            rec.update(source=source, updated_at=time.time())
            data["series"][k] = rec
        _save(root, data)
    return [sid for sid, _ in cleaned]


def title_for(rec, label):
    """-> (title, None) or (None, why not). Never shortens a locked hook."""
    t = f"[{label}] {rec['hook']} | {rec['search_title']}"
    if len(t) > TITLE_MAX:
        return None, f"the title would be {len(t)} characters (YouTube allows {TITLE_MAX}): {t}"
    return t, None


def _is_tag_line(line):
    s = line.strip()
    return bool(s) and all(_TAG.fullmatch(w) for w in s.split())


def series_block(rec):
    name = rec["search_title"]
    aka = list(rec.get("aka_names") or [])
    plain = name.replace("'", "")
    if plain != name and plain.lower() not in {a.lower() for a in aka}:
        aka.append(plain)                      # the name once with no apostrophe (people type it both ways)
    rows = [f"\U0001F4D6 Series: {name}"]
    if aka:
        rows.append("Also known as: " + ", ".join(aka))
    if rec.get("characters"):
        rows.append("Main characters: " + ", ".join(rec["characters"]))
    return rows


def description_for(desc, rec, label):
    """The description in the pack's format: line 1 leads with the search
    name, the series block names it with its other names and characters, and
    the hashtag line is the pack's three. Everything else is kept as written."""
    lines = straight(desc or "").replace("\r\n", "\n").split("\n")
    name = rec["search_title"]
    word = "Chapters" if "-" in str(label) else "Chapter"
    m = re.match(r"^.*?\s+Chapters?\s+[\d.]+(?:\s*(?:-|to)\s*[\d.]+)?\s+recap \(English\)!\s*(.*)$", lines[0]) if lines else None
    if m:
        lines[0] = f"{name} {word} {label} recap (English)! {m.group(1)}".rstrip()
    block = series_block(rec)
    at = next((i for i, ln in enumerate(lines) if ln.startswith("\U0001F4D6 Series:")), None)
    if at is None:
        at = next((i for i, ln in enumerate(lines) if ln.startswith("Series:")), None)
    if at is not None:
        end = at + 1
        while end < len(lines) and lines[end].startswith(("Also known as:", "Main characters:")):
            end += 1
        lines[at:end] = block
    else:
        cut = next((i for i, ln in enumerate(lines) if not ln.strip()), len(lines))
        lines[cut:cut] = [""] + block
    lines = [ln for ln in lines if not _is_tag_line(ln)]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines + ["", " ".join(rec["hashtags"])])


def apply(md, rec, label):
    """A video's details with the series' search pack applied.
    -> (new details, [changed fields], [problems]). A title that does not fit
    is a problem and the title is left as it was."""
    new, probs = dict(md), []
    t, why = title_for(rec, label)
    if t:
        new["title"] = t
    else:
        probs.append(why)
    new["description"] = description_for(md.get("description"), rec, label)
    new["hashtags"] = list(rec["hashtags"])
    changed = [k for k in ("title", "description", "hashtags") if new.get(k) != md.get(k)]
    return new, changed, probs
