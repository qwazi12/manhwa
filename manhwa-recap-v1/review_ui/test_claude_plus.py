"""Claude+ — the lab pipeline with production's discipline ported in.

What these protect, in order of how much it would hurt to lose it:

1. ISOLATION. The lab must be incapable of touching a production chapter. The
   baseline project is hashed before and after a full run.
2. THE ORDERING FIX. The crop must be chosen AFTER the script and the
   placement, and must be given the line it is framing for. This was the
   structural defect; a test that only checked "a crop exists" would pass on
   the broken version.
3. DETERMINISM WHERE IT BELONGS. Placement is a solver, not a prompt: monotonic
   by construction, junk-avoiding, and able to escape a wrong scene grouping
   while SAYING that it did.
4. THE PORTED CONTRACTS actually bind — a word cap and a banned opener that are
   only asked for in a prompt are wishes, so they are checked in code.

No network: Claude is driven through a stub that routes by system prompt.
"""
import hashlib
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
    def __init__(self):
        self.input_tokens = 1500
        self.output_tokens = 300
        self.cache_read_input_tokens = 0


class _Resp:
    def __init__(self, payload):
        self.content = [_Blk(payload if isinstance(payload, str)
                             else json.dumps(payload))]
        self.stop_reason = "end_turn"
        self.stop_details = None
        self.usage = _Usage()


class StubMessages:
    def __init__(self, pick):
        self.pick = pick
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return _Resp(self.pick(kw))

    def stream(self, **kw):
        outer = self

        class _Ctx:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def get_final_message(s):
                return outer.create(**kw)

        return _Ctx()


class StubClient:
    def __init__(self, pick):
        self.messages = StubMessages(pick)


SCRIPT_TEXT = ("Dawn broke over the ruined courtyard as the swordsman took his "
               "stance. He demanded to know who the stranger was, and the "
               "answer stopped him cold.")


def make_router(state):
    """Route by system prompt. The stages are told apart by nothing else."""
    def pick(kw):
        sysmsg = kw.get("system")
        text = sysmsg[0]["text"] if isinstance(sysmsg, list) else str(sysmsg)
        body = json.dumps(kw.get("messages", []))[:4000]

        if "looking at one full page" in text:
            state.setdefault("split_calls", []).append(text)
            if "YOUR PREVIOUS ANSWER MISSED ART" in text:
                state["retried"] = True
                return {"panels": [{"box": [0.0, 0.0, 1.0, 1.0], "kind": "art"}]}
            return {"panels": state.get("split_boxes",
                                        [{"box": [0.0, 0.0, 1.0, 0.45],
                                          "kind": "art"}])}

        if "reading single panels cut from a manhwa" in text:
            sent = []
            for blk in kw["messages"][0]["content"]:
                if isinstance(blk, dict) and blk.get("type") == "text" \
                        and blk["text"].startswith("PANEL "):
                    sent.append(int(blk["text"].split()[1].rstrip(":")))
            return {"panels": [state["describe"](n) for n in sent]}

        if "building the map every later stage" in text:
            return state["chapter_map"]

        if "master comic-recap narrator" in text:
            state.setdefault("script_prompts", []).append(body)
            if "EDITOR NOTES" in body:
                state.setdefault("revised", []).append(body)
                return SCRIPT_TEXT + " The revised line lands cleanly."
            return SCRIPT_TEXT

        if "fact-checking script editor" in text:
            return {"issues": state.get("issues", [])}

        if "choosing how each panel is framed" in text:
            state.setdefault("crop_prompts", []).append(body)
            sent = []
            for blk in kw["messages"][0]["content"]:
                if isinstance(blk, dict) and blk.get("type") == "text" \
                        and blk["text"].startswith("ITEM "):
                    sent.append(int(blk["text"].split()[1]))
            return {"crops": [state["crop"](n) for n in sent]}
        return {}
    return pick


