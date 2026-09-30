"""The Work log: everything the agents change, visible IN the tool (2026-09-30).

The owner could not see the work — it lived in manhwa-recap-v1/memory.md, in
commits, and in evidence files on the agent's machine. The owner asked to see
it in the website without having to ask. This module is the single reader:

  * entries come from memory.md (Rule 0 makes every change land there, and
    every push to main redeploys, so the deployed file is exactly what is
    live — the log can never drift from the running system);
  * the live commit comes from Railway (RAILWAY_GIT_COMMIT_SHA);
  * evidence files (cut sheets, listening clips, tables) are stored on the
    persistent volume under projects/_evidence/<slug>/, NOT in git — the repo
    is public and evidence is scraped chapter art. An entry links its
    evidence with a line `**Evidence:** slug-one, slug-two`.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY = os.path.join(HERE, "..", "memory.md")
EVIDENCE = os.path.join(HERE, "projects", "_evidence")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(png|jpg|jpeg|webp|mp3|wav|txt|md|json)$")
MAX_BYTES = 20 * 1024 * 1024
MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
         "webp": "image/webp", "mp3": "audio/mpeg", "wav": "audio/wav",
         "txt": "text/plain", "md": "text/markdown", "json": "application/json"}

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SHA = re.compile(r"\b[0-9a-f]{7}\b")
_EVID = re.compile(r"\*\*Evidence:\*\*\s*(.+)")


def parse(text):
    """memory.md -> entries, NEWEST FIRST (the file appends at the bottom)."""
    entries, cur = [], None
    for line in (text or "").splitlines():
        if line.startswith("### "):
            if cur:
                entries.append(cur)
            title = line[4:].strip()
            m = _DATE.search(title)
            cur = {"title": title, "date": m.group(0) if m else "", "lines": []}
        elif cur is not None:
            cur["lines"].append(line)
    if cur:
        entries.append(cur)
    out = []
    for i, e in enumerate(entries):
        body = "\n".join(e["lines"]).strip()
        ev = []
        for m in _EVID.finditer(body):
            ev += [s.strip().strip("`") for s in m.group(1).split(",")]
        out.append({"n": i + 1, "date": e["date"], "title": e["title"],
                    "body": body,
                    "commits": sorted(set(_SHA.findall(body))),
                    "evidence": [s for s in ev if SLUG_RE.match(s)]})
    return list(reversed(out))


def entries(limit=60):
    try:
        with open(MEMORY, encoding="utf-8") as f:
            return parse(f.read())[:limit]
    except OSError:
        return []


def deployed_commit():
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    return sha[:7] if sha else ""


def evidence_files(slug):
    if not SLUG_RE.match(slug or ""):
        return []
    d = os.path.join(EVIDENCE, slug)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if FILE_RE.match(f))


def evidence_path(slug, name):
    """Safe path for one evidence file, or None. Allowlisted names only, so
    no traversal and no arbitrary file types."""
    if not SLUG_RE.match(slug or "") or not FILE_RE.match(name or ""):
        return None
    return os.path.join(EVIDENCE, slug, name)


def media_type(name):
    return MEDIA.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
