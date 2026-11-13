"""One verdict per video from the checks the studio already runs.

Why (audit, 2026-11-13): the studio wrote a script critique, validator
findings and a final-media QA report for every chapter, and nothing read them
when a video was approved or posted. On production, of 59 chapters with a
critique 30 were marked "needs_review"; of 96 exports 30 had FAILED final-media
QA; neither stopped or even flagged an approval.

Two levels:
  critical — must not be released (blocks when RELEASE_GATE is "critical" or
             "strict"):
      script_unresolved   an invented fact, wrong speaker or wrong order the
                          editor found and the script still has (not repaired,
                          or still flagged after its rewrite)
      script_unchecked    the script editor did not run or its answer was
                          unusable — nothing vouches for the script
      media_failed        final-media QA failed on this exact export
  review   — a person should look (blocks only when RELEASE_GATE is "strict"):
      script_unverified   problems were rewritten but the rewrite was never
                          re-checked (records from before 2026-11-13)
      script_minor        open style / redundancy / flat-dialogue notes
      validator_high      high-severity timeline findings
      media_missing       no final-media QA report for this export

RELEASE_GATE (Railway variable):
  off       (default) nothing is blocked; the verdict is only reported
            (/api/scorecard) — so the effect can be seen before it is enforced
  critical  critical items block approval and posting
  strict    review items block as well

Undo: set RELEASE_GATE=off (or remove it). No data is changed by the gate.
"""
import json
import os

CRITICAL_TYPES = {"hallucination", "misattributed_dialogue", "misorder", "missed_beat", "spoiled_reveal"}
OPEN = {"not_repaired", "still_flagged_after_rewrite", "direct_speech_contract_failed"}


def mode():
    m = (os.environ.get("RELEASE_GATE") or "off").strip().lower()
    return m if m in ("off", "critical", "strict") else "off"


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def script_findings(pdir):
    """Findings from critique.json of ONE chapter folder."""
    c = _read(os.path.join(pdir, "critique.json"))
    if c is None:
        return []                       # made before the editor existed: nothing to say
    out = []
    if c.get("status") != "done" or c.get("outcome") == "incomplete":
        out.append({"code": "script_unchecked", "level": "critical",
                    "detail": f"the script editor did not finish ({str(c.get('status'))[:80]})"})
        return out
    unresolved = c.get("unresolved")
    if unresolved is None:
        # oldest records: issues + revised only
        revised = {r.get("unit") for r in c.get("revised") or []}
        unresolved = [{**i, "resolution": "rewritten_unverified" if i.get("unit") in revised else "not_repaired"}
                      for i in c.get("issues") or []]
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
    v = _read(os.path.join(pdir, "validation.json")) or {}
    high = [f for f in v.get("findings") or [] if f.get("severity") == "high"]
    if not high:
        return []
    return [{"code": "validator_high", "level": "review", "count": len(high),
             "detail": "; ".join(str(f.get("issue"))[:90] for f in high[:2])}]


def media_findings(pdir, name):
    if not name:
        return []
    q = _read(os.path.join(pdir, "exports", "_qa", name + ".qa.json"))
    if q is None:
        return [{"code": "media_missing", "level": "review", "detail": "no final-media QA report for this export"}]
    if q.get("status") == "failed":
        crit = [f for f in q.get("findings") or [] if f.get("severity") in ("critical", "high", "error")]
        return [{"code": "media_failed", "level": "critical", "count": len(crit) or None,
                 "detail": "; ".join(f"{f.get('rule')}: {str(f.get('explanation'))[:90]}" for f in (crit or q.get("findings") or [])[:3])
                           or "final-media QA failed"}]
    return []


def components(pdir, name):
    """Chapter folders a merged video was built from (from its QA manifest);
    [] for a single chapter."""
    m = _read(os.path.join(pdir, "exports", "_qa", (name or "") + ".manifest.json")) or {}
    ctx = m.get("context") or {}
    out = []
    for c in ctx.get("components") or []:
        p = c.get("project")
        if p and not os.path.isabs(p):
            p = os.path.join(os.path.dirname(pdir.rstrip("/")), p)
        if p and os.path.isdir(p):
            out.append(p)
    return out


def findings(pdir, name=None):
    """Everything known about this video: its own media report, plus the
    script and timeline findings of the chapter (or, for a merged video, of
    each chapter in it that still has its folder)."""
    out = list(media_findings(pdir, name))
    parts = components(pdir, name) or [pdir]
    for part in parts:
        tag = "" if part == pdir else os.path.basename(part.rstrip("/"))
        for f in script_findings(part) + validator_findings(part):
            out.append({**f, "chapter": tag} if tag else f)
    return out


def verdict(pdir, name=None):
    fs = findings(pdir, name)
    crit = [f for f in fs if f["level"] == "critical"]
    rev = [f for f in fs if f["level"] == "review"]
    return {"release": "blocked" if crit else ("review" if rev else "clear"),
            "critical": crit, "review": rev}


def blocking(pdir, name=None):
    """Plain-language blockers for approval/posting under the current mode."""
    m = mode()
    if m == "off":
        return []
    v = verdict(pdir, name)
    items = v["critical"] + (v["review"] if m == "strict" else [])
    return [f"Release check ({f['code']}{', ' + f['chapter'] if f.get('chapter') else ''}): {f['detail']}"
            for f in items]
