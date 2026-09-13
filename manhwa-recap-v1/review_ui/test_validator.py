"""The pre-owner review chain (validator.py).

What these protect, in order of how much it would hurt to lose it:

1. PRECISION. A validator that cries wolf costs the owner the exact reading
   time it exists to save. The first version of the rule pass raised 27
   findings on a clean chapter because it did not understand folded panels;
   `folded panels sharing one beat are not a timing fault` is that bug, frozen.
2. Nothing reaches the Anthropic API without passing usage.gate, so a Claude
   pass can never spend outside the daily cap or outside the cost the site
   shows.
3. A failed or skipped pass is never reported as a clean board.

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


class StubMessages:
    """Records every call so the tests can assert on batching and payloads."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0) if self.replies else _Resp({"findings": []})
        if isinstance(r, Exception):
            raise r
        return r


class StubClient:
    def __init__(self, replies=()):
        self.messages = StubMessages(replies)


# --------------------------------------------------------------- fixtures
def write_project(root, descs, scenes, segs):
    os.makedirs(os.path.join(root, "crops"), exist_ok=True)
    for name, data in (("descriptions.json", descs), ("script.json", scenes),
                       ("segments.json", segs)):
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    return root


def clean_board(root):
    """A board with nothing wrong with it. Every rule must stay silent."""
    descs = [{"panel_id": f"page001_panel_{i:03d}", "file": f"p{i}.png",
              "ocr_text": f"dialogue line {i}", "ok": True,
              "visual_description": f"A swordsman faces a rival in scene {i}."}
             for i in range(1, 5)]
    scenes = [{"scene_id": 0, "text": "The duel began at dawn.",
               "panel_ids": ["page001_panel_001", "page001_panel_002"]},
              {"scene_id": 1, "text": "Steel met steel for hours.",
               "panel_ids": ["page001_panel_003", "page001_panel_004"]}]
    segs = [{"seg_index": i - 1, "panel_id": f"page001_panel_{i:03d}",
             "start": (i - 1) * 4.0, "dur": 4.0, "user_included": True,
             "beats": [{"index": 0, "text": "The duel began at dawn.",
                        "start": 0.0, "end": 4.0}]}
            for i in range(1, 5)]
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
    check("a panel in no unit is marked left out",
          validator.build_rows(write_project(
              os.path.join(tmp, "orphan"),
              [{"panel_id": "p1", "file": "p1.png", "ocr_text": "hi",
                "visual_description": "a panel", "ok": True}], [], []
          ))[0]["placement"]["role"] == "left_out")

    # ================================================== rule pass: silence
    found = validator.rule_findings(rows)
    check("a clean board produces NO rule findings", found == [], )
    if found:
        print("   unexpected:", [f["issue"] for f in found])

    # ---- THE REGRESSION: folded panels share one beat's full text.
    # Six panels each carrying a copy of the same 24-word beat must not look
    # like six panels that each have to speak 24 words in their own slice.
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
          not [f for f in ff if f["column"] == "On-screen timing & motion"])

    # ================================================== rule pass: catches
    bad_descs = [
        {"panel_id": "b_001", "file": "b1.png", "ok": True,
         "ocr_text": "AsuraScans / discord.gg/asurascans / TL OAKS",
         "visual_description": "A credits page."},
        {"panel_id": "b_002", "file": "b2.png", "ok": True,
         "ocr_text": "he drew his blade", "visual_description": ""},
        {"panel_id": "b_003", "file": "b3.png", "ok": True,
         "ocr_text": "a shout", "visual_description": "A wide shot."},
        {"panel_id": "b_004", "file": "b4.png", "ok": True,
         "ocr_text": "silence", "visual_description": "An empty hall."},
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
    cols = [(f["row"], f["column"]) for f in bf]

    check("a credit page carrying narration is caught",
          (1, "System OCR") in cols)
    check("an empty description is caught", (2, "System description") in cols)
    check("a sub-second flash panel is caught",
          (2, "On-screen timing & motion") in cols)
    check("a long silent hold is caught",
          (3, "On-screen timing & motion") in cols)
    check("a story-order inversion is caught",
          (3, "Script placement") in cols)
    check("a narration unit with no panel in the video is caught",
          any(c == "Script placement" and "no panel in the final video" in
              f["issue"] for (rn, c), f in zip(cols, bf)))
    check("every rule finding names a fix, not just a complaint",
          all(f["suggestion"] for f in bf))
    check("rule findings are attributed to the rules pass",
          all(f["source"] == "rules" for f in bf))

    # A credit page that is ALREADY left out is the pipeline working.
    ok_credit = write_project(
        os.path.join(tmp, "credit_ok"),
        [bad_descs[0]], [], [])
    check("a credit page correctly left out is not a finding",
          not validator.rule_findings(validator.build_rows(ok_credit)))

    # ================================================== fail fast, by name
    saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        validator._client()
        check("a missing key raises rather than silently skipping", False)
    except validator.ValidatorError as e:
        check("a missing key raises rather than silently skipping",
              "ANTHROPIC_API_KEY" in str(e))
    os.environ["ANTHROPIC_API_KEY"] = saved_key or "test-key-not-real"

    # ================================================== Claude text pass
    validator.BATCH_ROWS = 2
    stub = StubClient([
        _Resp({"findings": [{"row": 1, "column": "Script placement",
                             "severity": "high", "issue": "line belongs to row 2",
                             "suggestion": "move it", "confidence": 0.9}]}),
        _Resp({"findings": [{"row": 99, "column": "Script placement",
                             "severity": "high", "issue": "hallucinated row",
                             "suggestion": "n/a", "confidence": 0.9}]}),
    ])
    validator._client = lambda: stub

    before_calls = usage.daily_summary()["claude_calls"]
    before_cost = usage.daily_summary()["est_cost_usd"]
    tf, stats = validator.claude_text_findings(rows, model="claude-opus-5")
    after = usage.daily_summary()

    check("rows are batched, not sent one call each",
          stats["batches"] == 2 and len(stub.messages.calls) == 2)
    check("a real finding is kept", any(f["row"] == 1 for f in tf))
    check("a finding for a row outside the batch is dropped",
          not any(f["row"] == 99 for f in tf))
    check("claude findings are attributed to the claude pass",
          all(f["source"] == "claude-text" for f in tf))
    check("every Claude call is counted by the usage gate",
          after["claude_calls"] == before_calls + 2)
    check("...and charged to the daily spend",
          after["est_cost_usd"] > before_cost)
    check("cost is metered from reported tokens, not a flat guess",
          abs(stats["cost_usd"] - usage.token_cost(
              "claude-opus-5", stats["prompt_tokens"],
              stats["output_tokens"])) < 1e-9)
    check("the batch prompt carries the rows",
          "script_placement" in json.dumps(stub.messages.calls[0]["messages"]))
    check("the instructions are sent as a cacheable prefix",
          stub.messages.calls[0]["system"][0].get("cache_control"))
    check("a JSON schema constrains the reply",
          stub.messages.calls[0]["output_config"]["format"]["type"]
          == "json_schema")

    # ---- a refusal must not read as "no problems found"
    validator._client = lambda: StubClient([_Resp(None, stop_reason="refusal")])
    try:
        validator.claude_text_findings(rows[:2], model="claude-opus-5")
        check("a refusal is surfaced, not swallowed as a clean batch", False)
    except validator.ValidatorError as e:
        check("a refusal is surfaced, not swallowed as a clean batch",
              "declined" in str(e).lower())

    # ---- unparseable output likewise
    class _Junk(_Resp):
        def __init__(self):
            super().__init__({"findings": []})
            self.content = [_Blk("not json at all")]

    validator._client = lambda: StubClient([_Junk()])
    try:
        validator.claude_text_findings(rows[:2], model="claude-opus-5")
        check("an unparseable reply is surfaced", False)
    except validator.ValidatorError:
        check("an unparseable reply is surfaced", True)

    # ================================================== full run + report
    validator.BATCH_ROWS = 25
    validator._client = lambda: StubClient([
        _Resp({"findings": [{"row": 2, "column": "Script placement",
                             "severity": "high", "issue": "wrong panel",
                             "suggestion": "move to row 3",
                             "confidence": 0.8}]}),
    ])
    rep = validator.validate(pdir, mode="text")
    check("a report is persisted next to the project",
          os.path.exists(validator.report_path(pdir)))
    check("the report reloads", validator.load_report(pdir)["mode"] == "text")
    check("the report states its status", rep["status"] == "ok")
    check("the report carries the cost it incurred", rep["cost_usd"] > 0)
    check("the report names the model that judged", rep["model"])
    check("the report counts findings by severity", "counts" in rep)
    check("findings are ordered worst-first",
          [f["severity"] for f in rep["findings"]] ==
          sorted((f["severity"] for f in rep["findings"]),
                 key=validator.SEVERITIES.index))

    # ---- rules-only mode spends nothing and needs no key
    spent = usage.daily_summary()["est_cost_usd"]
    rrep = validator.validate(pdir, mode="rules")
    check("rules-only mode spends nothing",
          rrep["cost_usd"] == 0 and
          usage.daily_summary()["est_cost_usd"] == spent)
    check("rules-only mode still reports ok", rrep["status"] == "ok")

    # ---- a failed Claude pass is an ERROR, never a clean board
    validator._client = lambda: (_ for _ in ()).throw(
        validator.ValidatorError("ANTHROPIC_API_KEY is not set"))
    frep = validator.validate(pdir, mode="text")
    check("a failed pass marks the report as error",
          frep["status"] == "error" and frep["error"])
    check("...while keeping the rule findings already gathered",
          isinstance(frep["findings"], list))

    # ================================================== vision pass
    from PIL import Image
    vdir = clean_board(os.path.join(tmp, "vision"))
    for i in range(1, 5):
        Image.new("RGB", (400, 900), (30, 30, 40)).save(
            os.path.join(vdir, "crops", f"p{i}.png"))
    vrows = validator.build_rows(vdir)
    targets = {1: [{"issue": "line may belong elsewhere"}]}

    vstub = StubClient([_Resp({"verdict": "cleared",
                               "reason": "the panel does match the line",
                               "corrected_description": "",
                               "confidence": 0.9})])
    validator._client = lambda: vstub
    checked, skipped, vstats = validator.claude_vision_findings(
        vdir, vrows, targets, model="claude-opus-5")
    check("the vision pass calls once per flagged row, not per row",
          vstats["calls"] == 1)
    check("the vision call actually carries the panel image",
          any(b.get("type") == "image"
              for b in vstub.messages.calls[0]["messages"][0]["content"]))
    check("the vision verdict comes back", checked[1]["verdict"] == "cleared")

    validator.MAX_VISION_ROWS = 1
    many = {1: [{"issue": "a"}], 2: [{"issue": "b"}], 3: [{"issue": "c"}]}
    validator._client = lambda: StubClient([
        _Resp({"verdict": "confirmed", "reason": "r",
               "corrected_description": "", "confidence": 0.9})])
    _, skipped2, vstats2 = validator.claude_vision_findings(
        vdir, vrows, many, model="claude-opus-5")
    check("the vision cap is enforced", vstats2["calls"] == 1)
    check("...and rows past it are reported as unchecked, not as clean",
          len(skipped2) == 2 and all("cap" in s["why"] for s in skipped2))
    validator.MAX_VISION_ROWS = 24

    # ---- a cleared finding is demoted and annotated, never deleted
    # ONE stub shared by both passes — _client() is called once per pass, so a
    # lambda that builds a fresh StubClient would replay the text reply at the
    # vision pass and silently test nothing.
    shared = StubClient([
        _Resp({"findings": [{"row": 1, "column": "Script placement",
                             "severity": "high", "issue": "suspect",
                             "suggestion": "check", "confidence": 0.8}]}),
        _Resp({"verdict": "cleared", "reason": "image shows it is fine",
               "corrected_description": "", "confidence": 0.95}),
    ])
    validator._client = lambda: shared
    full = validator.validate(vdir, mode="full")
    check("the full run used both passes off one client",
          len(shared.messages.calls) == 2)
    sus = [f for f in full["findings"] if f["row"] == 1
           and f["column"] == "Script placement"]
    check("a vision-cleared finding survives in the report", bool(sus))
    if sus:
        check("...demoted rather than deleted, so the check stays visible",
              sus[0]["severity"] == "low" and sus[0].get("cleared"))
        check("...carrying the reason the image gave",
              sus[0]["vision"]["reason"])

    # ================================================== caps
    validator._client = lambda: StubClient([_Resp({"findings": []})] * 50)
    usage.MAX_DAILY_CLAUDE_CALLS = usage.daily_summary()["claude_calls"]
    capped = validator.validate(pdir, mode="text")
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
