"""Real spend metering (2026-10-03).

From 2026-09-12 every Gemini call on the Interactions API (AQ. keys) was
logged with 0 tokens and $0.00: the reader looked for generateContent field
names. These pin the fix: both response shapes parse, thinking is billed as
output, a zero report is never recorded as free, prices are Google's published
ones, and the log carries the evidence. Temporary usage dir; no network."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
R = []


def check(name, ok):
    R.append((name, bool(ok)))


# The usage block of a REAL Interactions response (probe on Railway, 2026-10-03).
INTERACTIONS = {"service_tier": "standard", "steps": [], "usage": {
    "total_tokens": 66, "total_input_tokens": 8, "total_cached_tokens": 0,
    "total_output_tokens": 1, "total_tool_use_tokens": 0, "total_thought_tokens": 57}}
GENERATE = {"usageMetadata": {"promptTokenCount": 2250, "candidatesTokenCount": 500,
                              "thoughtsTokenCount": 285, "cachedContentTokenCount": 0}}


def main():
    root = tempfile.mkdtemp(prefix="meter_")
    import usage
    usage.USAGE_DIR = root
    usage.COUNTS_PATH = os.path.join(root, "counters.json")
    usage.LOG_PATH = os.path.join(root, "calls.log.jsonl")
    usage.LOCK_PATH = os.path.join(root, ".lock")

    u = usage.parse_gemini_usage(INTERACTIONS)
    check("Interactions shape: input read from total_input_tokens", u["prompt"] == 8)
    check("Interactions shape: thinking billed as output (1 + 57)", u["output"] == 58)
    check("Interactions shape: service tier recorded", u["tier"] == "standard")
    g = usage.parse_gemini_usage(GENERATE)
    check("generateContent shape: thinking added to output (500 + 285)",
          g["prompt"] == 2250 and g["output"] == 785)
    check("a response with no usage parses to nothing (not zeros)", usage.parse_gemini_usage({"steps": []}) == {})

    check("published 3.5 Flash input: $1.50 / 1M", abs(usage.token_cost("gemini-3.5-flash", 1_000_000, 0) - 1.50) < 1e-9)
    check("published 3.5 Flash output incl. thinking: $9.00 / 1M",
          abs(usage.token_cost("gemini-3.5-flash", 0, 1_000_000) - 9.00) < 1e-9)
    check("cached input billed at the $0.15 caching rate",
          abs(usage.token_cost("gemini-3.5-flash", 1_000_000, 0, cached_tokens=1_000_000) - 0.15) < 1e-9)
    check("Batch / Flex tier billed at half",
          abs(usage.token_cost("gemini-3.5-flash", 1_000_000, 0, tier="flex") - 0.75) < 1e-9)
    check("3.8 Flash $0.75 / $3.75 through 2026",
          abs(usage.token_cost("gemini-3.8-flash", 1_000_000, 1_000_000) - 4.50) < 1e-9)
    saved = usage.GEMINI_PRICE_CHANGES
    usage.GEMINI_PRICE_CHANGES = [("gemini-3.8-flash", "2000-01-01", 1.50, 7.50)]
    check("...and the published 2027 rise applies once its date comes",
          abs(usage.token_cost("gemini-3.8-flash", 1_000_000, 1_000_000) - 9.00) < 1e-9)
    usage.GEMINI_PRICE_CHANGES = saved
    check("embedding text input: $0.20 / 1M", abs(usage.token_cost("gemini-embedding-2", 1_000_000, 0) - 0.20) < 1e-9)

    # zero report is never "free"
    with usage.gate("gemini", 1, model="gemini-3.5-flash") as m:
        m.tokens(0, 0, 0)
    # a real one, read the way describe/narrate now do
    with usage.gate("gemini", 1, model="gemini-3.5-flash") as m:
        x = usage.parse_gemini_usage({"service_tier": "standard", "usage": {
            "total_input_tokens": 2250, "total_output_tokens": 500, "total_thought_tokens": 285}})
        m.tokens(x["prompt"], x["output"], x["cached"])
    rows = [json.loads(l) for l in open(usage.LOG_PATH) if l.strip()]
    check("a call reporting 0 tokens is logged as an estimate, not $0",
          rows[0]["metered"] is False and rows[0]["est_cost_usd"] > 0)
    want = 2250 * 1.5e-6 + 785 * 9e-6
    check("a real describe-sized call costs its measured tokens (~$0.0104)",
          rows[1]["metered"] is True and abs(rows[1]["est_cost_usd"] - want) < 1e-6)
    check("the log carries thinking tokens and the service tier",
          rows[1]["thought_tokens"] == 285 and rows[1]["service_tier"] == "standard")
    check("the rate card says the Gemini rates are published, with the date read",
          usage.rate_card()["gemini_rates_published"] is True and usage.rate_card()["gemini_prices_read"])

    # the two REST readers use the shared parser
    import importlib.util
    for name, path in (("describe", os.path.join(os.path.dirname(os.path.dirname(HERE)), "panel-describe", "describe.py")),
                       ("narrate", os.path.join(os.path.dirname(HERE), "narrate.py"))):
        src = open(path, encoding="utf-8").read()
        check(f"{name}.py reads usage through usage.parse_gemini_usage", "usage.parse_gemini_usage(res)" in src)
    msrc = open(os.path.join(os.path.dirname(HERE), "matcher.py"), encoding="utf-8").read()
    check("embeddings are metered with count_tokens (exact input)", "count_tokens(" in msrc and "_m.tokens(n or 0, 0)" in msrc)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
