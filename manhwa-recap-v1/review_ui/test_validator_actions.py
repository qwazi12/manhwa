"""Check findings acting on the REAL board (validator_actions.py).

The point of these is narrow and important: a Check action must change the
actual project, through the same code the board's own controls use — not a
checker-private copy, and not a re-implementation that can drift from it.

So every assertion here reads the change back off DISK (segments.json,
descriptions.json, review.json) after the action, rather than trusting the
return value. A handler that returned {"ok": true} without writing anything
would pass a test that only checked its reply.

No network: the one action that would call an external API (re-describe) is
driven through a stub module.
"""
import json
import os
import sys
import types
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def project(root):
    """A small but REAL project: two narration units over four panels, the
    shapes storyboard_edit actually requires."""
    os.makedirs(os.path.join(root, "crops"), exist_ok=True)
    descs = [{"panel_id": f"p_{i:03d}", "file": f"p_{i:03d}.png",
              "width": 400, "height": 900, "bbox": [0, 0, 400, 900],
              "ocr_text": f"line {i}", "ok": True, "source": "gemini",
              "visual_description": f"Panel {i} shows a swordsman."}
             for i in range(1, 5)]
    scenes = [{"scene_id": 0, "text": "The duel began.",
               "panel_ids": ["p_001", "p_002"]},
              {"scene_id": 1, "text": "Steel met steel.",
               "panel_ids": ["p_003", "p_004"]}]
    segs = []
    for i in range(4):
        st = i * 5.0
        segs.append({
            "seg_index": i, "panel_id": f"p_{i + 1:03d}",
            "start": st, "end": st + 5.0, "dur": 5.0,
            "user_included": True, "clip": f"clips/seg_{i:03d}.mp4",
            "width": 400, "height": 900,
            "crop_bbox_norm": [0.0, 0.1, 1.0, 0.9],
            "beats": [{"index": i, "text": f"beat {i}",
                       "start": st, "end": st + 4.5}],
        })
    for name, data in (("descriptions.json", descs), ("script.json", scenes),
                       ("segments.json", segs)):
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    from PIL import Image
    for d in descs:
        Image.new("RGB", (400, 900), (20, 20, 30)).save(
            os.path.join(root, "crops", d["file"]))
    return root


def segs_of(pdir):
    with open(os.path.join(pdir, "segments.json"), encoding="utf-8") as f:
        return json.load(f)


def descs_of(pdir):
    with open(os.path.join(pdir, "descriptions.json"), encoding="utf-8") as f:
        return json.load(f)


def seg(pdir, si):
    return next(s for s in segs_of(pdir) if s["seg_index"] == si)


