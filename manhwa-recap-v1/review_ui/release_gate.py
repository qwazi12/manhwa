"""One verdict per exported video, from evidence tied to that exact video.

Why (audit, 2026-11-13): the studio wrote a script critique, validator
findings and a final-media QA report for every chapter, and nothing read them
when a video was approved or posted.

Evidence model (third version, 2026-11-14). The first version called a video
"clear" when its checks were missing; the second still did when the files the
checks named were gone, when a report carried no hash, or when the narration
had been edited on the board after the script editor ran. The rule now:

    A check counts only when it is PROVEN to be about the thing released.
    Everything else is "unknown", and unknown is never "clear".

What "about the thing released" means, per check:

  script editor   critique.json records `narration_sha256`: the spoken lines
  (semantic)      (text, order, exclusions) the pipeline built from the script
                  it checked. The released video's lines come from its render
                  manifest (frozen at export). Equal -> the check covers what
                  is spoken. Different -> the lines were changed after the
                  check: stale evidence. If a person then approved this exact
                  export (reviews.json pins its media identity) it is a review
                  item; otherwise unknown. A changed hash only says the
                  evidence is stale — it does not say the new lines are wrong.
  sync / media    the QA report must name the manifest it inspected
                  (manifest_digest), the render inputs (selected images,
                  audio files, timeline: input_digest) and the exported bytes
                  (export_digest), and those must match the manifest and the
                  file. A report with no hash, or a file whose hash cannot be
                  computed, is unknown.
  archived video  an export that is not on disk is "archived" only when
                  drive.json names it, says Drive's copy was verified, and
                  records the same sha256 the QA report checked. Otherwise it
                  is missing (unknown) — absence never passes.
  compilation     evidence is stored per export of the compilation
                  (release_evidence.json -> exports[name]): each piece with
                  the sha256 of the bytes that were stitched and the findings
                  for that version, nested compilations included. The stored
                  record is the authority for that export, so editing a
                  chapter later neither clears nor condemns an older
                  compilation. Without a stored record, a piece's live checks
                  count only when the live export still has the hash the
                  compilation's manifest recorded.

Levels:
  critical  a defect was found: unresolved invented fact / wrong speaker /
            wrong order, or final-media QA failed
  unknown   evidence is missing, unreadable, unfinished, unbound, stale or
            cannot be verified (codes in UNKNOWN_CODES)
  review    a person should look: open style notes, unverified rewrites,
            timeline findings, narration edited after the check but approved
            by a person

Verdict: blocked (any critical) / unknown / review / clear.

RELEASE_GATE (Railway variable) — keep "off" in production until calibrated:
  off       (default) nothing is blocked; the verdict is only reported
  critical  blocked and unknown stop approval and posting
  strict    review items stop them too

RELEASE_GATE_LEGACY — videos made before this evidence existed:
  block     (default) their missing / unbound evidence stays unknown
  review    the codes in LEGACY_CODES become review items instead. Unreadable,
            unfinished, mismatched and missing-file findings are never
            downgraded.

Undo: set RELEASE_GATE=off (or remove it). The gate changes no data.
"""
import copy
import hashlib
import json
import os
import time

CRITICAL_TYPES = {"hallucination", "misattributed_dialogue", "misorder", "missed_beat", "spoiled_reveal"}
OPEN = {"not_repaired", "still_flagged_after_rewrite", "direct_speech_contract_failed"}
EVIDENCE = "release_evidence.json"
EVIDENCE_VERSION = 2
MAX_DEPTH = 6
MISSING, CORRUPT = object(), object()

# evidence that was never recorded for older videos — the only codes
# RELEASE_GATE_LEGACY=review may downgrade
LEGACY_CODES = {"script_missing", "narration_unbound", "media_missing", "archive_unbound",
                "component_unbound", "evidence_missing"}
UNKNOWN_CODES = LEGACY_CODES | {
    "script_unreadable", "script_unchecked", "script_outdated", "narration_unavailable", "narration_changed",
    "media_unreadable", "media_unchecked", "media_unbound", "media_outdated", "media_unverifiable",
    "export_missing", "manifest_missing", "validator_unreadable",
    "component_missing", "component_changed", "component_mismatch", "component_cycle", "component_depth",
    "evidence_unreadable"}


