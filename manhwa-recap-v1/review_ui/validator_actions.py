"""Turning a Check finding into a change on the real board.

A finding that only describes a problem leaves the whole fix to the owner:
read it, work out which control it maps to, find the row, apply the change.
That is most of the work, and it is exactly the work the checker exists to
remove. So every finding carries actions, and this module executes them.

THE ONE RULE HERE: every action goes through the SAME functions the board's own
controls use — `storyboard_edit.assign_panel`, `.reorder`, `.use_full_panel`,
the same `segments.json` writer, the same `review.json`. Nothing in this file
maintains a private copy of board state or a parallel notion of what a segment
is. That is what makes a Check action indistinguishable from the owner having
done it by hand on the board: it lands in the same undo history, it shows up in
the same edit log, and it can never drift out of sync with what the board
displays.

The undo snapshot is taken by the CALLER (the route in server.py) before
dispatch, for the same reason the board's own ops do it there: one snapshot per
user action, not one per internal step.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import validator


class ActionError(RuntimeError):
    """A user-visible reason the action could not be applied."""


# Actions that change the board and therefore invalidate the current report.
# Listed explicitly rather than inferred, so adding an action forces a decision
# about whether the findings still describe reality afterwards.
MUTATING = {"swap", "move_earlier", "move_later", "leave_out", "put_back",
            "redescribe", "reocr", "use_full_panel"}

# Actions that are a judgement about a finding rather than a change to the
# board. They feed the loop that stops a settled question being re-asked.
FEEDBACK = {"accept", "dismiss", "reopen", "mark_fixed"}

ALL_ACTIONS = MUTATING | FEEDBACK | {"send_to_review"}


# ------------------------------------------------------------- helpers
def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _need(params, *keys):
    out = []
    for k in keys:
        v = params.get(k)
        if v is None or v == "":
            raise ActionError(f"this action needs {k}")
        out.append(v)
    return out[0] if len(out) == 1 else out


def _descriptions(pdir):
    return os.path.join(pdir, "descriptions.json")


def _panel_path(pdir, descs, panel_id):
    """The crop on disk for a panel.

    descriptions.json stores a BARE FILENAME, not a path — a fact that has
    already broken one feature in this codebase by being assumed otherwise.
    Fall back to <panel_id>.png only when the record has no filename at all.
    """
    rec = next((d for d in descs if d.get("panel_id") == panel_id), None)
    fname = (rec or {}).get("file") or f"{panel_id}.png"
    return os.path.join(pdir, "crops", fname), rec


# --------------------------------------------------------- board actions
def _edit(pdir, fn, *a, **kw):
    """Run a storyboard_edit op, turning its vocabulary of failure into one the
    UI can show."""
    import storyboard_edit
    try:
        return fn(pdir, *a, **kw)
    except ValueError as e:
        raise ActionError(str(e)) from e


def act_swap(pdir, params):
    """Put segment `seg_index` onto the panel the checker named.

    move_here stays False deliberately: fixing WHICH PICTURE shows must not
    silently re-sequence the story. Moving the line is a separate, explicit
    action, because they are separate decisions.
    """
    import storyboard_edit
    si, pid = _need(params, "seg_index", "panel_id")
    descs = _load_json(_descriptions(pdir), [])
    if not any(d.get("panel_id") == pid for d in descs):
        raise ActionError(f"no panel {pid} in this project")
    _edit(pdir, storyboard_edit.assign_panel, int(si), pid, descs,
          move_here=False)
    return {"changed": "segments", "detail": f"segment {si} now plays over {pid}"}


def _move(pdir, si, delta):
    import storyboard_edit
    segs = storyboard_edit.load(pdir)
    try:
        pos = storyboard_edit._pos(segs, int(si))
    except ValueError as e:
        raise ActionError(str(e)) from e
    target = pos + delta
    if target < 0 or target > len(segs) - 1:
        raise ActionError("already at the " +
                          ("start" if delta < 0 else "end") + " of the timeline")
    _edit(pdir, storyboard_edit.reorder, int(si), target)
    return {"changed": "segments",
            "detail": f"segment {si} moved from slot {pos} to {target}"}


def act_move_earlier(pdir, params):
    return _move(pdir, _need(params, "seg_index"), -1)


def act_move_later(pdir, params):
    return _move(pdir, _need(params, "seg_index"), +1)


def _set_included(pdir, panel_id, included):
    """The final-video tick, set exactly as /api/storyboard/set_included does.

    Deliberately NOT storyboard_edit.exclude_panel: that removes the segment
    from the timeline, which is a bigger, harder-to-reverse change than the
    owner asked for when they clicked "leave this panel out". Unticking keeps
    the row, its narration and its audio, so it is one click back.
    """
    import storyboard_edit
    segs = storyboard_edit.load(pdir)
    hit = 0
    for s in segs:
        if s.get("panel_id") == panel_id:
            s["user_included"] = bool(included)
            hit += 1
    if not hit:
        raise ActionError(f"no segment on panel {panel_id}")
    storyboard_edit.save(pdir, segs)
    return {"changed": "segments", "detail":
            f"{hit} segment(s) on {panel_id} "
            + ("put back in" if included else "taken out of") + " the video"}


def act_leave_out(pdir, params):
    return _set_included(pdir, _need(params, "panel_id"), False)


def act_put_back(pdir, params):
    return _set_included(pdir, _need(params, "panel_id"), True)


def act_use_full_panel(pdir, params):
    import storyboard_edit
    si = _need(params, "seg_index")
    _edit(pdir, storyboard_edit.use_full_panel, int(si))
    return {"changed": "segments",
            "detail": f"segment {si} now renders the whole panel"}


def act_send_to_review(pdir, params):
    """Mark the segment as needing a human decision.

    'pending' rather than 'rejected' on purpose: the checker's job is to raise
    a question, and rejecting would answer it by taking the segment out of the
    video. That decision belongs to the owner.
    """
    si = int(_need(params, "seg_index"))
    path = os.path.join(pdir, "review.json")
    review = _load_json(path, {})
    review[str(si)] = {"status": "pending",
                       "note": (params.get("note") or
                                "Flagged by the Check pass.")}
    _write_json(path, review)
    return {"changed": "review", "detail": f"segment {si} sent to review"}


# ------------------------------------------------- description / OCR rerun
def _describe(pdir, panel_id):
    """Re-run the vision describer on ONE panel.

    Uses panel-describe's own single-panel entry point, which already routes
    through usage.gate for its Gemini call — so a re-describe is capped and
    costed exactly like the describe pass during ingest, and shows up in the
    same header.
    """
    descs = _load_json(_descriptions(pdir), [])
    path, rec = _panel_path(pdir, descs, panel_id)
    if rec is None:
        raise ActionError(f"no panel {panel_id} in this project")
    if not os.path.exists(path):
        raise ActionError(f"the crop for {panel_id} is not on disk")

    pd = os.path.join(ROOT, "panel-describe")
    if pd not in sys.path:
        sys.path.insert(0, pd)
    try:
        import describe as _describe_mod
    except ImportError as e:
        raise ActionError("the panel-describe module is not available "
                          "on this server") from e

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ActionError("GEMINI_API_KEY is not set, so the description "
                          "cannot be re-run. Set it in the Railway "
                          "environment, never in code.")
    try:
        fresh = _describe_mod.describe_panel(
            path, key, os.environ.get("DESCRIBE_MODEL", "gemini-3.5-flash"))
    except Exception as e:                      # cap breach, API error, …
        raise ActionError(f"re-describe failed: {e}") from e
    if not fresh.get("ok"):
        raise ActionError("the describer returned nothing usable for this panel")
    return descs, rec, fresh


def act_redescribe(pdir, params):
    panel_id = _need(params, "panel_id")
    descs, rec, fresh = _describe(pdir, panel_id)
    before = rec.get("visual_description", "")
    rec["ocr_text"] = fresh.get("ocr_text", "")
    rec["visual_description"] = fresh.get("visual_description", "")
    rec["source"] = fresh.get("source", rec.get("source"))
    rec["ok"] = bool(fresh.get("ok"))
    _write_json(_descriptions(pdir), descs)
    return {"changed": "descriptions", "before": before,
            "after": rec["visual_description"],
            "detail": f"description re-run for {panel_id}"}


def act_reocr(pdir, params):
    """Re-read only the OCR, leaving the description alone.

    The describer returns both in one call, so this costs the same as a full
    re-describe; what differs is what is KEPT. When only the OCR looks poisoned,
    overwriting a good description with a fresh guess would be a regression the
    owner did not ask for.
    """
    panel_id = _need(params, "panel_id")
    descs, rec, fresh = _describe(pdir, panel_id)
    before = rec.get("ocr_text", "")
    rec["ocr_text"] = fresh.get("ocr_text", "")
    _write_json(_descriptions(pdir), descs)
    return {"changed": "descriptions", "before": before,
            "after": rec["ocr_text"],
            "detail": f"OCR re-read for {panel_id}"}


# ------------------------------------------------------ feedback actions
def _feedback(pdir, params, verdict):
    fid = _need(params, "finding_id")
    validator.record_feedback(
        pdir, fid, verdict,
        panel_id=params.get("panel_id"), category=params.get("category"),
        note=params.get("note", ""))
    return {"changed": "feedback", "verdict": verdict,
            "detail": {
                "accepted": "this finding will come back demoted and marked, "
                            "not raised fresh",
                "dismissed": "recorded as a disagreement with the checker",
                "fixed": "recorded as fixed",
                "reopened": "this finding is live again",
            }[verdict]}


def act_accept(pdir, params):
    return _feedback(pdir, params, "accepted")


def act_dismiss(pdir, params):
    return _feedback(pdir, params, "dismissed")


def act_mark_fixed(pdir, params):
    return _feedback(pdir, params, "fixed")


def act_reopen(pdir, params):
    return _feedback(pdir, params, "reopened")


# ---------------------------------------------------------------- dispatch
_HANDLERS = {
    "swap": act_swap,
    "move_earlier": act_move_earlier,
    "move_later": act_move_later,
    "leave_out": act_leave_out,
    "put_back": act_put_back,
    "use_full_panel": act_use_full_panel,
    "send_to_review": act_send_to_review,
    "redescribe": act_redescribe,
    "reocr": act_reocr,
    "accept": act_accept,
    "dismiss": act_dismiss,
    "mark_fixed": act_mark_fixed,
    "reopen": act_reopen,
}


def apply_action(pdir, action, params=None):
    """Execute one finding action against the real project.

    Returns what changed, so the caller can tell the board to refresh the right
    thing and can show the owner what actually happened rather than a generic
    success.
    """
    params = params or {}
    fn = _HANDLERS.get(action)
    if fn is None:
        raise ActionError(f"unknown action '{action}'")
    result = fn(pdir, params)
    result["action"] = action
    result["mutating"] = action in MUTATING
    # A board change makes the current findings describe a board that no longer
    # exists. Say so rather than letting the drawer keep showing stale rows as
    # if they were current.
    result["report_stale"] = action in MUTATING
    return result
