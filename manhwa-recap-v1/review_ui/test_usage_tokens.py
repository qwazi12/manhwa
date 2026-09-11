"""Token-metered cost accounting.

The ledger used to price every Gemini call at a flat rate regardless of size,
so an image-bearing describe call and a 40-token prompt cost the same. These
assert that reported tokens win over the flat estimate, that unreported calls
still work (so an un-updated call site cannot break billing or the pipeline),
and that the UI is told which rates produced the number.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    root = tempfile.mkdtemp(prefix="usage_tok_")
    os.environ["USAGE_DIR"] = root
    import usage
    usage.USAGE_DIR = root
    usage.COUNTS_PATH = os.path.join(root, "counters.json")
    usage.LOG_PATH = os.path.join(root, "calls.log.jsonl")
    usage.LOCK_PATH = os.path.join(root, ".lock")

    # ---- token pricing
    c = usage.token_cost("gemini-3.5-flash", 1_000_000, 0)
    check("1M flash input tokens price off the input rate, not per-call",
          abs(c - usage._price("PRICE_FLASH_IN_PER_1M", 0.30)) < 1e-9)
    out = usage.token_cost("gemini-3.5-flash", 0, 1_000_000)
    check("output tokens are priced separately from input", out > c)
    pro = usage.token_cost("gemini-3.1-pro", 1_000_000, 0)
    check("pro is priced above flash", pro > c)
    check("an unknown model still prices rather than crashing",
          usage.token_cost("something-else", 1000, 1000) > 0)

    # ---- the meter
    m = usage.Meter()
    check("a fresh meter has reported nothing", m.reported is False)
    m.tokens(100, 50)
    check("reporting marks it metered", m.reported and m.prompt_tokens == 100)
    m.tokens(10, 5)
    check("repeat reports accumulate (retries, multi-part calls)",
          m.prompt_tokens == 110 and m.output_tokens == 55)

    class FakeUM:
        prompt_token_count = 700
        candidates_token_count = 300
        cached_content_token_count = 20

    class FakeResp:
        usage_metadata = FakeUM()

    m2 = usage.Meter().from_response(FakeResp())
    check("from_response reads the SDK's usage_metadata",
          m2.prompt_tokens == 700 and m2.output_tokens == 300)
    check("...including cached tokens", m2.cached_tokens == 20)
    m3 = usage.Meter().from_response(object())
    check("a response with no usage_metadata is not an error",
          m3.reported is False)
    check("...and neither is None", usage.Meter().from_response(None).reported is False)

    # ---- gate: metered vs unmetered
    big = 500_000
    with usage.gate("gemini", 1, model="gemini-3.5-flash") as meter:
        meter.tokens(big, big)
    d1 = usage.daily_summary()
    metered_cost = d1["est_cost_usd"]
    expected = usage.token_cost("gemini-3.5-flash", big, big)
    check("a metered call is billed from its ACTUAL tokens",
          abs(metered_cost - expected) < 1e-6)
    check("...which is far above the old flat per-call figure",
          metered_cost > usage.EST_COST_PER_GEMINI_CALL_USD * 10)

    with usage.gate("gemini", 1, model="gemini-3.5-flash"):
        pass                      # a call site that never reports
    d2 = usage.daily_summary()
    delta = d2["est_cost_usd"] - metered_cost
    check("an UNMETERED call still bills, at the flat fallback",
          abs(delta - usage.EST_COST_PER_GEMINI_CALL_USD) < 1e-6)
    check("call counts are unaffected by metering", d2["gemini_calls"] == 2)

    # ---- the log carries the evidence
    rows = [json.loads(l) for l in open(usage.LOG_PATH) if l.strip()]
    check("the log records the token counts", rows[0]["prompt_tokens"] == big)
    check("...and flags which rows were metered",
          rows[0]["metered"] is True and rows[1]["metered"] is False)

    # ---- TTS is already exact (billed per character), so it must not change
    before = usage.daily_summary()["est_cost_usd"]
    with usage.gate("tts", 1000, model="chirp3-hd-charon"):
        pass
    after = usage.daily_summary()["est_cost_usd"]
    check("TTS is still priced per character",
          abs((after - before) - usage.EST_COST_PER_TTS_1K_CHARS_USD) < 1e-6)

    # ---- the UI must be able to say what it priced at
    rc = usage.rate_card()
    check("a rate card is exposed", "gemini_per_1m" in rc and rc["gemini_per_1m"])
    check("...saying whether these are defaults or configured",
          isinstance(rc["defaults"], bool))
    check("daily_summary carries the rates for the header",
          "rates" in usage.daily_summary())

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