def mode():
    m = (os.environ.get("RELEASE_GATE") or "off").strip().lower()
    return m if m in ("off", "critical", "strict") else "off"


def legacy_review():
    return (os.environ.get("RELEASE_GATE_LEGACY") or "block").strip().lower() == "review"


def _f(code, detail, level=None, **kw):
    if level is None:
        level = "unknown" if code in UNKNOWN_CODES else "review"
        if code in LEGACY_CODES and legacy_review():
            level = "review"
    return {"code": code, "level": level, "detail": detail, **kw}


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
    return None if v is MISSING or v is CORRUPT else v


def _name(pdir):
    return os.path.basename(str(pdir).rstrip("/"))


# ------------------------------------------------------------- identities

def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def narration_digest(texts):
    """Identity of what is spoken: the lines, in order. An excluded or
    reordered line changes it."""
    return _sha([str(t if t is not None else "") for t in texts])


def board_narration(pdir):
    """The lines the board would speak now (selected segments, their beats in
    order), or None when the board cannot be read."""
    if not os.path.isfile(os.path.join(pdir, "segments.json")):
        return None
    try:
        import media_qa
        return narration_digest([b.get("text", "") for s in media_qa._selected(pdir) for b in s.get("beats", [])])
    except Exception:  # noqa — unreadable board: the caller reports "unavailable"
        return None