def good_panel(n):
    return {"n": n, "ocr": f"Line of dialogue {n}",
            "ocr_confidence": 0.9,
            "description": f"Drawing his blade, the swordsman faces rival {n} "
                           "across the courtyard.",
            "desc_confidence": 0.9, "is_credits": n == 1,
            "subject_type": "character", "importance": 3,
            "needs_review": False, "focus_hint": [0, 0, 1, 1]}


def good_crop(n):
    return {"n": n, "crop": [0.1, 0.1, 0.9, 0.9], "framing": "medium",
            "reason": "the line is about his expression", "confidence": 0.8}


CHAPTER_MAP = {
    "premise": "A duel at dawn.",
    "scenes": [{"scene": 1, "title": "The duel", "first_panel": 1,
                "last_panel": 6, "phase": "climax",
                "summary": "Two swordsmen meet."}],
    "reveals": [{"panel": 5, "what": "the rival is his brother"}],
    "turns": [], "climax_panel": 5, "compression_risks": [1],
}


# --------------------------------------------------------------- fixtures
def make_project(root, n=6):
    """A lab project with pages and crops already present, so scrape and split
    are skipped and the stages under test run."""
    os.makedirs(os.path.join(root, "pages"), exist_ok=True)
    os.makedirs(os.path.join(root, "crops"), exist_ok=True)
    from PIL import Image
    Image.new("RGB", (400, 900), (240, 240, 240)).save(
        os.path.join(root, "pages", "001.png"))
    for i in range(1, n + 1):
        Image.new("RGB", (400, 500), (30, 30, 40)).save(
            os.path.join(root, "crops", f"page001_panel_{i:03d}.png"))
    return root


def baseline_project(root):
    """A production-shaped chapter that must not be touched."""
    os.makedirs(root, exist_ok=True)
    for name, data in (
        ("descriptions.json", [{"panel_id": "b1", "file": "b1.png",
                                "ocr_text": "x", "visual_description": "y",
                                "ok": True}]),
        ("script.json", [{"scene_id": 0, "text": "t", "panel_ids": ["b1"]}]),
        ("segments.json", [{"seg_index": 0, "panel_id": "b1", "start": 0,
                            "end": 1, "dur": 1, "beats": []}]),
    ):
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    return root


