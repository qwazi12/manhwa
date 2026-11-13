"""One verdict per video from the checks the studio already runs.

Why (audit, 2026-11-13): the studio wrote a script critique, validator
findings and a final-media QA report for every chapter, and nothing read them
when a video was approved or posted. On production, of 59 chapters with a
critique 30 were marked "needs_review"; of 96 exports 30 had FAILED final-media
QA; neither stopped or even flagged an approval.

Rule (review, 2026-11-13): a check that is missing, unreadable, unfinished or
about a different version of the file is NOT a pass — it is "unknown", and
unknown never reads as clear. The first version returned "clear" for a chapter
with no critique.json and for one whose critique.json was not valid JSON.

Two levels:
  critical — must not be released (blocks when RELEASE_GATE is "critical" or
             "strict"):
      script_unresolved   an invented fact, wrong speaker or wrong order the
                          editor found and the script still has
      script_unchecked    the script editor did not finish
      script_unreadable   critique.json is there and cannot be read
      script_outdated     the script was changed after the editor checked it
      media_failed        final-media QA failed on this export
      media_unchecked     final-media QA did not finish, or its report cannot
                          be read
      media_outdated      the report is about a different file than the export
                          that is there now
  review   — a person should look (blocks only when RELEASE_GATE is "strict"):
      script_unverified   problems were rewritten but the rewrite was never
                          re-checked (records from before 2026-11-13)
      script_unbound      the record does not say which script it checked
                          (records from before 2026-11-13)
      script_minor        open style / redundancy / flat-dialogue notes
      validator_high      high-severity timeline findings
      validator_unreadable
  missing evidence — level set by RELEASE_GATE_LEGACY:
      script_missing      no critique.json for the chapter
      media_missing       no final-media QA report for this export
      component_missing   a chapter inside a merged video has no folder left
                          and no evidence was stored for it when it was merged

RELEASE_GATE (Railway variable):
  off       (default) nothing is blocked; the verdict is only reported
            (/api/scorecard) — so the effect can be seen before it is enforced
  critical  critical items block approval and posting
  strict    review items block as well

RELEASE_GATE_LEGACY — videos made before these checks existed have no evidence:
  block     (default) missing evidence is critical: such a video is released
            only after it is re-checked, or by a person turning the gate off
  review    missing evidence is a review item (blocks only under "strict")

Merged videos: every chapter in the video answers for itself, through nested
merges too. When a merge is built its chapters' findings are stored in the
merge's own folder (release_evidence.json), so the evidence stays with the
video after the chapter folders are cleaned up.

Undo: set RELEASE_GATE=off (or remove it). No data is changed by the gate.
"""
import hashlib
import json
import os
import time

CRITICAL_TYPES = {"hallucination", "misattributed_dialogue", "misorder", "missed_beat", "spoiled_reveal"}
OPEN = {"not_repaired", "still_flagged_after_rewrite", "direct_speech_contract_failed"}
UNKNOWN = {"script_missing", "script_unreadable", "script_unchecked", "script_outdated", "script_unbound",
           "media_missing", "media_unchecked", "media_outdated", "component_missing", "validator_unreadable"}
EVIDENCE = "release_evidence.json"
MISSING, CORRUPT = object(), object()


def mode():
    m = (os.environ.get("RELEASE_GATE") or "off").strip().lower()
    return m if m in ("off", "critical", "strict") else "off"


def legacy_level():
    """Level of a finding that says "the evidence is not there"."""
    return "review" if (os.environ.get("RELEASE_GATE_LEGACY") or "block").strip().lower() == "review" else "critical"


def _load(path):
    """The JSON object in the file, MISSING, or CORRUPT (unreadable, not JSON,
    or not an object) — three different answers, never merged into one."""
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return MISSING
    except (OSError, ValueError):
        return CORRUPT
    return v if isinstance(v, dict) else CORRUPT


def _read(path):
    v = _load(path)
    return None if v in (MISSING, CORRUPT) else v


