"""The chapter-level review chain (validator.py).

What these protect, in order of how much it would hurt to lose it:

1. THE CHAIN IS CHAPTER-LEVEL, NOT ROW-LOCAL. The first version judged each row
   in isolation and therefore could not see the failures that actually hurt a
   recap — a reveal before its setup, a line that belonged four rows earlier.
   "the chapter is mapped before any row is judged" and the overlap tests are
   that upgrade, frozen.
2. PRECISION. A validator that cries wolf costs the owner the exact reading
   time it exists to save. An early rule pass raised 27 findings on a clean
   chapter because it did not understand folded panels; that regression is
   frozen here too.
3. Nothing reaches the Anthropic API without passing usage.gate, so a Claude
   pass can never spend outside the daily cap or outside the cost the site
   shows.
4. A failed or skipped pass is never reported as a clean board.

The Claude passes are driven through a stub client — these tests never touch
the network and never need a key.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


# ------------------------------------------------------------ stub client
class _Blk:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    def __init__(self, i=1000, o=200, c=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_read_input_tokens = c


class _Resp:
    def __init__(self, payload, stop_reason="end_turn", usage=None):
        self.content = [_Blk(json.dumps(payload))] if payload is not None else []
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = usage or _Usage()


EMPTY_MAP = {
    "premise": "A duel.",
    "scenes": [{"scene": 1, "title": "The duel", "units": [0, 1],
                "phase": "setup", "summary": "Two swordsmen meet."}],
    "reveals": [{"unit": 1, "what": "the rival is his brother"}],
    "turns": [], "chronology_notes": "linear",
}


class StubMessages:
    """Records every call so the tests can assert on windowing and payloads.

    `replies` may be a list (popped in order) or a callable that picks a reply
    from the request — the chain makes several DIFFERENT kinds of call, so most
    tests route by what was asked rather than by counting.
    """

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.replies
        if callable(r):
            r = r(kw)
        elif isinstance(r, list):
            r = r.pop(0) if r else _Resp({"findings": []})
        if isinstance(r, Exception):
            raise r
        return r

    # The real SDK requires streaming for large max_tokens, so the stub has to
    # offer the same surface — otherwise the tests silently only ever cover the
    # non-streaming path, which is exactly how the production placement stage
    # shipped broken.
    def stream(self, **kw):
        outer = self

        class _Ctx:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def get_final_message(self_inner):
                return outer.create(**kw)

        return _Ctx()


class StubClient:
    def __init__(self, replies=()):
        self.messages = StubMessages(
            replies if callable(replies) else list(replies))


def router(chapter=None, seq=None, desc=None, vision=None):
    """Route a stub reply by which pass is asking. The passes are told apart by
    their system prompt, which is the only thing distinguishing them on the
    wire."""
    def pick(kw):
        sysmsg = kw.get("system")
        text = sysmsg[0]["text"] if isinstance(sysmsg, list) else str(sysmsg)
        if "building a map of it" in text:
            return _Resp(chapter if chapter is not None else EMPTY_MAP)
        if "RIGHT POINT in the" in text:
            return _Resp({"findings": list(seq or [])})
        if "AUTOMATED DESCRIPTIONS" in text:
            return _Resp({"findings": list(desc or [])})
        return _Resp(vision if vision is not None else
                     {"verdict": "cleared", "reason": "fine",
                      "corrected_description": "", "confidence": 0.9})
    return pick


# --------------------------------------------------------------- fixtures
def write_project(root, descs, scenes, segs):
    os.makedirs(os.path.join(root, "crops"), exist_ok=True)
    for name, data in (("descriptions.json", descs), ("script.json", scenes),
                       ("segments.json", segs)):
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    return root


def clean_board(root, n=4):
    """A board with nothing wrong with it. Every rule must stay silent."""
    half = max(1, n // 2)
    descs = [{"panel_id": f"page001_panel_{i:03d}", "file": f"p{i}.png",
              "ocr_text": f"dialogue line {i}", "ok": True,
              "visual_description": f"A swordsman faces a rival in scene {i}."}
             for i in range(1, n + 1)]
    scenes = [{"scene_id": 0, "text": "The duel began at dawn.",
               "panel_ids": [f"page001_panel_{i:03d}"
                             for i in range(1, half + 1)]},
              {"scene_id": 1, "text": "Steel met steel for hours.",
               "panel_ids": [f"page001_panel_{i:03d}"
                             for i in range(half + 1, n + 1)]}]
    segs = [{"seg_index": i - 1, "panel_id": f"page001_panel_{i:03d}",
             "start": (i - 1) * 4.0, "dur": 4.0, "user_included": True,
             "beats": [{"index": 0, "text": "The duel began at dawn.",
                        "start": 0.0, "end": 4.0}]}
            for i in range(1, n + 1)]
    return write_project(root, descs, scenes, segs)


def main():
    tmp = tempfile.mkdtemp(prefix="validator_")
    os.environ["USAGE_DIR"] = tmp
    import usage
    usage.USAGE_DIR = tmp
    usage.COUNTS_PATH = os.path.join(tmp, "counters.json")
    usage.LOG_PATH = os.path.join(tmp, "calls.log.jsonl")
    usage.LOCK_PATH = os.path.join(tmp, ".lock")
    import validator

    os.environ.setdefault("CLAUDE_API_KEY", "test-key-not-real")

    # ================================================== rows
    pdir = clean_board(os.path.join(tmp, "clean"))
    rows = validator.build_rows(pdir)
    check("one row per described panel", len(rows) == 4)
    check("rows carry all five board columns",
          all(k in rows[0] for k in ("panel_id", "ocr", "desc", "placement",
                                     "timing")))
    check("the first panel of a unit CARRIES the line",
          rows[0]["placement"]["role"] == "carries")
    check("later panels of the same unit SHARE it",
          rows[1]["placement"]["role"] == "shared")
    # Actions need coordinates, not just a description of the problem.
    check("rows carry the seg_index an action operates on",
          rows[0]["timing"]["seg_index"] == 0)
    check("...and the timeline position a move addresses",
          rows[0]["timing"]["pos"] == 0)

    # ================================================== chapter shape
    units = validator.chapter_units(rows)
    check("the chapter is readable as ordered narration units",
          [u["unit"] for u in units] == [0, 1])
    check("...each knowing which rows it owns", units[0]["rows"] == [1, 2])

    # ================================================== rule pass: silence
    found = validator.rule_findings(rows)
    check("a clean board produces NO rule findings", found == [])
    if found:
        print("   unexpected:", [f["issue"] for f in found])

    # ---- THE REGRESSION: folded panels share one beat's full text.
    folded_descs = [{"panel_id": f"pf_{i:03d}", "file": f"pf{i}.png",
                     "ocr_text": "x", "ok": True,
                     "visual_description": "A castle under a night sky."}
                    for i in range(1, 7)]
    beat = {"index": 0, "start": 0.0, "end": 5.5,
            "text": " ".join(["word"] * 24)}
    folded_segs = [{"seg_index": i, "panel_id": f"pf_{i + 1:03d}",
                    "start": i * 2.0, "dur": 2.0, "user_included": True,
                    "beats": [dict(beat)]} for i in range(6)]
    folded = write_project(
        os.path.join(tmp, "folded"), folded_descs,
        [{"scene_id": 0, "text": beat["text"],
          "panel_ids": [f"pf_{i:03d}" for i in range(1, 7)]}], folded_segs)
    ff = validator.rule_findings(validator.build_rows(folded))
    check("folded panels sharing one beat are not a timing fault",
          not [f for f in ff if f["category"] == "pacing"])

    # ================================================== rule pass: catches
    bad_descs = [
        {"panel_id": "b_001", "file": "b1.png", "ok": True,
         "ocr_text": "AsuraScans / discord.gg/asurascans / TL OAKS",
         "visual_description": "A credits page."},
        {"panel_id": "b_002", "file": "b2.png", "ok": True,
         "ocr_text": "he drew his blade", "visual_description": ""},
        {"panel_id": "b_003", "file": "b3.png", "ok": True,
         "ocr_text": "a shout", "visual_description": "A wide shot of a hall."},
        {"panel_id": "b_004", "file": "b4.png", "ok": True,
         "ocr_text": "silence", "visual_description": "An empty hall at dusk."},
    ]
    bad_scenes = [
        {"scene_id": 0, "text": "Unit zero.", "panel_ids": ["b_001"]},
        {"scene_id": 5, "text": "Unit five.", "panel_ids": ["b_002"]},
        {"scene_id": 1, "text": "Unit one.", "panel_ids": ["b_003"]},
        {"scene_id": 9, "text": "Never shown.", "panel_ids": ["b_004"]},
    ]
    bad_segs = [
        {"seg_index": 0, "panel_id": "b_001", "start": 0.0, "dur": 4.0,
         "user_included": True, "beats": [{"index": 0, "text": "Unit zero.",
                                           "start": 0.0, "end": 4.0}]},
        {"seg_index": 1, "panel_id": "b_002", "start": 4.0, "dur": 0.5,
         "user_included": True, "beats": [{"index": 1, "text": "Unit five.",
                                           "start": 4.0, "end": 4.5}]},
        {"seg_index": 2, "panel_id": "b_003", "start": 4.5, "dur": 20.0,
         "user_included": True, "beats": []},
        {"seg_index": 3, "panel_id": "b_004", "start": 24.5, "dur": 3.0,
         "user_included": False, "beats": []},
    ]
    bpdir = write_project(os.path.join(tmp, "bad"), bad_descs, bad_scenes,
                          bad_segs)
    bf = validator.rule_findings(validator.build_rows(bpdir))
    cats = [(f["row"], f["category"]) for f in bf]

    check("a credit page carrying narration is caught", (1, "ocr") in cats)
    check("an empty description is caught", (2, "description") in cats)
    check("a sub-second flash panel is caught", (2, "pacing") in cats)
    check("a long silent hold is caught", (3, "pacing") in cats)
    check("a story-order inversion is caught", (3, "order") in cats)
    check("a narration unit with no panel in the video is caught",
          (4, "coverage") in cats)
    check("every rule finding names a fix, not just a complaint",
          all(f["suggestion"] for f in bf))
    check("every finding carries a category AND a severity",
          all(f["category"] in validator.CATEGORIES
              and f["severity"] in validator.SEVERITIES for f in bf))
    check("...labelled in the words the drawer shows",
          all(f["severity_label"] and f["category_label"] for f in bf))
    check("...and a confidence", all("confidence" in f for f in bf))

    # ---- COVERAGE: three situations the rule used to collapse into one.
    # It asked only "is this unit in the video", and in_video needs the tick.
    # Segments are born unticked, so on an un-reviewed project it fired once
    # per unit — which is how two lab runs produced findings counts exactly
    # equal to their unit counts and clean-row percentages reproducible as
    # (panels - units)/panels. It was measuring the unit count.
    def cov_project(name, tick_first, give_second_a_segment=True):
        d = [{"panel_id": "c_001", "file": "c1.png", "ok": True,
              "ocr_text": "a", "visual_description": "A swordsman strikes."},
             {"panel_id": "c_002", "file": "c2.png", "ok": True,
              "ocr_text": "b", "visual_description": "A rival parries hard."}]
        sc = [{"scene_id": 0, "text": "Unit zero.", "panel_ids": ["c_001"]},
              {"scene_id": 1, "text": "Unit one.", "panel_ids": ["c_002"]}]
        sg = [{"seg_index": 0, "panel_id": "c_001", "start": 0.0, "dur": 4.0,
               "user_included": tick_first,
               "beats": [{"index": 0, "text": "Unit zero.",
                          "start": 0.0, "end": 4.0}]}]
        if give_second_a_segment:
            sg.append({"seg_index": 1, "panel_id": "c_002", "start": 4.0,
                       "dur": 4.0, "user_included": False,
                       "beats": [{"index": 1, "text": "Unit one.",
                                  "start": 4.0, "end": 8.0}]})
        return write_project(os.path.join(tmp, name), d, sc, sg)

    unreviewed = validator.rule_findings(validator.build_rows(
        cov_project("cov_unreviewed", tick_first=False)))
    check("an UN-REVIEWED project reports no coverage findings at all",
          not [f for f in unreviewed if f["category"] == "coverage"])

    # An un-reviewed project is judged AS BUILT. Every timing rule is gated on
    # in_video, so without this the checker is nearly blind on exactly the
    # chapters it is most useful for: re-measuring the Overgeared lab chapter
    # returned 0 findings over 164 panels, which was silence, not cleanliness.
    flash = write_project(
        os.path.join(tmp, "asbuilt"),
        [{"panel_id": "a_001", "file": "a1.png", "ok": True, "ocr_text": "x",
          "visual_description": "Charging forward, the swordsman lunges."}],
        [{"scene_id": 0, "text": "He lunged.", "panel_ids": ["a_001"]}],
        [{"seg_index": 0, "panel_id": "a_001", "start": 0.0, "dur": 0.4,
          "beats": [{"index": 0, "text": "He lunged.", "start": 0.0,
                     "end": 0.4}]}])          # NOTE: no user_included at all
    ab = validator.rule_findings(validator.build_rows(flash))
    check("an un-reviewed chapter is still checked for pacing, as built",
          any(f["category"] == "pacing" for f in ab))
    rej = validator.build_rows(flash, review={"0": {"status": "rejected"}})
    check("...but a REJECTED segment stays excluded, because that is a ruling",
          not rej[0]["timing"]["in_video"])

    partly = validator.rule_findings(validator.build_rows(
        cov_project("cov_partly", tick_first=True)))
    cov = [f for f in partly if f["category"] == "coverage"]
    check("once some units ARE ticked, an unticked unit is reported",
          len(cov) == 1 and cov[0]["row"] == 2)
    check("...as a review decision, not a pipeline fault",
          cov and cov[0]["severity"] == "medium")

    noseg = validator.rule_findings(validator.build_rows(
        cov_project("cov_noseg", tick_first=True,
                    give_second_a_segment=False)))
    cov2 = [f for f in noseg if f["category"] == "coverage"]
    check("a unit with NO panel at all is still a hard defect",
          len(cov2) == 1 and cov2[0]["severity"] == "high")
    check("...and says nothing was ever matched to it",
          cov2 and "no panel at all" in cov2[0]["issue"])

    # ---- a stall: several segments in a row on ONE panel
    stall_descs = [{"panel_id": f"s_{i:03d}", "file": f"s{i}.png",
                    "ocr_text": "x", "ok": True,
                    "visual_description": f"A quiet room, shot {i}."}
                   for i in range(1, 4)]
    stall_segs = [
        {"seg_index": 0, "panel_id": "s_001", "start": 0.0, "dur": 9.0,
         "user_included": True, "beats": [{"index": 0, "text": "a",
                                           "start": 0, "end": 9}]},
        {"seg_index": 1, "panel_id": "s_001", "start": 9.0, "dur": 9.0,
         "user_included": True, "beats": [{"index": 1, "text": "b",
                                           "start": 9, "end": 18}]},
        {"seg_index": 2, "panel_id": "s_002", "start": 18.0, "dur": 4.0,
         "user_included": True, "beats": [{"index": 2, "text": "c",
                                           "start": 18, "end": 22}]},
    ]
    stall = write_project(
        os.path.join(tmp, "stall"), stall_descs,
        [{"scene_id": 0, "text": "a b c",
          "panel_ids": ["s_001", "s_002", "s_003"]}], stall_segs)
    sf = validator.rule_findings(validator.build_rows(stall))
    check("a run of segments stuck on one panel is caught",
          any(f["category"] == "pacing" and "play over this one panel"
              in f["issue"] for f in sf))

    # THE MERGED CASE, which the seg_count version could never see:
    # build_segments merges consecutive shots on one panel into a SINGLE
    # segment, so a long hold has seg_count 1. Counting beats is what catches
    # it — the Overgeared lab chapter had 11 such holds the rule missed.
    merged = write_project(
        os.path.join(tmp, "merged_hold"),
        [{"panel_id": "m_001", "file": "m1.png", "ok": True, "ocr_text": "x",
          "visual_description": "Standing still, the swordsman waits."}],
        [{"scene_id": 0, "text": "a b c", "panel_ids": ["m_001"]}],
        [{"seg_index": 0, "panel_id": "m_001", "start": 0.0, "dur": 21.0,
          "user_included": True,
          "beats": [{"index": 0, "text": "a", "start": 0.0, "end": 7.0},
                    {"index": 1, "text": "b", "start": 7.0, "end": 14.0},
                    {"index": 2, "text": "c", "start": 14.0, "end": 21.0}]}])
    mf = validator.rule_findings(validator.build_rows(merged))
    check("a long hold merged into ONE segment is still caught",
          any(f["category"] == "pacing" for f in mf))

    # A credit page that is ALREADY left out is the pipeline working.
    ok_credit = write_project(os.path.join(tmp, "credit_ok"), [bad_descs[0]],
                              [], [])
    check("a credit page correctly left out is not a finding",
          not validator.rule_findings(validator.build_rows(ok_credit)))

    # ================================================== fail fast, by name
    saved = {k: os.environ.pop(k, None) for k in validator.API_KEY_VARS}
    try:
        validator._client()
        check("a missing key raises rather than silently skipping", False)
    except validator.ValidatorError as e:
        check("a missing key raises rather than silently skipping",
              "CLAUDE_API_KEY" in str(e))
    os.environ["CLAUDE_API_KEY"] = "from-claude-var"
    check("CLAUDE_API_KEY is accepted", validator.api_key() == "from-claude-var")
    os.environ["ANTHROPIC_API_KEY"] = "from-anthropic-var"
    check("...and wins over ANTHROPIC_API_KEY when both are set",
          validator.api_key() == "from-claude-var")
    del os.environ["CLAUDE_API_KEY"]
    check("ANTHROPIC_API_KEY still works as the fallback",
          validator.api_key() == "from-anthropic-var")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["CLAUDE_API_KEY"] = "   "
    check("a blank key counts as missing, not as configured",
          validator.api_key() == "")
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v:
            os.environ[k] = v
    os.environ.setdefault("CLAUDE_API_KEY", "test-key-not-real")

    # ============================== overlapping windows (edge-of-batch)
    seq_rows = [{"n": i} for i in range(1, 31)]
    wins = validator._windows(seq_rows, 10, 3)
    check("sequence windows overlap so edge-of-batch mistakes are visible",
          len(wins) > 1 and wins[0][-1]["n"] > wins[1][0]["n"])
    check("...and the whole chapter is still covered",
          max(r["n"] for w in wins for r in w) == 30)
    check("a zero overlap still tiles without looping forever",
          len(validator._windows(seq_rows, 10, 0)) == 3)

    # ============================== pass A: the chapter is mapped first
    big = clean_board(os.path.join(tmp, "big"), n=12)
    brows = validator.build_rows(big)
    stub = StubClient(router())
    validator._client = lambda: stub

    cmap, cstats = validator.chapter_map(brows, model="claude-opus-5")
    check("the chapter map pass makes exactly one call", cstats["calls"] == 1)
    check("...and returns scenes, reveals and chronology",
          cmap["scenes"] and "reveals" in cmap and "chronology_notes" in cmap)
    check("...built from the narration, not from the rows",
          "Map this chapter" in json.dumps(stub.messages.calls[0]["messages"]))

    # ============================== pass B judges rows AGAINST the map
    stub2 = StubClient(router(seq=[
        {"row": 3, "category": "order", "severity": "high",
         "issue": "this reveal lands before the line that sets it up",
         "suggestion": "move it after unit 1", "confidence": 0.9,
         "target_row": 5},
        {"row": 4, "category": "continuity", "severity": "medium",
         "issue": "this panel is from a different scene than its neighbours",
         "suggestion": "check the match", "confidence": 0.7, "target_row": 0},
    ]))
    validator._client = lambda: stub2
    seq, sstats = validator.sequence_findings(brows, cmap,
                                              model="claude-opus-5")
    sysmsg = stub2.messages.calls[0]["system"][0]["text"]
    check("the sequence pass is given the chapter map, not just rows",
          "CHAPTER MAP" in sysmsg and "SCENE 1" in sysmsg)
    check("...including where the reveals land",
          "REVEAL lands at unit 1" in sysmsg)
    check("...as a cacheable prefix so it is paid for once",
          stub2.messages.calls[0]["system"][0].get("cache_control"))
    check("an order problem is detected",
          any(f["category"] == "order" for f in seq))
    check("a scene-continuity problem is detected",
          any(f["category"] == "continuity" for f in seq))
    check("a named target row becomes a one-click swap target",
          any(f["target_row"] == 5 for f in seq))
    check("...and a target of 0 is treated as 'none named'",
          all(f["target_row"] != 0 for f in seq))
    check("sequence stats report the overlap actually used",
          sstats["overlap"] == validator.SEQ_OVERLAP)

    # a finding reported twice by two overlapping windows is emitted once
    dupe = StubClient(router(seq=[
        {"row": 3, "category": "order", "severity": "high", "issue": "dupe",
         "suggestion": "x", "confidence": 0.9, "target_row": 0}]))
    validator._client = lambda: dupe
    old_batch, old_ov = validator.BATCH_ROWS, validator.SEQ_OVERLAP
    validator.BATCH_ROWS, validator.SEQ_OVERLAP = 6, 3
    dseq, _ = validator.sequence_findings(brows, cmap, model="claude-opus-5")
    check("a row seen by two overlapping windows is reported once",
          len([f for f in dseq if f["row"] == 3]) == 1)
    validator.BATCH_ROWS, validator.SEQ_OVERLAP = old_batch, old_ov

    # ============================== pass D: description poisoning
    stub3 = StubClient(router(desc=[
        {"row": 2, "category": "description", "severity": "high",
         "issue": "the description contradicts the OCR on this panel",
         "suggestion": "re-run describe", "confidence": 0.85},
        {"row": 6, "category": "ocr", "severity": "medium",
         "issue": "the OCR looks like a watermark bleeding in",
         "suggestion": "re-run OCR", "confidence": 0.6},
    ]))
    validator._client = lambda: stub3
    dfind, _ = validator.description_findings(brows, model="claude-opus-5")
    check("a likely-wrong description is identified as description poison",
          any(f["category"] == "description" for f in dfind))
    check("...and poisoned OCR separately from it",
          any(f["category"] == "ocr" for f in dfind))
    check("the description pass sees each row's NEIGHBOURS, not the row alone",
          "prev_description" in json.dumps(stub3.messages.calls[0]["messages"]))

    # ============================== pass E: spot checks on CLEAN rows
    spot = validator.spot_check_rows(brows, flagged={1, 2})
    check("rows nothing flagged are sampled for an image check", len(spot) > 0)
    check("...never a row that was already flagged", not (set(spot) & {1, 2}))
    check("...spread across the chapter rather than clustered",
          max(spot) - min(spot) > len(brows) // 3)
    check("sampling can be switched off",
          validator.spot_check_rows(brows, flagged=set(), n=0) == [])

    # ================================================== full run
    from PIL import Image
    for i in range(1, 13):
        Image.new("RGB", (300, 600), (30, 30, 40)).save(
            os.path.join(big, "crops", f"p{i}.png"))

    shared = StubClient(router(
        seq=[{"row": 3, "category": "placement", "severity": "high",
              "issue": "this line belongs to another panel",
              "suggestion": "swap it", "confidence": 0.9, "target_row": 5}],
        vision={"verdict": "cleared", "reason": "the image matches after all",
                "corrected_description": "", "confidence": 0.9}))
    validator._client = lambda: shared

    before_calls = usage.daily_summary()["claude_calls"]
    rep = validator.validate(big, mode="full")
    after = usage.daily_summary()

    check("a full run reports ok", rep["status"] == "ok")
    check("the report records every pass separately",
          all(k in rep["passes"] for k in
              ("rules", "chapter_map", "sequence", "description", "vision")))
    check("the chapter map is kept in the report so it can be inspected",
          rep["chapter"] and rep["chapter"]["scenes"])
    check("every Claude call is counted by the usage gate",
          after["claude_calls"] == before_calls + len(shared.messages.calls))
    check("the report carries the cost it incurred", rep["cost_usd"] > 0)
    check("cost is metered from reported tokens, not a flat guess",
          abs(rep["cost_usd"] - usage.token_cost(
              "claude-opus-5", rep["prompt_tokens"],
              rep["output_tokens"])) < 1e-6)
    check("findings are counted by category as well as severity",
          "by_category" in rep and "counts" in rep)
    check("the vision pass reports how many were spot checks",
          "spot_checked" in rep["passes"]["vision"])
    check("a vision-cleared finding is demoted, not deleted",
          any(f.get("cleared") and f["severity"] == "low"
              for f in rep["findings"]))

    # ---- EVERY finding must be actionable
    check("every finding carries actions",
          bool(rep["findings"]) and all(f.get("actions")
                                        for f in rep["findings"]))
    check("...always including a way to jump to the row",
          all(any(a["id"] == "goto" for a in f["actions"])
              for f in rep["findings"]))
    check("...and a way to accept it as intentional",
          all(any(a["id"] == "accept" for a in f["actions"])
              for f in rep["findings"]))
    place = [f for f in rep["findings"] if f["category"] == "placement"]
    check("a placement finding with a target offers a one-click swap",
          any(any(a["id"] == "swap" for a in f["actions"]) for f in place))
    desc_f = [f for f in rep["findings"] if f["category"] == "description"]
    check("a description finding offers a re-describe",
          all(any(a["id"] == "redescribe" for a in f["actions"])
              for f in desc_f) if desc_f else True)
    check("server actions describe what they will do before they do it",
          all(a.get("preview") for f in rep["findings"]
              for a in f["actions"] if a["kind"] == "server"))

    # ---- a spot check that CONFIRMS becomes a finding of its own
    spotter = StubClient(router(
        seq=[], desc=[],
        vision={"verdict": "confirmed",
                "reason": "the panel shows a different scene entirely",
                "corrected_description": "A burning village.",
                "confidence": 0.9}))
    validator._client = lambda: spotter
    rep2 = validator.validate(big, mode="full")
    check("a spot check that finds a problem on an unflagged row reports it",
          any(f.get("from_spot_check") for f in rep2["findings"]))
    check("...carrying what the image actually showed",
          any((f.get("vision") or {}).get("corrected_description")
              for f in rep2["findings"] if f.get("from_spot_check")))

    # ================================================== feedback loop
    target = rep2["findings"][0]
    validator.record_feedback(big, target["id"], "accepted",
                              panel_id=target["panel_id"],
                              category=target["category"])
    fb = validator.load_feedback(big)
    check("an accepted finding is remembered", target["id"] in fb)
    validator._client = lambda: spotter
    rep3 = validator.validate(big, mode="full")
    same = next((f for f in rep3["findings"] if f["id"] == target["id"]), None)
    check("...and comes back marked as accepted", bool(same and same.get("accepted")))
    check("...demoted rather than deleted, so it can be reconsidered",
          bool(same) and same["severity"] == "low")
    check("...and excluded from the headline counts",
          rep3["accepted_suppressed"] >= 1)
    validator.record_feedback(big, target["id"], "reopened")
    check("an accepted finding can be reopened",
          target["id"] not in validator.load_feedback(big))
    try:
        validator.record_feedback(big, "x", "nonsense")
        check("an unknown verdict is rejected", False)
    except ValueError:
        check("an unknown verdict is rejected", True)

    # ================================================== honest failure
    validator._client = lambda: (_ for _ in ()).throw(
        validator.ValidatorError("CLAUDE_API_KEY is not set"))
    frep = validator.validate(big, mode="text")
    check("a failed pass marks the report as error",
          frep["status"] == "error" and bool(frep["error"]))
    check("...while keeping the rule findings already gathered",
          isinstance(frep["findings"], list))

    validator._client = lambda: StubClient([_Resp(None, stop_reason="refusal")])
    try:
        validator.chapter_map(brows, model="claude-opus-5")
        check("a refusal is surfaced, not swallowed as a clean batch", False)
    except validator.ValidatorError as e:
        check("a refusal is surfaced, not swallowed as a clean batch",
              "declined" in str(e).lower())

    class _Junk(_Resp):
        def __init__(self):
            super().__init__({"findings": []})
            self.content = [_Blk("not json at all")]

    validator._client = lambda: StubClient([_Junk()])
    try:
        validator.chapter_map(brows, model="claude-opus-5")
        check("an unparseable reply is surfaced", False)
    except validator.ValidatorError:
        check("an unparseable reply is surfaced", True)

    # ---- rules-only mode spends nothing and needs no key
    spent = usage.daily_summary()["est_cost_usd"]
    rrep = validator.validate(big, mode="rules")
    check("rules-only mode spends nothing",
          rrep["cost_usd"] == 0 and
          usage.daily_summary()["est_cost_usd"] == spent)
    check("rules-only mode still reports ok", rrep["status"] == "ok")

    # ================================================== caps
    validator._client = lambda: StubClient(router())
    usage.MAX_DAILY_CLAUDE_CALLS = usage.daily_summary()["claude_calls"]
    capped = validator.validate(big, mode="text")
    check("the daily Claude cap stops the run",
          capped["status"] == "error" and
          "CLAUDE" in (capped["error"] or "").upper())

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