def script_digest(pdir):
    try:
        with open(os.path.join(pdir, "script.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def bind_critique(pdir):
    """Called by ingest right after it builds segments.json from the script it
    just checked: record which spoken lines the script editor's verdict covers.
    Refuses (returns False) unless critique.json is about the script on disk."""
    path = os.path.join(pdir, "critique.json")
    c = _read(path)
    if not c or not c.get("script_sha256") or c["script_sha256"] != script_digest(pdir):
        return False
    digest = board_narration(pdir)
    if not digest:
        return False
    c.update(narration_sha256=digest, narration_bound_at=time.time())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return True


def _qa_paths(pdir, name):
    base = os.path.join(pdir, "exports", "_qa")
    return os.path.join(base, name + ".manifest.json"), os.path.join(base, name + ".qa.json")


def _file_sha(path):
    """sha256 of a file through final-media QA's memoized hasher; None when
    the file is not there or cannot be hashed."""
    if not os.path.isfile(path):
        return None
    try:
        import media_qa
        return media_qa.file_digest(path)
    except Exception:  # noqa
        return None


def export_identity(pdir, name):
    """Where the export's bytes are and what they are:
      {"state": "local", "sha256": ...}        on disk, hashed
      {"state": "unhashable"}                  on disk, hash failed
      {"state": "archived", "sha256": ...}     not on disk; drive.json names it,
                                               verified, with the sha256 recorded
      {"state": "archived_unbound"}            in Drive, but no sha256 on record
      {"state": "missing"}                     not on disk, no archive record"""
    path = os.path.join(pdir, "exports", os.path.basename(name or ""))
    if name and os.path.isfile(path):
        sha = _file_sha(path)
        return {"state": "local", "sha256": sha} if sha else {"state": "unhashable"}
    rec = _read(os.path.join(pdir, "drive.json")) or {}
    vid = (rec.get("files") or {}).get("video") if isinstance(rec.get("files"), dict) else None
    if name and rec.get("video") == name and isinstance(vid, dict) and vid.get("id"):
        if rec.get("verified") is True and isinstance(rec.get("sha256"), str) and rec["sha256"]:
            return {"state": "archived", "sha256": rec["sha256"]}
        return {"state": "archived_unbound"}
    return {"state": "missing"}


# ---------------------------------------------------------------- checks

def media_findings(pdir, name, ident=None):
    """Final-media QA for this export, bound to its manifest and its bytes."""
    if not name:
        return []
    mpath, qpath = _qa_paths(pdir, name)
    q = _load(qpath)
    if q is MISSING:
        return [_f("media_missing", "no final-media QA report for this export")]
    if q is CORRUPT:
        return [_f("media_unreadable", "the final-media QA report cannot be read")]
    st = q.get("status")
    if st == "failed":
        crit = [x for x in q.get("findings") or [] if isinstance(x, dict) and x.get("severity") in ("critical", "high", "error")]
        shown = crit or [x for x in q.get("findings") or [] if isinstance(x, dict)]
        return [_f("media_failed", "; ".join(f"{x.get('rule')}: {str(x.get('explanation'))[:90]}" for x in shown[:3])
                   or "final-media QA failed", "critical", count=len(crit) or None)]
    if st != "passed":
        return [_f("media_unchecked", f"final-media QA did not finish ({str(st)[:60]})")]
    # passed — but about what?
    checked = q.get("export_digest")
    if not isinstance(checked, str) or not checked:
        return [_f("media_unbound", "the final-media QA report does not say which file it checked")]
    ident = ident or export_identity(pdir, name)
    if ident["state"] == "missing":
        return [_f("export_missing", "the exported video is not on disk and has no verified archive copy")]
    if ident["state"] == "unhashable":
        return [_f("media_unverifiable", "the exported video is there but its hash could not be computed")]
    if ident["state"] == "archived_unbound":
        return [_f("archive_unbound", "the exported video is archived, but the archive record does not carry "
                                      "the hash needed to tie it to the QA report")]
    if ident["sha256"] != checked:
        return [_f("media_outdated", "final-media QA passed a different file than the export "
                                     + ("on disk" if ident["state"] == "local" else "in the archive"))]
    m = _load(mpath)
    if m is MISSING or m is CORRUPT:
        return [_f("manifest_missing", "the render manifest (which images, audio and timing went into this "
                                       "export) is missing or unreadable")]
    try:
        import media_qa
        m_digest = media_qa._digest(m)
        version_ok = q.get("version") == media_qa.VERSION
    except Exception:  # noqa
        m_digest, version_ok = None, False
    if not m_digest or q.get("manifest_digest") != m_digest or q.get("input_digest") != m.get("input_digest") \
            or m.get("export_digest") != checked or not version_ok:
        return [_f("media_outdated", "final-media QA was run against a different render manifest (images, "
                                     "audio or timing) than the one stored for this export")]
    return []


def _human_approved(pdir, name):
    """A person approved exactly this export: reviews.json says approved and
    pins the media identity (export bytes + manifest) that is there now."""
    rec = ((_read(os.path.join(pdir, "reviews.json")) or {}).get(name)) or {}
    if not isinstance(rec, dict):
        return False
    pinned = rec.get("media_identity")
    if rec.get("status") != "approved" or not isinstance(pinned, dict) or not pinned.get("export_digest"):
        return False
    m = _read(_qa_paths(pdir, name)[0])
    if not m:
        return False
    try:
        import media_qa
        return pinned.get("manifest_digest") == media_qa._digest(m) and pinned["export_digest"] == m.get("export_digest")
    except Exception:  # noqa
        return False


def spoken_digest(pdir, name):
    """The lines this export speaks (from its render manifest), or — with no
    export named — the lines on the board now. None when unavailable."""
    if not name:
        return board_narration(pdir)
    m = _read(_qa_paths(pdir, name)[0])
    if not m or not isinstance(m.get("events"), list) or (m.get("context") or {}).get("kind") == "range":
        return None
    return narration_digest([e.get("text", "") for e in m["events"] if isinstance(e, dict)])


def script_findings(pdir, name=None):
    """The script editor's verdict for ONE chapter, and whether it covers the
    lines this export actually speaks."""
    c = _load(os.path.join(pdir, "critique.json"))
    if c is MISSING:
        return [_f("script_missing", "no script-editor record for this chapter — nothing vouches for its script")]
    if c is CORRUPT:
        return [_f("script_unreadable", "the script-editor record (critique.json) cannot be read")]
    if c.get("status") != "done" or c.get("outcome") == "incomplete":
        return [_f("script_unchecked", f"the script editor did not finish ({str(c.get('status'))[:80]})")]
    out = []
    checked_script, checked_lines = c.get("script_sha256"), c.get("narration_sha256")
    now_script = script_digest(pdir)
    if checked_script and now_script and checked_script != now_script:
        return [_f("script_outdated", "the script file was changed after the script editor checked it")]
    if not isinstance(checked_lines, str) or not checked_lines:
        out.append(_f("narration_unbound", "the script-editor record does not say which spoken lines it covers"))
    else:
        spoken = spoken_digest(pdir, name)
        if not spoken:
            out.append(_f("narration_unavailable", "the lines this video speaks cannot be read (no render manifest "
                                                   "or board), so the script check cannot be tied to them"))
        elif spoken != checked_lines:
            if name and _human_approved(pdir, name):
                out.append(_f("narration_edited", "the spoken lines were edited after the script editor ran; a person "
                                                  "approved this exact export", "review"))
            else:
                out.append(_f("narration_changed", "the spoken lines were changed after the script editor checked "
                                                   "them; the check does not cover what is spoken now"))
    unresolved = c.get("unresolved")
    if unresolved is None:
        # oldest records: issues + revised only
        revised = {r.get("unit") for r in c.get("revised") or [] if isinstance(r, dict)}
        unresolved = [{**i, "resolution": "rewritten_unverified" if i.get("unit") in revised else "not_repaired"}
                      for i in c.get("issues") or [] if isinstance(i, dict)]
    if not isinstance(unresolved, list) or not all(isinstance(i, dict) for i in unresolved):
        return [_f("script_unreadable", "the script-editor record (critique.json) has an unexpected shape")]
    hard = [i for i in unresolved if i.get("resolution") in OPEN and
            (i.get("type") in CRITICAL_TYPES or i.get("resolution") == "direct_speech_contract_failed")]
    soft = [i for i in unresolved if i.get("resolution") in OPEN and i not in hard]
    unverified = [i for i in unresolved if i.get("resolution") == "rewritten_unverified"]
    if hard:
        out.append(_f("script_unresolved", "; ".join(f"unit {i.get('unit')}: {i.get('type')} — {str(i.get('problem'))[:120]}"
                                                     for i in hard[:3]), "critical", count=len(hard)))
    if unverified:
        out.append(_f("script_unverified", f"{len(unverified)} flagged unit(s) were rewritten and never re-checked",
                      "review", count=len(unverified)))
    if soft:
        out.append(_f("script_minor", "; ".join(f"unit {i.get('unit')}: {i.get('type')}" for i in soft[:4]),
                      "review", count=len(soft)))
    return out


def validator_findings(pdir):
    """Board timeline rules (advisory: review level; not bound to an export)."""
    v = _load(os.path.join(pdir, "validation.json"))
    if v is MISSING:
        return []
    if v is CORRUPT:
        return [_f("validator_unreadable", "the timeline check's record (validation.json) cannot be read")]
    high = [x for x in v.get("findings") or [] if isinstance(x, dict) and x.get("severity") == "high"]
    if not high:
        return []
    return [_f("validator_high", "; ".join(str(x.get("issue"))[:90] for x in high[:2]), "review", count=len(high))]


def chapter_findings(pdir, name):
    """Everything about ONE single-chapter export."""
    return media_findings(pdir, name) + script_findings(pdir, name) + validator_findings(pdir)


# ------------------------------------------------------------ compilations

def _meta(pdir):
    return _read(os.path.join(pdir, "project.json")) or {}


def _is_merge(pdir):
    return _meta(pdir).get("kind") == "range"


def _manifest_components(pdir, name):
    """[(project dir, export name, sha256 the manifest recorded or None)] of a
    compilation export, from its render manifest; None when there is none."""
    m = _read(_qa_paths(pdir, name)[0]) if name else None
    if not m or (m.get("context") or {}).get("kind") != "range":
        return None
    root = os.path.dirname(str(pdir).rstrip("/"))
    inputs = m.get("inputs") if isinstance(m.get("inputs"), dict) else {}
    hashes = {(_name(c.get("project") or ""), c.get("name")): c.get("export")
              for c in inputs.get("components") or [] if isinstance(c, dict)}
    out = []
    for c in (m.get("context") or {}).get("components") or []:
        if not isinstance(c, dict) or not c.get("project") or not c.get("name"):
            return None
        # manifests store the build machine's absolute path: use the folder name
        out.append((os.path.join(root, _name(c["project"])), c["name"], hashes.get((_name(c["project"]), c["name"]))))
    return out


def _recorded_components(pdir, name):
    """Manifest first; else project.json's merged_from (no hashes)."""
    got = _manifest_components(pdir, name)
    if got is not None:
        return got
    root = os.path.dirname(str(pdir).rstrip("/"))
    return [(os.path.join(root, _name(c["project"])), c.get("name") or "", None)
            for c in _meta(pdir).get("merged_from") or [] if isinstance(c, dict) and c.get("project")]


def component_entry(pdir, name, _trail=()):
    """What is known about one piece AT THIS MOMENT, in a form that is stored
    with the compilation: identity of the bytes, the lines spoken, and the
    findings for exactly that version. A piece that is itself a compilation
    carries its own pieces."""
    key = (_name(pdir), name)
    entry = {"project": key[0], "name": name, "at": time.time()}
    if key in _trail:
        return {**entry, "findings": [_f("component_cycle", f"{key[0]} contains itself")]}
    if len(_trail) >= MAX_DEPTH:
        return {**entry, "findings": [_f("component_depth", f"compilations nested deeper than {MAX_DEPTH} levels")]}
    if not os.path.isdir(pdir):
        return {**entry, "findings": [_f("component_missing", "this piece's folder is not there")]}
    ident = export_identity(pdir, name)
    entry["export_sha256"] = ident.get("sha256")
    entry["export_state"] = ident["state"]
    if _is_merge(pdir):
        entry["kind"] = "range"
        entry["findings"] = media_findings(pdir, name, ident)
        entry["components"], extra = _pieces(pdir, name, _trail + (key,))
        entry["findings"] += extra
    else:
        entry["kind"] = "chapter"
        entry["narration_sha256"] = spoken_digest(pdir, name)
        entry["findings"] = chapter_findings(pdir, name)
    return entry


def _valid_entry(e):
    return (isinstance(e, dict) and isinstance(e.get("project"), str) and isinstance(e.get("name"), str)
            and isinstance(e.get("findings"), list)
            and all(isinstance(x, dict) and isinstance(x.get("code"), str)
                    and x.get("level") in ("critical", "unknown", "review") for x in e["findings"])
            and (e.get("kind") != "range" or (isinstance(e.get("components"), list)
                                              and all(_valid_entry(c) for c in e["components"]))))


def _pieces(pdir, name, _trail):
    """([entries], [findings about the list itself]) for a compilation export:
    its stored record when there is one, else its pieces as they are now —
    each accepted only if it still has the hash the manifest recorded."""
    ev = _load(os.path.join(pdir, EVIDENCE))
    listed = _recorded_components(pdir, name)
    if ev is CORRUPT:
        return [], [_f("evidence_unreadable", f"{_name(pdir)}: the stored record of its pieces cannot be read")]
    exports = ev.get("exports") if ev is not MISSING else None
    if ev is not MISSING and not isinstance(exports, dict):
        return [], [_f("evidence_unreadable", f"{_name(pdir)}: the stored record of its pieces has an unexpected shape")]
    rec = (exports or {}).get(name)
    if rec is not None:
        comps = rec.get("components") if isinstance(rec, dict) else None
        if not isinstance(comps, list) or not comps or not all(_valid_entry(e) for e in comps):
            return [], [_f("evidence_unreadable", f"{_name(pdir)}: the stored record of its pieces has an unexpected shape")]
        extra = []
        if listed and [(_name(p), n) for p, n, _h in listed] != [(e["project"], e["name"]) for e in comps]:
            extra.append(_f("component_mismatch", f"{_name(pdir)}: the stored record lists different pieces than "
                                                  "the render manifest of this export"))
        else:
            for (_p, _n, h), e in zip(listed or [], comps):
                if h and h != e.get("export_sha256"):
                    extra.append(_f("component_mismatch", f"{e['project']}: the stored record is about a different "
                                                          "file than the one stitched into this export"))
        return copy.deepcopy(comps), extra
    # no stored record for this export (built before 2026-11-14)
    if not listed:
        return [], [_f("component_missing", f"{_name(pdir)}: no record of the pieces in this compilation")]
    out = []
    for p, n, h in listed:
        e = component_entry(p, n, _trail)
        if e.get("kind"):
            if not h:
                e["findings"] = [_f("component_unbound", "the compilation did not record which version of this "
                                                         "piece it used, so its current checks cannot be tied to it")]
                e["components"] = [] if e["kind"] == "range" else e.get("components")
            elif e.get("export_sha256") != h:
                e["findings"] = [_f("component_changed", "this piece was changed (or its file is gone) after the "
                                                         "compilation was built; its current checks say nothing "
                                                         "about the version inside")]
                e["components"] = [] if e["kind"] == "range" else e.get("components")
            if e["kind"] != "range":
                e.pop("components", None)
        out.append(e)
    return out, [_f("evidence_missing", f"{_name(pdir)}: built before evidence was stored with compilations; "
                                        "its pieces were matched by file hash instead")]


def store_evidence(merge_dir, name, pieces):
    """Store, for THIS export of the compilation, each piece's identity and
    findings as they are now. Call it when the pieces' files are on disk
    (after they are fetched for the stitch). pieces: [(dir, export name)].
    Other exports' records in the file are kept."""
    comps = [component_entry(p, n, ((_name(merge_dir), name),)) for p, n in pieces]
    path = os.path.join(merge_dir, EVIDENCE)
    old = _read(path) or {}
    exports = old.get("exports") if isinstance(old.get("exports"), dict) else {}
    exports[name] = {"built_at": time.time(), "components": comps}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": EVIDENCE_VERSION, "exports": exports}, f, indent=1)
    os.replace(tmp, path)
    return comps


_STRUCTURAL = ("component_", "evidence_")


def _flatten(entries, trail, out):
    """Stored entries -> findings tagged with the chapter they belong to.
    Depth and cycles are checked again here: stored data may be malformed."""
    for e in entries:
        key = (e.get("project"), e.get("name"))
        if key in trail:
            out.append({**_f("component_cycle", f"{key[0]} contains itself"), "chapter": key[0]})
            continue
        if len(trail) > MAX_DEPTH:
            out.append({**_f("component_depth", f"compilations nested deeper than {MAX_DEPTH} levels"), "chapter": key[0]})
            continue
        own = [x for x in e.get("findings") or [] if isinstance(x, dict)]
        for x in own:
            x = dict(x)
            x.setdefault("chapter", key[0])
            if x.get("code") in LEGACY_CODES and x.get("level") in ("unknown", "review"):
                x["level"] = "review" if legacy_review() else "unknown"     # policy is read now, not at build
            out.append(x)
        if e.get("kind") == "range":
            if not e.get("components") and not any(str(x.get("code")).startswith(_STRUCTURAL) for x in own):
                out.append({**_f("component_missing", f"{key[0]}: no record of the pieces inside it"), "chapter": key[0]})
            _flatten(e.get("components") or [], trail + (key,), out)
        elif not e.get("kind") and not own:
            out.append({**_f("component_missing", f"{key[0]}: nothing is recorded about this piece"), "chapter": key[0]})
    return out


def findings(pdir, name=None):
    """Everything known about this export."""
    if not _is_merge(pdir) and _manifest_components(pdir, name) is None:
        return chapter_findings(pdir, name) if name else script_findings(pdir) + validator_findings(pdir)
    here = ((_name(pdir), name or ""),)
    out = list(media_findings(pdir, name))
    entries, extra = _pieces(pdir, name or "", here)
    return _flatten(entries, here, out + extra)


def verdict(pdir, name=None):
    fs = findings(pdir, name)
    crit = [x for x in fs if x["level"] == "critical"]
    unk = [x for x in fs if x["level"] == "unknown"]
    rev = [x for x in fs if x["level"] == "review"]
    return {"release": "blocked" if crit else "unknown" if unk else "review" if rev else "clear",
            "critical": crit, "unknown": unk, "review": rev}


def blocking(pdir, name=None):
    """Plain-language blockers for approval/posting under the current mode."""
    m = mode()
    if m == "off":
        return []
    v = verdict(pdir, name)
    items = v["critical"] + v["unknown"] + (v["review"] if m == "strict" else [])
    return [f"Release check ({x['code']}{', ' + x['chapter'] if x.get('chapter') else ''}): {x['detail']}"
            for x in items]