def script_digest(pdir):
    """sha256 of the chapter's script.txt as it is now, or None."""
    try:
        with open(os.path.join(pdir, "script.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def script_findings(pdir):
    """Findings from critique.json of ONE chapter folder."""
    c = _load(os.path.join(pdir, "critique.json"))
    if c is MISSING:
        return [{"code": "script_missing", "level": legacy_level(),
                 "detail": "no script-editor record for this chapter — nothing vouches for its script"}]
    if c is CORRUPT:
        return [{"code": "script_unreadable", "level": "critical",
                 "detail": "the script-editor record (critique.json) cannot be read"}]
    out = []
    if c.get("status") != "done" or c.get("outcome") == "incomplete":
        out.append({"code": "script_unchecked", "level": "critical",
                    "detail": f"the script editor did not finish ({str(c.get('status'))[:80]})"})
        return out
    checked, now = c.get("script_sha256"), script_digest(pdir)
    if checked and now and checked != now:
        out.append({"code": "script_outdated", "level": "critical",
                    "detail": "the script was changed after the script editor checked it"})
        return out
    if not checked:
        out.append({"code": "script_unbound", "level": "review",
                    "detail": "the script-editor record does not say which script it checked"})
    unresolved = c.get("unresolved")
    if unresolved is None:
        # oldest records: issues + revised only
        revised = {r.get("unit") for r in c.get("revised") or [] if isinstance(r, dict)}
        unresolved = [{**i, "resolution": "rewritten_unverified" if i.get("unit") in revised else "not_repaired"}
                      for i in c.get("issues") or [] if isinstance(i, dict)]
    if not isinstance(unresolved, list) or not all(isinstance(i, dict) for i in unresolved):
        return [{"code": "script_unreadable", "level": "critical",
                 "detail": "the script-editor record (critique.json) has an unexpected shape"}]
    hard = [i for i in unresolved if i.get("resolution") in OPEN and
            (i.get("type") in CRITICAL_TYPES or i.get("resolution") == "direct_speech_contract_failed")]
    soft = [i for i in unresolved if i.get("resolution") in OPEN and i not in hard]
    unverified = [i for i in unresolved if i.get("resolution") == "rewritten_unverified"]
    if hard:
        out.append({"code": "script_unresolved", "level": "critical", "count": len(hard),
                    "detail": "; ".join(f"unit {i.get('unit')}: {i.get('type')} — {str(i.get('problem'))[:120]}"
                                        for i in hard[:3])})
    if unverified:
        out.append({"code": "script_unverified", "level": "review", "count": len(unverified),
                    "detail": f"{len(unverified)} flagged unit(s) were rewritten and never re-checked"})
    if soft:
        out.append({"code": "script_minor", "level": "review", "count": len(soft),
                    "detail": "; ".join(f"unit {i.get('unit')}: {i.get('type')}" for i in soft[:4])})
    return out


def validator_findings(pdir):
    v = _load(os.path.join(pdir, "validation.json"))
    if v is MISSING:
        return []
    if v is CORRUPT:
        return [{"code": "validator_unreadable", "level": "review",
                 "detail": "the timeline check's record (validation.json) cannot be read"}]
    high = [f for f in v.get("findings") or [] if isinstance(f, dict) and f.get("severity") == "high"]
    if not high:
        return []
    return [{"code": "validator_high", "level": "review", "count": len(high),
             "detail": "; ".join(str(f.get("issue"))[:90] for f in high[:2])}]


def _export_digest(pdir, name):
    """Digest of the export file that is on disk now (None when it is not
    here, e.g. archived to Drive). Uses final-media QA's memoized hash."""
    path = os.path.join(pdir, "exports", os.path.basename(name))
    if not os.path.isfile(path):
        return None
    try:
        import media_qa
        return media_qa.file_digest(path)
    except Exception:  # noqa — no digest means "cannot tell", handled by the caller
        return None


def media_findings(pdir, name):
    if not name:
        return []
    q = _load(os.path.join(pdir, "exports", "_qa", name + ".qa.json"))
    if q is MISSING:
        return [{"code": "media_missing", "level": legacy_level(),
                 "detail": "no final-media QA report for this export"}]
    if q is CORRUPT:
        return [{"code": "media_unchecked", "level": "critical",
                 "detail": "the final-media QA report cannot be read"}]
    st = q.get("status")
    if st == "failed":
        crit = [f for f in q.get("findings") or [] if f.get("severity") in ("critical", "high", "error")]
        return [{"code": "media_failed", "level": "critical", "count": len(crit) or None,
                 "detail": "; ".join(f"{f.get('rule')}: {str(f.get('explanation'))[:90]}" for f in (crit or q.get("findings") or [])[:3])
                           or "final-media QA failed"}]
    if st != "passed":
        return [{"code": "media_unchecked", "level": "critical",
                 "detail": f"final-media QA did not finish ({str(st)[:60]})"}]
    on_disk = _export_digest(pdir, name)
    if on_disk and q.get("export_digest") and on_disk != q["export_digest"]:
        return [{"code": "media_outdated", "level": "critical",
                 "detail": "the export was replaced after final-media QA passed it"}]
    return []


# ------------------------------------------------------------ merged videos

def _is_merge(pdir):
    return (_read(os.path.join(pdir, "project.json")) or {}).get("kind") == "range"


def components(pdir, name=None):
    """Folders a merged video was built from: the QA manifest of this export,
    else the project's own record (merged_from). [] for a single chapter.
    Paths are returned whether or not the folder still exists."""
    root = os.path.dirname(pdir.rstrip("/"))
    m = _read(os.path.join(pdir, "exports", "_qa", (name or "") + ".manifest.json")) or {}
    listed = [c.get("project") for c in (m.get("context") or {}).get("components") or [] if isinstance(c, dict)]
    if not listed:
        meta = _read(os.path.join(pdir, "project.json")) or {}
        listed = [c.get("project") for c in meta.get("merged_from") or [] if isinstance(c, dict)]
    out = []
    for p in listed:
        if not p:
            continue
        p = p if os.path.isabs(p) else os.path.join(root, p)
        if p not in out:
            out.append(p)
    return out


def chapter_snapshot(pdir):
    """What is known about ONE chapter right now, in a form that can be kept."""
    return {"at": time.time(), "script_sha256": script_digest(pdir),
            "findings": script_findings(pdir) + validator_findings(pdir)}


def _collect(pdir, stored, seen, depth=0):
    """[(chapter name, findings)] for every chapter inside this folder, through
    nested merges; a chapter that cannot be found is reported, not skipped."""
    key = os.path.basename(pdir.rstrip("/"))
    if key in seen or depth > 8:
        return []
    seen.add(key)
    if os.path.isdir(pdir):
        if _is_merge(pdir):
            inner = dict(stored)
            inner.update((_read(os.path.join(pdir, EVIDENCE)) or {}).get("chapters") or {})
            out = []
            for part in components(pdir, _merge_export(pdir)):
                out += _collect(part, inner, seen, depth + 1)
            return out or [(key, [{"code": "component_missing", "level": legacy_level(),
                                   "detail": "this merged video has no record of the chapters in it"}])]
        return [(key, script_findings(pdir) + validator_findings(pdir))]
    kept = stored.get(key)
    if isinstance(kept, dict) and isinstance(kept.get("parts"), list):      # a merge that is gone
        out = []
        for part in kept["parts"]:
            out += _collect(os.path.join(os.path.dirname(pdir.rstrip("/")), part), stored, seen, depth + 1)
        return out
    if isinstance(kept, dict) and isinstance(kept.get("findings"), list):
        return [(key, [{**f, "stored": True} for f in kept["findings"] if isinstance(f, dict)])]
    return [(key, [{"code": "component_missing", "level": legacy_level(),
                    "detail": "this chapter's folder is gone and no check results were stored for it"}])]


def _merge_export(pdir):
    b = (_read(os.path.join(pdir, "project.json")) or {}).get("build") or {}
    return b.get("export") or ""


def store_evidence(merge_dir, part_dirs):
    """Keep each chapter's findings inside the merged video's folder, at build
    time, so they outlive the chapter folders. Nested merges are flattened:
    their chapters' findings are copied, and the merge is remembered as a
    list of its parts."""
    chapters = {}

    def add(p, depth=0):
        key = os.path.basename(p.rstrip("/"))
        if key in chapters or depth > 8:
            return
        if os.path.isdir(p) and _is_merge(p):
            own = (_read(os.path.join(p, EVIDENCE)) or {}).get("chapters") or {}
            parts = components(p, _merge_export(p))
            chapters[key] = {"parts": [os.path.basename(x.rstrip("/")) for x in parts]}
            for x in parts:
                if os.path.isdir(x):
                    add(x, depth + 1)
                elif os.path.basename(x.rstrip("/")) in own:
                    chapters.setdefault(os.path.basename(x.rstrip("/")), own[os.path.basename(x.rstrip("/"))])
            for k, v in own.items():           # anything the inner merge still vouches for
                chapters.setdefault(k, v)
        elif os.path.isdir(p):
            chapters[key] = chapter_snapshot(p)

    for p in part_dirs:
        add(p)
    path = os.path.join(merge_dir, EVIDENCE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "stored_at": time.time(), "chapters": chapters}, f, indent=1)
    os.replace(tmp, path)
    return chapters


def findings(pdir, name=None):
    """Everything known about this video: its own media report, plus the
    script and timeline findings of the chapter — or, for a merged video, of
    every chapter in it (live folder, else the evidence stored at build)."""
    out = list(media_findings(pdir, name))
    parts = components(pdir, name)
    if not parts and not _is_merge(pdir):
        return out + script_findings(pdir) + validator_findings(pdir)
    stored = (_read(os.path.join(pdir, EVIDENCE)) or {}).get("chapters") or {}
    seen = {os.path.basename(pdir.rstrip("/"))}
    got = []
    for part in parts:
        got += _collect(part, stored, seen, 1)
    if not got:
        got = [("", [{"code": "component_missing", "level": legacy_level(),
                      "detail": "this merged video has no record of the chapters in it"}])]
    for chapter, fs in got:
        out += [{**f, "chapter": chapter} if chapter else f for f in fs]
    return out


def verdict(pdir, name=None):
    fs = findings(pdir, name)
    crit = [f for f in fs if f["level"] == "critical"]
    rev = [f for f in fs if f["level"] == "review"]
    return {"release": "blocked" if crit else ("review" if rev else "clear"),
            "critical": crit, "review": rev,
            # evidence that is missing / unreadable / about another version
            "unknown": [f["code"] for f in fs if f["code"] in UNKNOWN]}


def blocking(pdir, name=None):
    """Plain-language blockers for approval/posting under the current mode."""
    m = mode()
    if m == "off":
        return []
    v = verdict(pdir, name)
    items = v["critical"] + (v["review"] if m == "strict" else [])
    return [f"Release check ({f['code']}{', ' + f['chapter'] if f.get('chapter') else ''}): {f['detail']}"
            for f in items]
