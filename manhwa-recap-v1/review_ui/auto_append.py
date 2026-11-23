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

Owner, 2026-11-08, replaces "added to the prior videos": no overlapping ranges.
Each new merge starts at the chapter after the series' last posted video (posted
88-94 -> the next merge is 95+) and is built once `min_new` new chapters are
ready (AUTO_APPEND_MIN on Railway, default 7). A waiting merge that overlaps a
posted video (Death Knight 88-95, Mount Hua 180-186) is replaced by that re-cut.
The posted video still lends its title hook, tags and thumbnail art.

Owner brief, 2026-11-13, brings the 2026-11-04 rule back as the default: "each
new chapter extends the previous compilation" — posted 1-4 and a new chapter 5
give 1-5 (the posted video is the first piece, re-used as it is; nothing is
rendered or generated again). The two rules are both kept, chosen by `mode`
(AUTO_APPEND_MODE on Railway):
  cumulative  (default) old compilation + the new chapters, as soon as
              `min_new` are ready (AUTO_APPEND_MIN, default 1)
  fresh       the 2026-11-08 rule: start after the last posted video, wait for
              `min_new` (default 7)

This module is pure decision logic and text helpers over plain data; the
server gathers the inputs and does the building (server._auto_append_pass).
"""
import re

# Single-chapter review statuses a chapter can be appended from. "approved"
# means the owner chose to post that chapter on its own: leave it alone.
ELIGIBLE = ("review_pending",)


MODES = ("cumulative", "fresh")


def decide(posted, singles, pending, min_new=1, mode="fresh", skip=()):
    """What to build for one series, or why not.

    mode: "fresh" — the merge starts after the last posted video and "base"
          only lends its title / tags / thumbnail; "cumulative" — the merge is
          the last posted video plus the new chapters, and "base" is its first
          piece.

    posted:  [{"project", "name", "start", "end", "at"}] — videos of the series
             that are on YouTube (a single chapter has start == end).
    singles: {chapter: {"project", "name", "status", "superseded",
                         "merged_into", "has_video"}} — chapter projects.
    pending: [{"project", "start", "end", "building", "approved",
               "pieces": [{"project", "name"}]}] — merged videos of the
             series that are NOT posted.
    min_new: new chapters needed before a merge is built.
    skip:    chapters marked as having no source of their own (owner, 2026-11-23:
             Damn Reincarnation 85 and 91 repeat the chapter before); the run of
             new chapters steps over them instead of stopping there.

    Returns {"action": "build", "base", "start", "end", "projects", "retire"}
    or {"action": "none" | "wait", "why"}. "base" is the posted video whose
    title / tags / thumbnail the new merge borrows; it is not part of it.
    """
    if mode not in MODES:
        raise ValueError(f"unknown merge mode {mode!r}")
    ext = _extend_waiting(posted, singles, pending, min_new, skip, mode)
    if ext:
        return ext
    if not posted:
        return {"action": "none", "why": "nothing posted yet"}
    base = max(posted, key=lambda p: (int(p["end"]), float(p.get("at") or 0)))
    if mode == "cumulative":
        return _decide_cumulative(base, singles, pending, min_new, skip)
    b_end = int(base["end"])
    start = b_end + 1
    pend = {p["project"]: p for p in pending}

    run = _new_run(singles, pend, start, skip)
    if not run:
        return {"action": "none", "why": f"no new rendered chapter after {b_end}"}
    last = run[-1]

    if any(p.get("building") for p in pending):
        return {"action": "wait", "why": "a merged video of this series is still being built"}
    for p in pending:
        if int(p["start"]) == start and int(p["end"]) >= last:
            return {"action": "none", "why": f"{p['project']} already has chapters {start}-{last}"}
    stale = [p for p in pending if int(p["end"]) >= start and int(p["start"]) <= last]
    held = [p for p in stale if p.get("approved")]
    if held:
        h = held[0]
        why = f"{h['project']} is approved — waiting for it to post"
        if last > int(h["end"]):                  # owner 2026-11-08 (Stellar 136 after 129-135)
            nxt = f"ch {int(h['end']) + 1}" + (f"-{last}" if last > int(h["end"]) + 1 else "")
            why += (f"; {nxt} ready right after it — rebuild {int(h['start'])}-{last} before it uploads "
                    f"to include {'it' if last == int(h['end']) + 1 else 'them'}, otherwise the next merge starts there")
        return {"action": "wait", "why": why}
    if len(run) < min_new:
        return {"action": "wait", "why": f"{len(run)} new chapter(s) after {b_end}; the next merge starts at "
                                         f"{start} once {min_new} are ready"}
    return {"action": "build", "base": base, "start": start, "end": last,
            "projects": [singles[c]["project"] for c in run],
            "retire": [p["project"] for p in stale]}


def _new_run(singles, pend, first, skip=()):
    """Consecutive rendered chapters from `first` that may join a merge; a
    chapter in `skip` (no source of its own) is stepped over, never ended on."""
    run, n = [], first
    skip = set(skip or ())
    while n in singles or (n in skip and any(c > n for c in singles)):
        if n not in singles:
            n += 1
            continue
        s = singles[n]
        if not s.get("has_video") or s.get("superseded"):
            break
        if s.get("status") == "merged":
            if s.get("merged_into") not in pend:   # inside a posted video, or one that's gone
                break
        elif s.get("status") not in ELIGIBLE:
            break
        run.append(n)
        n += 1
    return run


def _extend_waiting(posted, singles, pending, min_new, skip=(), mode="cumulative"):
    """Owner, 2026-11-23: "If a chapter isn't merged find out why and make the
    fixes so you can merge them … why are we creating different merges for the
    same series?" New chapters rendered after a merge that has NOT posted yet
    (approved and queued, or still in Needs review) are added to it: one
    waiting video per series, a-b + new chapters -> a-n.

    The waiting merge is the head: the newest approved one, else the earliest
    unapproved one after the last posted chapter. An approved head is never
    replaced here — it stays queued as it is, and the longer video waits in
    Needs review; approving it puts it in the head's place on the schedule
    (server._schedule_on_verdict). Unapproved waiting merges inside the new
    range are retired once it is built. In "fresh" mode a waiting merge that
    overlaps a posted video is not extended (the fresh rule re-cuts it from the
    next chapter) and min_new counts the whole merge. Returns None when there
    is nothing to extend (the posted-video rules then apply)."""
    last_posted = max((int(p["end"]) for p in posted), default=0)
    first = (lambda p: int(p["end"])) if mode == "cumulative" else (lambda p: int(p["start"]))
    waiting = [p for p in pending if first(p) > last_posted and not p.get("building")]
    approved = [p for p in waiting if p.get("approved")]
    head = (max(approved, key=lambda p: (int(p["end"]), int(p["start"]))) if approved
            else min(waiting, key=lambda p: (int(p["start"]), int(p["end"])), default=None))
    if head is None:
        return None
    pend = {p["project"]: p for p in pending}
    run = _new_run(singles, pend, int(head["end"]) + 1, skip)
    if not run:
        return None
    a, last = int(head["start"]), run[-1]
    if any(p.get("building") for p in pending):
        return {"action": "wait", "why": "a merged video of this series is still being built"}
    for p in waiting:
        if p is not head and not p.get("approved") and int(p["start"]) == a and int(p["end"]) >= last:
            return {"action": "none", "why": f"{p['project']} already has chapters {a}-{last}"
                                             + (f" — approving it puts it in {head['project']}'s place"
                                                if head.get("approved") else "")}
    count = len(run) if mode == "cumulative" else len(run) + int(head["end"]) - a + 1
    if count < min_new:
        return {"action": "wait", "why": f"{len(run)} new chapter(s) after {head['end']}; they are added to "
                                         f"{a}-{head['end']} once {min_new} are ready"}
    run_projects = [singles[c]["project"] for c in run]
    pieces = [pc["project"] for pc in head.get("pieces") or [] if pc.get("project") not in run_projects]
    if not pieces:
        pieces = [head["project"]]
    held = {pc.get("project") for p in waiting if p.get("approved") for pc in p.get("pieces") or []}
    retire = [p["project"] for p in waiting
              if p is not head and not p.get("approved") and p["project"] not in held
              and int(p["start"]) >= a and int(p["end"]) <= last]
    if not head.get("approved"):
        retire.insert(0, head["project"])
    return {"action": "build", "base": head, "start": a, "end": last,
            "projects": list(dict.fromkeys(pieces + run_projects)),
            "retire": retire, "extends": [head["project"]] if head.get("approved") else []}


def _decide_cumulative(base, singles, pending, min_new, skip=()):
    """Posted a-b + new chapters b+1..n -> a-n, with the posted video as the
    first piece. A waiting merge that already holds the posted video is
    replaced by the longer one; an approved one is never replaced."""
    b_start, b_end = int(base["start"]), int(base["end"])
    pend = {p["project"]: p for p in pending}
    run = _new_run(singles, pend, b_end + 1, skip)
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
        h = held[0]
        why = f"{h['project']} is approved — waiting for it to post"
        if last > int(h["end"]):
            nxt = f"ch {int(h['end']) + 1}" + (f"-{last}" if last > int(h["end"]) + 1 else "")
            why += f"; {nxt} will be added to it once it has posted"
        return {"action": "wait", "why": why}
    # chapters not yet inside any waiting merge are the "new" ones
    fresh = [c for c in run if singles[c].get("status") != "merged"]
    if len(fresh) < min_new:
        return {"action": "wait", "why": f"{len(fresh)} new chapter(s) after {b_end}; they are added to "
                                         f"{b_start}-{b_end} once {min_new} are ready"}

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