def fingerprint(pdir):
    out = {}
    for f in ("descriptions.json", "script.json", "segments.json"):
        with open(os.path.join(pdir, f), "rb") as fh:
            out[f] = hashlib.sha1(fh.read()).hexdigest()
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="claude_plus_")
    os.environ["USAGE_DIR"] = tmp
    import usage
    usage.USAGE_DIR = tmp
    usage.COUNTS_PATH = os.path.join(tmp, "counters.json")
    usage.LOG_PATH = os.path.join(tmp, "calls.log.jsonl")
    usage.LOCK_PATH = os.path.join(tmp, ".lock")
    os.environ.setdefault("CLAUDE_API_KEY", "test-key-not-real")

    import validator
    import claude_plus as PLUS
    import claude_place as PLACE
    import claude_lab as LAB
    import claude_pipeline as CP

    # =============================== the ported description contract binds
    check("a compliant description passes the audit",
          PLUS.audit_description(
              "Tumbling down a slope, the boy claws at the dirt.") == [])
    check("a banned opener is caught in CODE, not just asked for in the prompt",
          "banned_opener" in PLUS.audit_description("A panel depicting a boy."))
    check("the 50-word cap is enforced in code",
          any(v.startswith("over_word_cap")
              for v in PLUS.audit_description(" ".join(["w"] * 60))))
    check("an empty description is caught",
          PLUS.audit_description("") == ["empty"])
    # The action-verb rule must not apply to panels with no action in them.
    # Forcing it produced dangling participles on the real Overgeared run.
    static_desc = "A boxed narration caption on an otherwise empty white page."
    check("a static panel is NOT required to open with an action verb",
          "weak_opener" not in PLUS.audit_description(
              static_desc, subject_type="text"))
    check("...nor is a credits card",
          "weak_opener" not in PLUS.audit_description(
              static_desc, is_credits=True))
    check("but an action panel still is",
          "weak_opener" in PLUS.audit_description(
              "A boy falls down a slope.", subject_type="character"))
    check("a static panel still cannot use a banned opener",
          "banned_opener" in PLUS.audit_description(
              "A panel depicting a caption.", subject_type="text"))

    # =============================== the ported word budget
    check("the budget formula matches production's",
          PLUS.word_budget(20, 8) == 220 and PLUS.word_budget(3, 0) == 40)
    talky = [{"n": i, "ocr_text": "a real spoken line", "is_credits": False}
             for i in range(30)]
    silent = [{"n": i, "ocr_text": "", "is_credits": False} for i in range(30)]
    g1, b1, r1 = PLUS.scene_budget(
        {"scene": 1, "phase": "climax", "first_panel": 1, "last_panel": 30},
        talky, CHAPTER_MAP)
    g2, b2, r2 = PLUS.scene_budget(
        {"scene": 9, "phase": "setup", "first_panel": 90, "last_panel": 120},
        silent, CHAPTER_MAP)
    check("a dense capped scene EARNS extra words", g1 > b1 and bool(r1))
    check("...and the reasons are recorded so the grant can be audited",
          all(isinstance(x, str) and x for x in r1))
    check("a capped scene with no reason gets NO allowance", g2 == b2 and not r2)
    check("the allowance is hard-capped", g1 <= PLUS.DENSE_HARD_CAP)

    # =============================== OCR poisoning defence
    trusted = {"ocr_text": "hello", "visual_description": "a thing",
               "ocr_confidence": 0.9, "desc_confidence": 0.9}
    poisoned = {"ocr_text": "hello", "visual_description": "a thing",
                "ocr_confidence": 0.1, "desc_confidence": 0.9}
    wo_t, _ = PLACE.panel_weights(trusted)
    wo_p, wd_p = PLACE.panel_weights(poisoned)
    check("a trusted panel keeps production's OCR weight",
          abs(wo_t - PLACE.BASE_OCR_W) < 1e-6)
    check("a panel whose OCR the reader distrusts is scored on its description",
          wo_p < wo_t and wd_p > wo_p)
    check("a panel with no OCR at all does not score on OCR",
          PLACE.panel_weights({"ocr_text": "", "visual_description": "d"})[0] == 0.0)
    check("a panel weak on BOTH signals is flagged, not silently trusted",
          PLACE.is_uncertain({"ocr_text": "", "visual_description": "d",
                              "desc_confidence": 0.2}))

    # =============================== placement is a solver, not a prompt
    panels = [
        {"panel_id": "p0", "ocr_text": "SCANS discord.gg", "is_credits": True,
         "visual_description": "A credits page.", "width": 400, "height": 600},
        {"panel_id": "p1", "ocr_text": "Who are you",
         "visual_description": "A swordsman draws his blade in a courtyard.",
         "width": 400, "height": 600},
        {"panel_id": "p2", "ocr_text": "I am your brother",
         "visual_description": "A hooded rival lowers his hood revealing a scar.",
         "width": 400, "height": 600},
        {"panel_id": "p3", "ocr_text": "",
         "visual_description": "A wide shot of a burning castle at night.",
         "width": 400, "height": 600},
    ]
    beats = [
        {"index": 0, "text": "The swordsman drew his blade in the courtyard.",
         "start": 0, "end": 4, "scene_id": 0},
        {"index": 1, "text": "The rival lowered his hood, revealing a scar.",
         "start": 4, "end": 8, "scene_id": 0},
        {"index": 2, "text": "Behind them the castle burned through the night.",
         "start": 8, "end": 13, "scene_id": 1},
    ]
    a, diag = PLACE.place(beats, panels)
    check("placement assigns every beat", len(a) == len(beats))
    check("placement is monotonic — the story never runs backwards",
          [x["panel_index"] for x in a]
          == sorted(x["panel_index"] for x in a))
    check("a credits panel is never given a narration line",
          all(x["panel_index"] != 0 for x in a))
    check("junk panels are counted", diag["junk_panels"] == 1)
    _, repairs = PLACE.enforce_monotonic(a)
    check("a correct solution needs no order repair", repairs == [])
    broken = [{"beat_index": 0, "panel_index": 5},
              {"beat_index": 1, "panel_index": 2}]
    fixed, rep = PLACE.enforce_monotonic(broken)
    check("an inversion from ANY source is repaired", len(rep) == 1
          and fixed[1]["panel_index"] == 5)
    check("...and the repair is reported, not silent",
          "repaired_from" in fixed[1])

    # =============================== provenance: soft, with evidence
    wrong = {0: {1}, 1: {1}, 2: {1}}       # deliberately wrong grouping
    a2, d2 = PLACE.place(beats, panels, allowed=wrong)
    check("a beat can escape a WRONG scene grouping when evidence is strong",
          len(d2["provenance_escapes"]) > 0)
    check("...and every escape reports its margin so the grouping can be fixed",
          all("margin" in e for e in d2["provenance_escapes"]))
    right = {0: {1}, 1: {2}, 2: {3}}
    a3, d3 = PLACE.place(beats, panels, allowed=right)
    check("a correct grouping produces no escapes",
          d3["provenance_escapes"] == [])

    # =============================== splitter coverage validation
    import numpy as np
    from PIL import Image
    page = np.full((900, 400), 255, dtype=np.uint8)
    page[100:300, :] = 0
    page[500:800, :] = 0
    cov_full, bands_full = LAB._measure_coverage(page, 255,
                                                 [[0, 0, 400, 900]])
    cov_half, bands_half = LAB._measure_coverage(page, 255,
                                                 [[0, 0, 400, 350]])
    check("full coverage measures as full", cov_full > 0.99)
    check("a splitter that misses art is MEASURED as missing it",
          cov_half < 0.6)
    check("...and the missed bands are located for recovery", bands_half)
    rec = LAB._recover_bands(page, 255, bands_half, 400)
    check("missed bands are recoverable geometrically", len(rec) > 0)

    tall = LAB._split_tall(page, 255, [0, 0, 100, 900], 400)
    check("a tall strip is split rather than shipped whole", len(tall) > 1)
    short = LAB._split_tall(page, 255, [0, 0, 400, 400], 400)
    check("a normal panel is left alone", len(short) == 1)

    # =============================== FULL RUN
    base = baseline_project(os.path.join(tmp, "baseline"))
    before = fingerprint(base)

    proj = make_project(os.path.join(tmp, "projects", "demo-lab-claude"), n=6)
    LAB.PROJECTS = os.path.join(tmp, "projects")
    state = {"describe": good_panel, "crop": good_crop,
             "chapter_map": CHAPTER_MAP, "issues": []}
    stub = StubClient(make_router(state))
    validator._client = lambda: stub

    import claude_lab
    orig_lab_dir = claude_lab.lab_dir
    claude_lab.lab_dir = lambda url, sp: proj

    man = LAB.run_lab("https://example.com/comics/demo/chapter/1",
                      splitter="claude", voice=False)
    check("a full Claude+ run completes", man["status"] == "ok")
    if man["status"] != "ok":
        print("   error:", man.get("error"))
    check("the production baseline is byte-identical afterwards",
          fingerprint(base) == before)
    check("the run is stamped as the claude+ pipeline",
          man.get("pipeline") == "claude+")

    # ---- stage order: the whole point of this batch
    passes = man.get("passes", {})
    check("every Claude+ stage ran",
          all(k in passes for k in ("read", "map", "script", "critique",
                                    "revise", "place", "crop")))
    crop_prompts = state.get("crop_prompts", [])
    check("the crop stage ran at all", bool(crop_prompts))
    check("THE ORDERING FIX: the crop prompt carries the line it frames for",
          any("the line that plays over this panel" in p for p in crop_prompts))
    check("...and that line is real narration, not a placeholder",
          any("swordsman" in p or "stranger" in p for p in crop_prompts))

    # ---- the script contract reached the model
    sp = state.get("script_prompts", [])
    # The budget is now a RANGE, not a ceiling: measured spend was 21-36% on
    # large scenes because "compress ruthlessly" drives to the floor while a
    # ceiling only caps the top. A floor is what was missing.
    check("the script prompt carries a computed budget as a TARGET RANGE",
          any("TARGET RANGE" in p for p in sp))
    check("...and says that coming in under it is a failure, not economy",
          any("failure, not economy" in p for p in sp))
    check("...and the density rule", "DENSITY IS EDITORIAL" in PLUS.SCRIPT_SYSTEM)
    check("...and the ban on camera/panel language",
          "never the word \\\"camera\\\"" in PLUS.SCRIPT_SYSTEM
          or "camera" in PLUS.SCRIPT_SYSTEM)
    check("...and the reported-speech requirement",
          "NEVER use quotation marks" in PLUS.SCRIPT_SYSTEM)
    check("...and dialogue fidelity",
          "DIALOGUE FIDELITY" in PLUS.SCRIPT_SYSTEM)

    # ---- outputs
    with open(os.path.join(proj, "segments.json"), encoding="utf-8") as f:
        segs = json.load(f)
    check("the run produces render segments", len(segs) > 0)
    check("segments tile with no gaps",
          all(abs(x["end"] - y["start"]) < 0.01 for x, y in zip(segs, segs[1:])))
    check("segments carry the crop Claude chose for the line",
          all(s.get("focus_source") == "claude+" for s in segs))
    check("segments are born unticked, as ingest leaves them",
          all("user_included" not in s for s in segs))
    check("the project is stamped as a lab experiment",
          json.load(open(os.path.join(proj, "project.json")))["lab"] is True)

    descs = json.load(open(os.path.join(proj, "descriptions.json")))
    check("panels carry an OCR confidence for the scorer to use",
          all("ocr_confidence" in d for d in descs))
    check("...and a needs_review marker", all("needs_review" in d for d in descs))
    check("...and the contract audit result",
          all("contract_violations" in d for d in descs))
    check("the credits panel was identified",
          any(d.get("is_credits") for d in descs))

    diagn = man.get("diagnostics", {})
    check("placement diagnostics are kept for audit", "placement" in diagn)
    check("...including order repairs", "order_repairs" in diagn["placement"])
    check("...and ambiguous pairs worth a human look",
          "ambiguous" in diagn["placement"])

    # ---- the board and the checker can read the result
    rows = validator.build_rows(proj)
    check("the board can read a Claude+ project like any other", len(rows) > 0)
    findings = validator.rule_findings(rows)
    check("the checker validates Claude+ output", isinstance(findings, list))

    # =============================== critique only revises what it flagged
    state["issues"] = [{"unit": 0, "type": "over_compression",
                        "problem": "two exchanges collapsed",
                        "fix": "give each its own sentence"}]
    state["revised"] = []
    units = [{"scene_id": 0, "text": "draft zero", "panel_numbers": [1, 2],
              "scene": 1, "budget": 120, "words": 2},
             {"scene_id": 1, "text": "draft one", "panel_numbers": [3, 4],
              "scene": 1, "budget": 120, "words": 2}]
    issues, _ = PLUS.critique(units, descs)
    out, rst = PLUS.revise(units, issues, descs, CHAPTER_MAP)
    check("critique returns typed issues",
          issues and issues[0]["type"] == "over_compression")
    check("revision regenerates ONLY the flagged unit", rst["revised"] == 1)
    check("...leaving the unflagged unit untouched",
          out[1]["text"] == "draft one")
    check("...and records why it was revised", "revised_for" in out[0])
    check("no issues means no revision calls",
          PLUS.revise(units, [], descs, CHAPTER_MAP)[1]["calls"] == 0)

    # =============================== crop floor + full-frame
    state["crop"] = lambda n: {"n": n, "crop": [0.4, 0.4, 0.45, 0.45],
                               "framing": "close_up", "reason": "sliver",
                               "confidence": 0.9}
    placements = [{"panel": {"panel_id": d["panel_id"], "n": d["n"],
                             "file": d["file"]},
                   "line": "a line", "prev": "", "next": ""}
                  for d in descs[:2]]
    got, st = PLUS.plan_crops(proj, placements)
    check("a sliver crop is refused and falls back to the full panel",
          all(v["crop_bbox_norm"] == [0.0, 0.0, 1.0, 1.0]
              for v in got.values()))
    check("...and the refusal is reported", st["rejected"])

    state["crop"] = lambda n: {"n": n, "crop": [0.2, 0.2, 0.9, 0.9],
                               "framing": "medium", "reason": "ok",
                               "confidence": 0.2}
    got2, st2 = PLUS.plan_crops(proj, placements)
    check("a low-confidence crop falls back to the full panel too",
          all(v["crop_bbox_norm"] == [0.0, 0.0, 1.0, 1.0]
              for v in got2.values()))

    state["crop"] = lambda n: {"n": n, "crop": [0.0, 0.0, 1.0, 1.0],
                               "framing": "full",
                               "reason": "whole composition carries the line",
                               "confidence": 0.9}
    got3, st3 = PLUS.plan_crops(proj, placements)
    check("a deliberate full-frame choice is honoured, not treated as failure",
          st3["full_frame"] == len(got3) and not st3["rejected"])

    check("a panel nothing plays over costs no crop call",
          PLUS.plan_crops(proj, [])[1]["calls"] == 0)

    # ================= script granularity, budget range, world-building
    check("the chapter map asks for scenes of four to six panels",
          "FOUR TO SIX PANELS" in PLUS.CHAPTER_SYSTEM)
    check("...and to split at sub-beats rather than by location",
          "sub-beat" in PLUS.CHAPTER_SYSTEM
          and "two or three scenes" in PLUS.CHAPTER_SYSTEM)
    check("the script prompt carries world-building rules",
          "WORLD-BUILDING" in PLUS.SCRIPT_SYSTEM)
    check("...naming factions, ranks, power-system and stakes",
          all(w in PLUS.SCRIPT_SYSTEM for w in
              ("factions", "power system", "stakes")))
    check("...and forbidding invention of what the panels do not show",
          "never invent a rank" in PLUS.SCRIPT_SYSTEM)
    check("...and forbidding an exposition dump",
          "exposition dump" in PLUS.SCRIPT_SYSTEM)
    check("critique can raise missing world-building",
          "missing_worldbuilding" in PLUS.CRITIQUE_SYSTEM)
    check("...only when the panel facts carry it, never speculatively",
          "never because the narration" in PLUS.CRITIQUE_SYSTEM)
    check("critique can raise over-compression",
          "over_compression" in PLUS.CRITIQUE_SYSTEM)

    # ---- under-spend is a FAILURE state, raised in code
    rich = {"scene_id": 0, "panel_numbers": [1, 2, 3, 4, 5, 6],
            "panel_ids": [], "budget": 200, "words": 40, "spend_frac": 0.2,
            "dialogue_lines": 4, "under_spent": True, "scene": 1}
    thin = {"scene_id": 1, "panel_numbers": [1], "panel_ids": [],
            "budget": 40, "words": 18, "spend_frac": 0.45,
            "dialogue_lines": 0, "under_spent": False, "scene": 2}
    iss = PLUS.underspend_issues([rich, thin])
    check("a content-rich scene written far under its range is flagged",
          len(iss) == 1 and iss[0]["unit"] == 0)
    check("...as over-compression, so it goes back for a rewrite",
          iss[0]["type"] == "over_compression")
    check("...telling the writer the range to hit",
          "-" in iss[0]["fix"] and "words" in iss[0]["fix"])
    check("a genuinely thin scene is NOT flagged",
          all(i["unit"] != 1 for i in iss))
    check("the target range is a floor as well as a ceiling",
          0 < PLUS.TARGET_MIN_FRAC < PLUS.TARGET_MAX_FRAC <= 1.0)

    # ================= visual progression (the folding fix)
    import claude_place as PL

    def _p(pid, page, desc, subj="character"):
        return {"panel_id": pid, "page": page, "visual_description": desc,
                "subject_type": subj, "ocr_text": "x", "width": 400,
                "height": 600}

    descs_v = [
        _p("page001_panel_001", "page001", "Raising his blade, the swordsman lunges."),
        _p("page002_panel_001", "page002", "Recoiling, the rival stumbles back bleeding."),
        _p("page003_panel_001", "page003", "Burning, the castle collapses behind them."),
        _p("page004_panel_001", "page004", "Kneeling in ash, the survivor weeps."),
    ]
    beats_v = [{"index": 0, "text": "The duel ended badly.", "start": 0.0,
                "end": 12.0, "scene_id": 0}]
    assigns_v = [{"beat_index": 0, "panel_index": 0}]
    unit_panels = {0: [0, 1, 2, 3]}

    slots, prog = PL.expand_units(assigns_v, beats_v, descs_v, unit_panels)
    check("one line over four distinct panels yields several visual segments",
          len(slots) > 1)
    check("...recovering panels that would otherwise be folded away",
          prog["panels_recovered"] >= 1)
    check("...and each slot clears the no-flicker floor",
          all(sl["end"] - sl["start"] >= PL.MIN_VISUAL_SEC - 0.01
              for sl in slots))
    check("...tiling the unit's own window without gaps",
          abs(slots[0]["start"] - 0.0) < 0.01
          and abs(slots[-1]["end"] - 12.0) < 0.01)
    check("...in reading order, so the story still runs forwards",
          [sl["panel_index"] for sl in slots]
          == sorted(sl["panel_index"] for sl in slots))
    check("a wide unit is reported for the board",
          prog["wide_units"])

    # Near-duplicates must NOT be exploded into a strobe.
    dupes = [_p(f"page001_panel_{i:03d}", "page001",
                "Raising his blade, the swordsman lunges forward.")
             for i in range(1, 6)]
    slots_d, prog_d = PL.expand_units(
        [{"beat_index": 0, "panel_index": 0}],
        [{"index": 0, "text": "He lunged.", "start": 0.0, "end": 12.0,
          "scene_id": 0}], dupes, {0: [0, 1, 2, 3, 4]})
    check("near-identical panels are not exploded into a strobe",
          len(slots_d) < 5)

    # A short window cannot fit many panels — capacity is respected, not
    # silently exceeded.
    slots_s, prog_s = PL.expand_units(
        [{"beat_index": 0, "panel_index": 0}],
        [{"index": 0, "text": "Fast.", "start": 0.0, "end": 2.0,
          "scene_id": 0}], descs_v, unit_panels)
    check("a short narration window does not manufacture flicker",
          all(sl["end"] - sl["start"] >= PL.MIN_VISUAL_SEC - 0.01
              for sl in slots_s))
    check("...and says it could not fit them all",
          prog_s["capacity_limited"])

    # =============================== a dying stage still reports its spend
    # The estate-developer run's card read "19 Claude calls · $0.1432" while its
    # read stage had actually completed ~206 panels. The manifest is only
    # updated when a stage FINISHES, so a stage cut short by a cap reported
    # none of what it spent. The usage ledger was right the whole time; the
    # run's own card understated it by an order of magnitude.
    seen = {"cost": []}

    def _watch(partial):
        seen["cost"].append(shared_tally.cost)

    shared_tally = CP._Tally()
    # Real crop filenames — a name that is not on disk makes _image_block
    # return None, the batch is skipped, and on_batch never fires.
    _crops = sorted(f for f in os.listdir(os.path.join(proj, "crops"))
                    if f.endswith(".png"))
    panels_in2 = [{"panel_id": os.path.splitext(f)[0], "file": f,
                   "width": 400, "height": 500, "_n": i}
                  for i, f in enumerate(_crops, start=1)]
    state3 = {"describe": good_panel, "crop": good_crop,
              "chapter_map": CHAPTER_MAP, "issues": []}
    validator._client = lambda: StubClient(make_router(state3))
    _ppc = PLUS.PANELS_PER_CALL
    PLUS.PANELS_PER_CALL = 2          # force several batches from the fixture
    try:
        PLUS.describe_plus(proj, panels_in2, on_batch=_watch,
                           tally=shared_tally)
    finally:
        PLUS.PANELS_PER_CALL = _ppc
    check("the caller can read the running cost while a read is still going",
          len(seen["cost"]) > 1 and seen["cost"][0] > 0)
    check("...and it rises batch by batch, so a stage cut short still reports",
          seen["cost"][-1] > seen["cost"][0])

    # =============================== fresh must mean fresh
    # Clearing only pages/crops left descriptions.json in place, and the read
    # stage resumes from it — so a re-run after the contract changed would skip
    # every panel as "already read" and reuse the old reads.
    import claude_pipeline as _cp
    _cp._write(proj, "descriptions.json", [{"panel_id": "stale", "n": 1,
                                            "visual_description": "old read"}])
    with open(os.path.join(proj, "script.json"), "w", encoding="utf-8") as f:
        json.dump([{"scene_id": 0, "text": "stale", "panel_ids": []}], f)
    state2 = {"describe": good_panel, "crop": good_crop,
              "chapter_map": CHAPTER_MAP, "issues": []}
    validator._client = lambda: StubClient(make_router(state2))
    # fresh=True re-runs the scrape, which must not touch the network here.
    # The pages are already on disk, so the stub just reports them.
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    import types as _types
    _fake_scraper = _types.ModuleType("scraper")
    _fake_scraper.LAST_WARNING = ""
    _fake_scraper.download_chapter = lambda url, out: sorted(
        os.path.join(out, f) for f in os.listdir(out) if not f.startswith("_"))
    sys.modules["scraper"] = _fake_scraper
    LAB.run_lab("https://example.com/comics/demo/chapter/1",
                splitter="claude", voice=False, fresh=True)
    after_fresh = _cp.read(proj, "descriptions.json") or []
    check("a fresh run does NOT reuse stale reads",
          not any(d.get("panel_id") == "stale" for d in after_fresh))
    check("...and re-reads the panels under the current contract",
          bool(after_fresh) and all("ocr_confidence" in d for d in after_fresh))

    # =============================== resume after interruption
    full = CP.read(proj, "descriptions.json")
    CP._write(proj, "descriptions.json", full[:2])
    stub.messages.calls.clear()
    panels_in = [{"panel_id": d["panel_id"], "file": d["file"],
                  "width": 400, "height": 500, "_n": d["n"]} for d in full]
    prior = CP.read(proj, "descriptions.json", [])
    done = {d["panel_id"] for d in prior}
    _, rst2 = PLUS.describe_plus(proj, panels_in, skip=done)
    check("an interrupted read resumes rather than starting over",
          rst2["resumed"] == 2)
    check("...and does not re-send the panels already read",
          all("PANEL 1:" not in json.dumps(c.get("messages", []))
              for c in stub.messages.calls))

    claude_lab.lab_dir = orig_lab_dir

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
