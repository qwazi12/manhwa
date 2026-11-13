"""Measured quality, corrections, cost and timeliness — from records the
studio already keeps. Read-only; nothing here calls a model.

What each number is, and is not (audit 2026-11-13):

  script.*        from critique.json — a MODEL's review of the first draft
                  against the panel facts. It counts what the editor flagged;
                  it is not a human-verified error rate.
  media.*         from exports/_qa/*.qa.json — signal checks on the finished
                  file (length, clipping, pauses, voice support). They do not
                  verify the spoken words.
  timeline.*      from validation.json — rule findings on image/narration
                  placement; precision unmeasured.
  corrections.*   from edits.log.jsonl and reviews.json. edits.log.jsonl is
                  deleted when a stage is re-run, and approvals record no
                  reviewer, so these are LOWER BOUNDS, said as such.
  gate.*          what release_gate.py would decide for each video
                  (reported whether or not RELEASE_GATE is on).
  timeliness.*    hours from a chapter's source release date (day precision)
                  and from the studio first seeing it, to the video going live.

Units: chapters (single-chapter folders) for script/timeline/corrections;
exports for media and gate; published videos for timeliness.
"""
import json
import os
import statistics
import time
from datetime import datetime, timezone

import release_gate


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def _is_merge(meta):
    return (meta or {}).get("kind") == "range"


def quality(root):
    script = {"chapters": 0, "issues": 0, "by_type": {}, "chapters_flagged": 0, "rewritten": 0,
              "rechecked": 0, "outcome": {}}
    timeline = {"chapters": 0, "high": 0, "chapters_with_high": 0}
    media = {"exports": 0, "passed": 0, "failed": 0, "other": 0, "failed_rules": {}}
    corr = {"chapters": 0, "chapters_with_edit_log": 0, "edits": 0, "by_op": {},
            "exports_reviewed": 0, "sent_back": 0, "approved_first_time": 0, "reviewer_recorded": 0}
    gate = {"exports": 0, "clear": 0, "review": 0, "blocked": 0, "by_code": {}}
    rows = []
    for pid in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        pdir = os.path.join(root, pid)
        if pid.startswith("_") or not os.path.isdir(pdir):
            continue
        meta = _read(os.path.join(pdir, "project.json")) or {}
        merge = _is_merge(meta)
        row = {"id": pid, "kind": "merge" if merge else "chapter"}
        if not merge:
            corr["chapters"] += 1
            c = _read(os.path.join(pdir, "critique.json"))
            if c is not None:
                script["chapters"] += 1
                issues = c.get("issues") or []
                script["issues"] += len(issues)
                script["chapters_flagged"] += bool(issues)
                for i in issues:
                    t = i.get("type") or "?"
                    script["by_type"][t] = script["by_type"].get(t, 0) + 1
                script["rewritten"] += len(c.get("revised") or [])
                script["rechecked"] += len(((c.get("verified") or {}).get("units")) or [])
                o = c.get("outcome") or "not recorded"
                script["outcome"][o] = script["outcome"].get(o, 0) + 1
                row["script_issues"], row["script_outcome"] = len(issues), o
            v = _read(os.path.join(pdir, "validation.json"))
            if v is not None:
                timeline["chapters"] += 1
                high = sum(1 for f in v.get("findings") or [] if f.get("severity") == "high")
                timeline["high"] += high
                timeline["chapters_with_high"] += bool(high)
                row["timeline_high"] = high
            log = os.path.join(pdir, "edits.log.jsonl")
            if os.path.exists(log):
                corr["chapters_with_edit_log"] += 1
                n = 0
                with open(log, encoding="utf-8") as f:
                    for ln in f:
                        try:
                            op = json.loads(ln).get("op") or "?"
                        except ValueError:
                            continue
                        n += 1
                        corr["by_op"][op] = corr["by_op"].get(op, 0) + 1
                corr["edits"] += n
                row["edits"] = n
        for name, r in (_read(os.path.join(pdir, "reviews.json")) or {}).items():
            if not isinstance(r, dict) or r.get("status") == "merged":
                continue
            corr["exports_reviewed"] += 1
            back = sum(1 for h in r.get("history") or [] if h.get("status") == "sent_back")
            corr["sent_back"] += bool(back)
            corr["approved_first_time"] += (r.get("status") == "approved" and not back)
            corr["reviewer_recorded"] += bool(r.get("reviewed_by"))
        qdir = os.path.join(pdir, "exports", "_qa")
        for fn in sorted(os.listdir(qdir)) if os.path.isdir(qdir) else []:
            if not fn.endswith(".qa.json"):
                continue
            q = _read(os.path.join(qdir, fn)) or {}
            media["exports"] += 1
            st = q.get("status")
            media["passed" if st == "passed" else "failed" if st == "failed" else "other"] += 1
            if st == "failed":
                for f in q.get("findings") or []:
                    k = f.get("rule") or "?"
                    media["failed_rules"][k] = media["failed_rules"].get(k, 0) + 1
            name = fn[:-len(".qa.json")]
            ver = release_gate.verdict(pdir, name)
            gate["exports"] += 1
            gate[ver["release"]] += 1
            for f in ver["critical"] + ver["review"]:
                gate["by_code"][f["code"]] = gate["by_code"].get(f["code"], 0) + 1
            if ver["release"] != "clear":
                row.setdefault("gate", []).append({"export": name, "release": ver["release"],
                                                   "codes": sorted({f["code"] for f in ver["critical"] + ver["review"]})})
        if len(row) > 2:
            rows.append(row)
    script["issues_per_chapter"] = round(script["issues"] / script["chapters"], 2) if script["chapters"] else None
    script["chapters_flagged_pct"] = _pct(script["chapters_flagged"], script["chapters"])
    timeline["high_per_chapter"] = round(timeline["high"] / timeline["chapters"], 2) if timeline["chapters"] else None
    media["pass_pct"] = _pct(media["passed"], media["exports"])
    corr["approved_first_time_pct"] = _pct(corr["approved_first_time"], corr["exports_reviewed"])
    corr["caveat"] = ("lower bounds: the edit log is deleted when a stage is re-run "
                      f"({corr['chapters_with_edit_log']} of {corr['chapters']} chapters still have one), "
                      "review time is not recorded, and approvals carry no reviewer")
    gate["mode"] = release_gate.mode()
    gate["would_hold_pct"] = _pct(gate["blocked"], gate["exports"])
    return {"script": script, "timeline": timeline, "media": media, "corrections": corr, "gate": gate,
            "chapters": rows}


