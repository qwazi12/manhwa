"""Append new chapters onto the series' last posted video.

Owner, 2026-11-04: "going forward new chapters that are made need to be added
to the prior videos after they are rendered and processed so let's say we
previously did chapter 20-30 and posted that, and now a new chapters 31 & 32
I want 31 & 32 added to 20-30 so we have 20-32 in the needs review with the
prior seo and everything applied".

Owner's choices (same day):
  * when: after the owner approved the chapter's board for render AND the
    render finished (renders only start from the owner's Render / render
    queue, so a finished chapter export is the trigger);
  * a merge already waiting in Needs review (20-31) when 32 finishes is
    REPLACED by 20-32 (the old one is moved aside, never deleted);
  * a merge the owner already approved / queued is never replaced: the
    series waits until it posts.

This module is pure decision logic and text helpers over plain data; the
server gathers the inputs and does the building (server._auto_append_pass).
"""
import re

# Single-chapter review statuses a chapter can be appended from. "approved"
# means the owner chose to post that chapter on its own: leave it alone.
ELIGIBLE = ("review_pending",)


def decide(posted, singles, pending):
    """What to build for one series, or why not.

    posted:  [{"project", "name", "start", "end", "at"}] — videos of the series
             that are on YouTube (a single chapter has start == end).
    singles: {chapter: {"project", "name", "status", "superseded",
                         "merged_into", "has_video"}} — chapter projects.
    pending: [{"project", "start", "end", "building", "approved",
               "pieces": [{"project", "name"}]}] — merged videos of the
             series that are NOT posted.

    Returns {"action": "build", "base", "start", "end", "projects", "retire"}
    or {"action": "none" | "wait", "why"}.
    """
    if not posted:
        return {"action": "none", "why": "nothing posted yet"}
    base = max(posted, key=lambda p: (int(p["end"]), float(p.get("at") or 0)))
    b_start, b_end = int(base["start"]), int(base["end"])
    pend = {p["project"]: p for p in pending}

    run, n = [], b_end + 1
    while n in singles:
        s = singles[n]
        if not s.get("has_video") or s.get("superseded"):
            break
        mi = s.get("merged_into")
        if s.get("status") == "merged":
            if mi not in pend:            # inside a posted video, or one that's gone
                break
        elif s.get("status") not in ELIGIBLE:
            break
        run.append(n)
        n += 1
    if not run:
        return {"action": "none", "why": f"no new rendered chapter after {b_end}"}
    last = run[-1]

    if any(p.get("building") for p in pending):
        return {"action": "wait", "why": "a merged video of this series is still being built"}
    for p in pending:
        if int(p["start"]) <= b_start and int(p["end"]) >= last:
            return {"action": "none", "why": f"{p['project']} already has chapters up to {last}"}
    stale = [p for p in pending if int(p["end"]) >= b_end + 1 and int(p["start"]) <= last]
    held = [p for p in stale if p.get("approved")]
    if held:
        return {"action": "wait", "why": f"{held[0]['project']} is approved — waiting for it to post"}

    run_projects = [singles[c]["project"] for c in run]
    # the waiting merge may already reach back further than the posted video
    # (Murim 47-49 on posted 48): keep its earlier pieces, add the new chapters
    reach = min(stale, key=lambda p: int(p["start"])) if stale else None
    if reach and int(reach["start"]) <= b_start:
        pieces = [pc["project"] for pc in reach.get("pieces") or [] if pc["project"] not in run_projects]
        start = int(reach["start"])
    else:
        pieces, start = [base["project"]], b_start
    return {"action": "build", "base": base, "start": start, "end": last,
            "projects": list(dict.fromkeys(pieces + run_projects)),
            "retire": [p["project"] for p in stale]}


# ---------------------------------------------------------------- SEO text

_LEAD = re.compile(r"^\s*\[\s*\d+(?:\s*-\s*\d+)?\s*\]\s*")


def relabel_title(title, a, b, limit=100):
    """The posted title with its [x] / [a-b] swapped for this range."""
    t = (title or "").strip()
    lab = f"[{a}-{b}]" if a != b else f"[{a}]"
    t = (_LEAD.sub(lab + " ", t, count=1) if _LEAD.match(t) else f"{lab} {t}").strip()
    return t[:limit]


def merge_tags(current, posted, a, b, posted_label="", limit=480):
    """This video's tags, then the posted video's (its chapter label, e.g.
    "92-93", moved to this range), deduplicated, inside YouTube's
    500-character budget. A tag naming a chapter outside the range, or a
    range other than this one, is dropped."""
    out, seen, total = [], set(), 0
    nums = set(range(int(a), int(b) + 1))
    lab = f"{a}-{b}"
    if posted_label:
        posted = [re.sub(rf"(?<![\d-]){re.escape(posted_label)}(?![\d-])", lab, t or "") for t in posted or []]
        if "-" in posted_label:
            pa, pb = posted_label.split("-", 1)
            posted = [re.sub(rf"\b{pa} to {pb}\b", f"{a} to {b}", t) for t in posted]
    for t in list(current or []) + list(posted or []):
        t = (t or "").strip()
        k = t.lower()
        if not k or k in seen:
            continue
        found = [int(x) for x in re.findall(r"\b(\d{1,4})\b", k)]
        other_range = [r for r in re.findall(r"\b\d{1,4}-\d{1,4}\b", k) if r != lab]
        if other_range or (found and lab not in k and not any(x in nums for x in found)):
            continue
        if total + len(t) + 1 > limit:
            break
        seen.add(k)
        out.append(t)
        total += len(t) + 1
    return out


def _clock(s):
    s = int(s)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def with_timestamps(desc, marks):
    """The posted style ("⏱️ Timestamps:" + "• 0:00 - Chapter N"), above the
    Ko-fi line; any older Timestamps block is replaced, never doubled."""
    d = re.sub(r"\n*(?:⏱️ )?Timestamps:\n(?:[^\n]+(?:\n|\Z))*", "\n", desc or "")
    d = re.sub(r"\n{3,}", "\n\n", d).strip()
    if not marks:
        return d
    block = "⏱️ Timestamps:\n" + "\n".join(f"• {_clock(t)} - Chapter {n}" for n, t in marks)
    i = d.find("\n\n❤️")
    return (d[:i] + "\n\n" + block + d[i:]) if i >= 0 else (d + "\n\n" + block).strip()