def main():
    tmp = tempfile.mkdtemp(prefix="vactions_")
    os.environ["USAGE_DIR"] = tmp
    import usage
    usage.USAGE_DIR = tmp
    usage.COUNTS_PATH = os.path.join(tmp, "counters.json")
    usage.LOG_PATH = os.path.join(tmp, "calls.log.jsonl")
    usage.LOCK_PATH = os.path.join(tmp, ".lock")

    import validator
    import validator_actions as VA

    # ============================================ findings expose actions
    pdir = project(os.path.join(tmp, "proj"))
    rows = validator.build_rows(pdir)
    rows_by_n = {r["n"]: r for r in rows}

    placement = validator._finding(
        rows_by_n[1], "Script placement", "high", "wrong panel", "swap it",
        "claude-sequence", category="placement", target=3)
    placement["actions"] = validator._actions_for(placement, rows_by_n)
    ids = [a["id"] for a in placement["actions"]]
    check("a placement finding offers a swap to the row it named",
          "swap" in ids)
    check("...and both directions of move", "move_earlier" in ids
          and "move_later" in ids)
    check("...and a way to leave the panel out", "leave_out" in ids)
    check("...and a way to jump to the row", "goto" in ids)
    check("...and a way to open the source image", "jump_to_source" in ids)
    check("...and a way to accept it as intentional", "accept" in ids)
    swap = next(a for a in placement["actions"] if a["id"] == "swap")
    check("the swap action carries the real panel it will swap to",
          swap["params"]["panel_id"] == rows_by_n[3]["panel_id"])
    check("...and the segment it will move", swap["params"]["seg_index"] == 0)

    desc_f = validator._finding(
        rows_by_n[2], "System description", "high", "wrong description",
        "re-run", "claude-description", category="description")
    desc_f["actions"] = validator._actions_for(desc_f, rows_by_n)
    dids = [a["id"] for a in desc_f["actions"]]
    check("a description finding offers a re-describe", "redescribe" in dids)
    check("...and an OCR-only re-read", "reocr" in dids)
    check("a description finding does NOT offer a swap", "swap" not in dids)

    pacing = validator._finding(
        rows_by_n[2], "On-screen timing & motion", "medium", "flash",
        "drop it", "rules", category="pacing")
    pacing["actions"] = validator._actions_for(pacing, rows_by_n)
    check("a pacing finding offers 'use full panel'",
          "use_full_panel" in [a["id"] for a in pacing["actions"]])

    cov = validator._finding(
        rows_by_n[4], "Script placement", "high", "no panel in video",
        "tick one", "rules", category="coverage")
    cov["actions"] = validator._actions_for(cov, rows_by_n)
    check("a coverage finding offers putting the panel back",
          "put_back" in [a["id"] for a in cov["actions"]])

    # ============================================ swap changes the board
    before = seg(pdir, 0)["panel_id"]
    out = VA.apply_action(pdir, "swap",
                          {"seg_index": 0, "panel_id": "p_003"})
    after = seg(pdir, 0)["panel_id"]
    check("swap changes the segment's panel ON DISK",
          before == "p_001" and after == "p_003")
    check("...and reports the change as mutating", out["mutating"])
    check("...and flags the report as stale, since the board moved",
          out["report_stale"])
    check("...marking it as a manual placement so re-matching cannot undo it",
          seg(pdir, 0).get("user_assigned"))
    # Swapping the picture must NOT re-sequence the story.
    check("swap does not move the segment in the timeline",
          [s["seg_index"] for s in sorted(segs_of(pdir),
                                          key=lambda x: x["start"])][0] == 0)
    VA.apply_action(pdir, "swap", {"seg_index": 0, "panel_id": "p_001"})

    try:
        VA.apply_action(pdir, "swap", {"seg_index": 0, "panel_id": "nope"})
        check("swapping to a panel that does not exist is refused", False)
    except VA.ActionError:
        check("swapping to a panel that does not exist is refused", True)

    # ============================================ move earlier / later
    order0 = [s["seg_index"] for s in sorted(segs_of(pdir),
                                             key=lambda x: x["start"])]
    VA.apply_action(pdir, "move_later", {"seg_index": 0})
    order1 = [s["seg_index"] for s in sorted(segs_of(pdir),
                                             key=lambda x: x["start"])]
    check("move later actually reorders the timeline on disk",
          order1 != order0 and order1.index(0) == 1)
    VA.apply_action(pdir, "move_earlier", {"seg_index": 0})
    order2 = [s["seg_index"] for s in sorted(segs_of(pdir),
                                             key=lambda x: x["start"])]
    check("move earlier puts it back", order2 == order0)
    try:
        VA.apply_action(pdir, "move_earlier", {"seg_index": order0[0]})
        check("moving past the start of the timeline is refused", False)
    except VA.ActionError as e:
        check("moving past the start of the timeline is refused",
              "start" in str(e))

    # ============================================ leave out / put back
    VA.apply_action(pdir, "leave_out", {"panel_id": "p_002"})
    check("leave out unticks every segment on that panel",
          all(not s["user_included"] for s in segs_of(pdir)
              if s["panel_id"] == "p_002"))
    check("...without deleting the segment, so it is one click back",
          any(s["panel_id"] == "p_002" for s in segs_of(pdir)))
    VA.apply_action(pdir, "put_back", {"panel_id": "p_002"})
    check("put back re-ticks it",
          all(s["user_included"] for s in segs_of(pdir)
              if s["panel_id"] == "p_002"))
    try:
        VA.apply_action(pdir, "leave_out", {"panel_id": "p_999"})
        check("leaving out a panel with no segment is refused", False)
    except VA.ActionError:
        check("leaving out a panel with no segment is refused", True)

    # ============================================ use full panel
    VA.apply_action(pdir, "use_full_panel", {"seg_index": 1})
    box = seg(pdir, 1).get("crop_bbox_norm")
    check("use full panel widens the crop to the whole panel on disk",
          box == [0.0, 0.0, 1.0, 1.0])

    # ============================================ send to review
    VA.apply_action(pdir, "send_to_review", {"seg_index": 2})
    with open(os.path.join(pdir, "review.json"), encoding="utf-8") as f:
        rv = json.load(f)
    check("send to review records the segment in review.json",
          rv.get("2", {}).get("status") == "pending")
    check("...as PENDING, so it raises a question without answering it",
          rv["2"]["status"] != "rejected")
    check("...and says where the flag came from", "Check" in rv["2"]["note"])

    # ============================================ re-describe / re-OCR
    # The real describer calls Gemini; stub the module it imports so the action
    # is tested without the network.
    fake = types.ModuleType("describe")

    def _fake_describe_panel(path, key, model):
        return {"panel_id": os.path.basename(path).split(".")[0],
                "ocr_text": "FRESH OCR", "visual_description": "A fresh look.",
                "source": "gemini", "ok": True}

    fake.describe_panel = _fake_describe_panel
    sys.modules["describe"] = fake
    os.environ["GEMINI_API_KEY"] = "test-key-not-real"

    old_desc = next(d for d in descs_of(pdir) if d["panel_id"] == "p_001")
    out = VA.apply_action(pdir, "redescribe", {"panel_id": "p_001"})
    new_desc = next(d for d in descs_of(pdir) if d["panel_id"] == "p_001")
    check("re-describe rewrites the description on disk",
          new_desc["visual_description"] == "A fresh look."
          and old_desc["visual_description"] != new_desc["visual_description"])
    check("...and the OCR with it", new_desc["ocr_text"] == "FRESH OCR")
    check("...reporting before and after so the change is reviewable",
          out["before"] != out["after"])

    # re-OCR must NOT clobber a description the owner may be happy with
    d2 = next(d for d in descs_of(pdir) if d["panel_id"] == "p_002")
    keep = d2["visual_description"]
    VA.apply_action(pdir, "reocr", {"panel_id": "p_002"})
    d2b = next(d for d in descs_of(pdir) if d["panel_id"] == "p_002")
    check("re-OCR updates the OCR text", d2b["ocr_text"] == "FRESH OCR")
    check("...and leaves the description alone",
          d2b["visual_description"] == keep)

    os.environ.pop("GEMINI_API_KEY")
    try:
        VA.apply_action(pdir, "redescribe", {"panel_id": "p_001"})
        check("re-describe without a key fails by name", False)
    except VA.ActionError as e:
        check("re-describe without a key fails by name",
              "GEMINI_API_KEY" in str(e))
    os.environ["GEMINI_API_KEY"] = "test-key-not-real"

    try:
        VA.apply_action(pdir, "redescribe", {"panel_id": "ghost"})
        check("re-describing a panel that does not exist is refused", False)
    except VA.ActionError:
        check("re-describing a panel that does not exist is refused", True)

    # ============================================ feedback actions
    out = VA.apply_action(pdir, "accept",
                          {"finding_id": "abc123", "panel_id": "p_001",
                           "category": "placement"})
    fb = validator.load_feedback(pdir)
    check("accept records the judgement against the project",
          fb.get("abc123", {}).get("verdict") == "accepted")
    check("...and is NOT treated as a board change",
          not out["mutating"] and not out["report_stale"])
    VA.apply_action(pdir, "dismiss", {"finding_id": "abc123"})
    check("dismiss is recorded distinctly from accept",
          validator.load_feedback(pdir)["abc123"]["verdict"] == "dismissed")
    VA.apply_action(pdir, "reopen", {"finding_id": "abc123"})
    check("reopen clears the judgement",
          "abc123" not in validator.load_feedback(pdir))

    # ============================================ guardrails
    try:
        VA.apply_action(pdir, "not_a_real_action", {})
        check("an unknown action is refused", False)
    except VA.ActionError as e:
        check("an unknown action is refused", "unknown action" in str(e))
    try:
        VA.apply_action(pdir, "swap", {"seg_index": 0})
        check("an action missing a required parameter says which", False)
    except VA.ActionError as e:
        check("an action missing a required parameter says which",
              "panel_id" in str(e))

    check("every advertised action has a handler",
          all(a in VA._HANDLERS for a in VA.ALL_ACTIONS))
    check("board-changing actions are marked as such",
          "swap" in VA.MUTATING and "accept" not in VA.MUTATING)

    # Every action a finding can offer must actually be dispatchable — an
    # action in the UI with no handler is a dead button.
    offered = set()
    for r in rows:
        for cat in validator.CATEGORIES:
            f = validator._finding(r, "x", "low", "i", "s", "rules",
                                   category=cat, target=2)
            for a in validator._actions_for(f, rows_by_n):
                if a["kind"] == "server":
                    offered.add(a["id"])
    check("every server action a finding can offer is dispatchable",
          offered <= set(VA._HANDLERS))
    if offered - set(VA._HANDLERS):
        print("   orphaned:", offered - set(VA._HANDLERS))

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
