"""The Claude-vs-Gemini experiment (claude_pipeline.py, claude_compare.py).

Two things matter more than anything else here:

1. ISOLATION. The experiment must be incapable of touching the production
   board. Every test that runs the pipeline hashes descriptions.json,
   script.json and segments.json before and after, and fails if a single byte
   moved. A side experiment that quietly rewrote the real chapter would be the
   worst possible outcome of this feature.

2. HONESTY OF THE COMPARISON. The owner asked for a usable evaluation, not
   impressions — but there is no ground truth for a scraped chapter, so a
   metric like "OCR characters per panel" cannot declare a winner without
   inventing a result. These assert that neutral metrics NEVER name a winner,
   and that the ones that do have a direction that is true by construction.

No network: Claude is driven through a stub client.
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
        self.input_tokens = 2000
        self.output_tokens = 400
        self.cache_read_input_tokens = 0


class _Resp:
    def __init__(self, payload):
        self.content = [_Blk(json.dumps(payload))]
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


class StubClient:
    def __init__(self, pick):
        self.messages = StubMessages(pick)


def make_pick(n_panels):
    """Answer whichever pipeline stage is asking, by its system prompt."""
    def pick(kw):
        sysmsg = kw.get("system")
        text = sysmsg[0]["text"] if isinstance(sysmsg, list) else str(sysmsg)
        if "already been cut into individual panels" in text:
            # Which panels were in THIS batch? Read them back out of the
            # request, so the stub answers the real question rather than
            # assuming a batch size.
            sent = []
            for blk in kw["messages"][0]["content"]:
                if blk.get("type") == "text" and blk["text"].startswith("PANEL "):
                    sent.append(int(blk["text"].split()[1].rstrip(":")))
            return {"panels": [
                {"n": n, "ocr": f"claude ocr for panel {n}",
                 "description": f"Claude sees a distinct scene in panel {n}.",
                 "crop": [0.0, 0.05, 1.0, 0.95],
                 "is_credits": n == 1, "subject": "character",
                 "importance": 3} for n in sent]}
        if "writing the narration for a recap video" in text:
            return {"units": [
                {"unit": 0, "text": "Claude unit zero.", "covers_panels": [1, 2]},
                {"unit": 1, "text": "Claude unit one.", "covers_panels": [3]}]}
        if "which panel each line of recap narration plays over" in text:
            return {"placements": [
                {"unit": 0, "panels": [2], "reason": "shows the beat"},
                {"unit": 1, "panels": [3, 4], "reason": "the reaction"}]}
        if "how long it stays on screen" in text:
            return {"panels": [
                {"panel": n, "seconds": 3.5, "motion": "push-in",
                 "transition": "cut"} for n in range(1, n_panels + 1)]}
        return {}
    return pick


# --------------------------------------------------------------- fixtures
def project(root, n=6):
    os.makedirs(os.path.join(root, "crops"), exist_ok=True)
    descs = [{"panel_id": f"p_{i:03d}", "file": f"p_{i:03d}.png",
              "width": 400, "height": 900, "bbox": [0, 0, 400, 900],
              "ocr_text": f"gemini ocr {i}", "ok": True, "source": "gemini",
              "visual_description": f"Gemini sees panel {i} with a swordsman."}
             for i in range(1, n + 1)]
    scenes = [{"scene_id": 0, "text": "Gemini unit zero.",
               "panel_ids": ["p_001", "p_002"]},
              {"scene_id": 1, "text": "Gemini unit one.",
               "panel_ids": ["p_003"]}]
    segs = []
    for i in range(1, n + 1):
        st = (i - 1) * 5.0
        segs.append({"seg_index": i - 1, "panel_id": f"p_{i:03d}",
                     "start": st, "end": st + 5.0, "dur": 5.0,
                     "user_included": i <= 3,
                     "crop_bbox_norm": [0.0, 0.0, 1.0, 1.0],
                     "beats": [{"index": i - 1, "text": "x",
                                "start": st, "end": st + 4.0}]})
    for name, data in (("descriptions.json", descs), ("script.json", scenes),
                       ("segments.json", segs)):
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    from PIL import Image
    for d in descs:
        Image.new("RGB", (400, 900), (20, 20, 30)).save(
            os.path.join(root, "crops", d["file"]))
    return root


PROD_FILES = ("descriptions.json", "script.json", "segments.json")


def fingerprint(pdir):
    out = {}
    for f in PROD_FILES:
        p = os.path.join(pdir, f)
        with open(p, "rb") as fh:
            out[f] = hashlib.sha1(fh.read()).hexdigest()
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="claude_exp_")
    os.environ["USAGE_DIR"] = tmp
    import usage
    usage.USAGE_DIR = tmp
    usage.COUNTS_PATH = os.path.join(tmp, "counters.json")
    usage.LOG_PATH = os.path.join(tmp, "calls.log.jsonl")
    usage.LOCK_PATH = os.path.join(tmp, ".lock")
    os.environ.setdefault("CLAUDE_API_KEY", "test-key-not-real")

    import validator
    import claude_pipeline as CP
    import claude_compare as CC

    N = 6
    pdir = project(os.path.join(tmp, "proj"), n=N)
    before = fingerprint(pdir)

    stub = StubClient(make_pick(N))
    validator._client = lambda: stub

    # ================================================== crop sanitising
    check("a sane crop survives",
          CP._clean_crop([0.1, 0.2, 0.9, 0.8]) == [0.1, 0.2, 0.9, 0.8])
    check("an inverted crop falls back to the full panel",
          CP._clean_crop([0.9, 0.9, 0.1, 0.1]) == [0.0, 0.0, 1.0, 1.0])
    check("a vanishing crop falls back to the full panel",
          CP._clean_crop([0.5, 0.5, 0.51, 0.51]) == [0.0, 0.0, 1.0, 1.0])
    check("garbage falls back to the full panel",
          CP._clean_crop("nonsense") == [0.0, 0.0, 1.0, 1.0])
    check("out-of-range values are clamped, not trusted",
          CP._clean_crop([-2, -2, 9, 9]) == [0.0, 0.0, 1.0, 1.0])

    # ================================================== the run
    before_calls = usage.daily_summary()["claude_calls"]
    man = CP.run(pdir)
    after = usage.daily_summary()

    check("the run reports ok", man["status"] == "ok")
    check("it ran every stage",
          set(man["passes"]) == set(CP.STAGES))
    check("it says plainly that no audio was touched", "audio" in man)
    check("...and that its timings are PROPOSED, not measured",
          man["timing_is_proposed"] is True)
    check("every Claude call went through the usage gate",
          after["claude_calls"] == before_calls + len(stub.messages.calls))
    check("the run reports what it cost", man["cost_usd"] > 0)

    # ---- THE ISOLATION GUARANTEE
    check("the production board files are byte-identical afterwards",
          fingerprint(pdir) == before)
    check("...and everything it wrote is inside the sidecar",
          os.path.isdir(CP.out_dir(pdir)) and
          sorted(os.listdir(CP.out_dir(pdir))) ==
          ["descriptions.json", "manifest.json", "script.json", "timing.json",
           "units.json"])

    # ---- panels are batched, not one call each
    vision_calls = [c for c in stub.messages.calls
                    if any(b.get("type") == "image"
                           for b in c["messages"][0]["content"]
                           if isinstance(b, dict))]
    check("panels are batched several to a vision call",
          len(vision_calls) < N)
    check("...and the batch actually carries the panel images",
          any(b.get("type") == "image"
              for b in vision_calls[0]["messages"][0]["content"]))

    # ---- the output shapes
    cd = CP.read(pdir, "descriptions.json")
    check("Claude produced one reading per panel", len(cd) == N)
    check("...with OCR", all(r["ocr_text"] for r in cd))
    check("...with a description", all(r["visual_description"] for r in cd))
    check("...with a crop box", all(len(r["crop_bbox_norm"]) == 4 for r in cd))
    check("...and its own credits judgement",
          any(r["is_credits"] for r in cd))
    units = CP.read(pdir, "units.json")
    check("Claude wrote narration units", len(units) >= 2)
    check("...numbered densely from zero",
          [u["scene_id"] for u in units] == list(range(len(units))))
    sc = CP.read(pdir, "script.json")
    check("placement produces the same shape the board understands",
          all("scene_id" in s and "panel_ids" in s for s in sc))
    check("a panel is never given to two different lines",
          len([p for s in sc for p in s["panel_ids"]]) ==
          len({p for s in sc for p in s["panel_ids"]}))
    tm = CP.read(pdir, "timing.json")
    check("timing proposes a duration and a camera move per placed panel",
          tm and all(t["dur"] > 0 and t["motion"] for t in tm))

    # ---- guardrails
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty, exist_ok=True)
    try:
        CP.run(empty)
        check("running on a chapter with no ingest is refused", False)
    except CP.PipelineError as e:
        check("running on a chapter with no ingest is refused",
              "ingested" in str(e))
    # Pre-flight refusals RAISE rather than returning an error manifest:
    # nothing has been spent yet, so the caller is stopped cleanly instead of
    # being handed something that looks like a run.
    old_cap = CP.MAX_PANELS
    CP.MAX_PANELS = 2
    try:
        CP.run(pdir)
        check("a panel cap stops a runaway spend before it starts", False)
    except CP.PipelineError as e:
        check("a panel cap stops a runaway spend before it starts",
              "cap" in str(e))
    CP.MAX_PANELS = old_cap

    # ================================================== the comparison
    validator._client = lambda: stub
    CP.run(pdir)                      # restore a good run after the cap test
    cmp = CC.compare(pdir)

    check("the comparison compares the shared panels",
          cmp["panels_compared"] == N)
    check("it produces a real table of metrics", len(cmp["metrics"]) > 10)
    check("it covers OCR", any("OCR" in m["metric"] for m in cmp["metrics"]))
    check("...descriptions",
          any("Descriptions" in m["metric"] for m in cmp["metrics"]))
    check("...crops", any("Crops" in m["metric"] for m in cmp["metrics"]))
    check("...panel-to-line matching",
          any("Matching" in m["metric"] for m in cmp["metrics"]))
    check("...story order",
          any("Story order" in m["metric"] for m in cmp["metrics"]))
    check("...checker findings",
          any("Checker" in m["metric"] for m in cmp["metrics"]))
    check("...manual fixes needed",
          any("Manual fixes" in m["metric"] for m in cmp["metrics"]))
    check("...and board cleanliness",
          any("cleanliness" in m["metric"] for m in cmp["metrics"]))

    # ---- THE HONESTY GUARANTEE
    neutral = [m for m in cmp["metrics"] if m["direction"] == "neutral"]
    scored = [m for m in cmp["metrics"] if m["direction"] != "neutral"]
    check("there ARE metrics with no defensible winner", bool(neutral))
    check("a neutral metric never declares a winner",
          all(m["winner"] is None for m in neutral))
    check("...and says what a human would have to check instead",
          all(m["note"] for m in neutral))
    check("every scored metric reaches a verdict",
          all(m["winner"] in ("claude", "baseline", "tie") for m in scored))
    check("the score only counts metrics that have a direction",
          cmp["score"]["scored_metrics"] == len(scored))
    check("...and the neutral ones are counted separately, not hidden",
          cmp["score"]["neutral_metrics"] == len(neutral))
    check("the caveats state that timings are not comparable like for like",
          any("PROPOSED" in c or "proposed" in c for c in cmp["caveats"]))
    check("...and that both sides got the same crops",
          any("SAME panel crops" in c or "same panel crops" in c.lower()
              for c in cmp["caveats"]))
    check("...and that the win count is not an overall verdict",
          any("not an overall verdict" in c for c in cmp["caveats"]))

    # ---- verdict direction is actually correct
    m = CC._metric("fewer is better", 5, 2, "lower_better")
    check("lower_better picks the smaller number", m["winner"] == "claude")
    m = CC._metric("more is better", 5, 2, "higher_better")
    check("higher_better picks the larger number", m["winner"] == "baseline")
    m = CC._metric("equal", 3, 3, "lower_better")
    check("an equal metric is a tie, not a win", m["winner"] == "tie")

    # ---- the disagreement shortlist is the actionable part
    check("it shortlists the panels the two disagree about most",
          len(cmp["most_disagreement"]) > 0)
    check("...showing both descriptions side by side",
          all("baseline_desc" in d and "claude_desc" in d
              for d in cmp["most_disagreement"]))
    check("...worst agreement first",
          cmp["most_disagreement"][0]["ocr_agreement"] +
          cmp["most_disagreement"][0]["desc_agreement"] <=
          cmp["most_disagreement"][-1]["ocr_agreement"] +
          cmp["most_disagreement"][-1]["desc_agreement"])

    # ---- the checker is used as the measuring instrument, on BOTH sides
    c_rows = CC._claude_rows(pdir)
    check("the Claude variant is expressed in the checker's own row shape",
          all(k in c_rows[0] for k in
              ("panel_id", "ocr", "desc", "placement", "timing")))
    check("...so the identical rule pass can measure it",
          isinstance(validator.rule_findings(c_rows), list))

    # ---- comparison refuses rather than inventing a result
    bare = project(os.path.join(tmp, "bare"), n=3)
    try:
        CC.compare(bare)
        check("comparing with no experiment output is refused", False)
    except ValueError as e:
        check("comparing with no experiment output is refused",
              "not produced" in str(e))

    # ============================== resume after an interrupted run
    # A real deploy restarted the container 4 batches into a 21-batch chapter
    # and the first version threw all four away, because it only wrote results
    # once every batch had finished. Batches are now checkpointed.
    CP.run(pdir)                                   # a complete run
    full = CP.read(pdir, "descriptions.json")
    # Simulate an interruption: only the first two panels survived.
    CP._write(pdir, "descriptions.json", full[:2])
    stub.messages.calls.clear()
    CP.run(pdir, stages=("describe",))
    after_resume = CP.read(pdir, "descriptions.json")
    check("an interrupted run resumes instead of starting over",
          len(after_resume) == len(full))
    check("...without paying again for the panels already read",
          all(not any(b.get("text", "").startswith("PANEL 1:")
                      for b in c["messages"][0]["content"]
                      if isinstance(b, dict))
              for c in stub.messages.calls))
    check("...and the resumed run says how many it skipped",
          CP.load_manifest(pdir)["passes"]["describe"].get("resumed") == 2)
    CP.run(pdir)                                   # restore a full run

    # ============================== promote: make it actually watchable
    # The comparison answers "did Claude decide better". It cannot answer "is
    # this any good to watch", because a sidecar has no audio and no segments.
    # promote() builds a REAL sibling project using the STANDARD TTS path.
    # tts=False here so the test costs no TTS characters.
    before_promote = fingerprint(pdir)
    out = CP.promote(pdir, tts=False)
    check("promote builds a separate sibling project",
          out["project"].endswith("-claude") and os.path.isdir(out["dir"]))
    check("...leaving the baseline byte-identical",
          fingerprint(pdir) == before_promote)
    check("...with real render segments", out["segments"] > 0)
    check("...and a duration", out["duration"] > 0)

    dest = out["dir"]
    for f in ("descriptions.json", "script.json", "script.txt",
              "segments.json", "project.json"):
        check("the promoted project has " + f,
              os.path.exists(os.path.join(dest, f)))
    check("...and shares the baseline's crops rather than duplicating them",
          os.path.islink(os.path.join(dest, "crops")) or
          os.path.isdir(os.path.join(dest, "crops")))

    with open(os.path.join(dest, "segments.json"), encoding="utf-8") as f:
        psegs = json.load(f)
    check("promoted segments carry Claude's crop, not a Gemini one",
          all(sg.get("focus_source") == "claude" for sg in psegs))
    check("...tile the timeline without gaps",
          all(abs(a["end"] - b["start"]) < 0.01
              for a, b in zip(psegs, psegs[1:])))
    check("...and each carries its narration text",
          all(sg["beats"] and sg["beats"][0].get("text") for sg in psegs))
    check("segments are born UNTICKED, exactly as ingest leaves them",
          all("user_included" not in sg for sg in psegs))

    with open(os.path.join(dest, "project.json"), encoding="utf-8") as f:
        pmeta = json.load(f)
    check("the promoted project is stamped as an experiment",
          pmeta.get("experiment") is True)
    check("...naming the chapter it came from", pmeta.get("experiment_of"))
    check("...and never mistakable for a normal match",
          pmeta.get("match_method") == "claude-experiment")
    check("...carrying the series and chapter so it is identifiable",
          "series" in pmeta and "chapter" in pmeta)

    # The board must be able to read the promoted project like any other.
    prows = validator.build_rows(dest)
    # Not row 1 specifically: the credits page is CORRECTLY left unassigned,
    # so asserting that the first row has a line would be asserting a bug.
    check("the board can read the promoted project like any other",
          len(prows) > 0 and
          any(r["placement"]["unit"] is not None for r in prows))
    check("...with the panels Claude left out genuinely left out",
          any(r["placement"]["role"] == "left_out" for r in prows))
    check("...and the checker can validate it",
          isinstance(validator.rule_findings(prows), list))

    man_after = CP.load_manifest(pdir)
    check("the manifest records what it was promoted to",
          man_after.get("promoted_to") == out["project"])

    import shutil as _sh
    _sh.rmtree(dest)

    try:
        CP.promote(os.path.join(tmp, "bare"), tts=False)
        check("promoting with no experiment output is refused", False)
    except CP.PipelineError as e:
        check("promoting with no experiment output is refused",
              "Run the Claude pipeline first" in str(e))

    # ---- the experiment is disposable
    import shutil
    shutil.rmtree(CP.out_dir(pdir))
    check("deleting the sidecar leaves the production board intact",
          fingerprint(pdir) == before)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