def _chapter_dates(series, chapter):
    """(release_day 'YYYY-MM-DD' or None, approx bool, first_seen epoch or None)
    for one chapter, the earliest any mirror of the series reports."""
    day, approx, seen = None, False, None
    ch = str(chapter)
    for m in (series or {}).get("mirrors") or []:
        rd = (m.get("release_dates") or {}).get(ch)
        if rd:
            d, a = (rd[0], bool(rd[1])) if isinstance(rd, (list, tuple)) else (rd, False)
            if d and (day is None or d < day):
                day, approx = d, a
        fs = (m.get("first_seen") or {}).get(ch)
        if fs and (seen is None or fs < seen):
            seen = fs
    return day, approx, seen


def timeliness(posts, find_series):
    """posts: [{"project", "label", "url", "chapter_end", "live_at"}].
    find_series(url) -> watchlist series dict or None."""
    rows, unmeasured = [], {}
    for p in posts:
        s = find_series(p.get("url") or "") if p.get("url") else None
        if not s:
            unmeasured["no series on the watchlist for its link"] = unmeasured.get("no series on the watchlist for its link", 0) + 1
            continue
        day, approx, seen = _chapter_dates(s, p.get("chapter_end"))
        if not day and not seen:
            unmeasured["no release date and no first-seen time for its newest chapter"] = \
                unmeasured.get("no release date and no first-seen time for its newest chapter", 0) + 1
            continue
        row = {"project": p["project"], "label": p.get("label"), "live_at": p["live_at"]}
        if day:
            rel = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            row.update(release_day=day, release_approx=approx,
                       hours_from_release=round((p["live_at"] - rel) / 3600.0, 1))
        if seen:
            row.update(first_seen=seen, hours_from_detection=round((p["live_at"] - seen) / 3600.0, 1))
        rows.append(row)

    def summ(key):
        v = sorted(r[key] for r in rows if key in r)
        return {"videos": len(v), "median_hours": round(statistics.median(v), 1) if v else None,
                "within_24h": sum(x <= 24 for x in v), "within_48h": sum(x <= 48 for x in v),
                "within_24h_pct": _pct(sum(x <= 24 for x in v), len(v)),
                "within_48h_pct": _pct(sum(x <= 48 for x in v), len(v))}
    return {"published": len(posts), "from_release": summ("hours_from_release"),
            "from_detection": summ("hours_from_detection"),
            "unmeasured": unmeasured,
            "note": "release dates have day precision (counted from 00:00 UTC of that day); "
                    "'first seen' is when the studio first read the chapter on a source, and is "
                    "missing for chapters already there when the series was added",
            "videos": sorted(rows, key=lambda r: -r["live_at"])[:60]}


def cost(ledger, index, month):
    """ledger: autopilot ledger entries; index: spend_index state."""
    per = sorted(float(e["cost"]) for e in ledger if e.get("cost"))
    recent = [float(e["cost"]) for e in sorted((e for e in ledger if e.get("cost")),
                                                key=lambda e: e.get("updated_at") or 0)[-20:]]
    by_stage = {k[1]: round(v, 3) for k, v in index.get("by_stage", {}).items() if k[0] == month}
    labelled = sum(v for k, v in by_stage.items() if k != "unlabelled")
    total = sum(by_stage.values())
    return {"chapters_with_cost": len(per),
            "per_chapter_median": round(statistics.median(per), 3) if per else None,
            "per_chapter_recent20_median": round(statistics.median(recent), 3) if recent else None,
            "under_1_dollar_pct": _pct(sum(x < 1.0 for x in per), len(per)),
            "under_50_cents_pct": _pct(sum(x < 0.5 for x in per), len(per)),
            "month": month, "month_by_stage": by_stage,
            "month_labelled_pct": _pct(labelled, total),
            "note": "per-chapter cost is the AI spend of the chapter's own job (failed attempts included); "
                    "packaging is labelled separately since 2026-11-13; reused chapters in a merged video "
                    "cost nothing and are not counted as new output"}


def build(root, posts, find_series, ledger, index, month):
    return {"generated_at": time.time(), "quality": quality(root),
            "timeliness": timeliness(posts, find_series), "cost": cost(ledger, index, month)}
