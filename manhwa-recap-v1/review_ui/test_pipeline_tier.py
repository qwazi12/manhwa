"""Describe + narrate on Gemini 3.8 Flash, autopilot on the Flex tier
(owner, 2026-10-03). Captures the real request bodies with a fake network:
the model, the service_tier field, the Standard retry when Flex is refused,
and that a manual ingest stays on Standard. No network, no money."""
import io
import json
import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.dirname(HERE)
ROOT = os.path.dirname(RECAP)
sys.path.insert(0, HERE)
sys.path.insert(0, RECAP)
sys.path.insert(0, os.path.join(ROOT, "panel-describe"))
R = []


def check(name, ok):
    R.append((name, bool(ok)))


OK = {"service_tier": "flex", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "OK"}]}],
      "usage": {"total_input_tokens": 10, "total_output_tokens": 2, "total_thought_tokens": 5}}


class Net:
    def __init__(self, fail_first=None):
        self.sent = []
        self.fail_first = fail_first

    def urlopen(self, req, timeout=None, context=None):
        body = json.loads(req.data.decode())
        self.sent.append((body, timeout))
        if self.fail_first and len(self.sent) == 1:
            raise urllib.error.HTTPError(req.full_url, self.fail_first, "busy", {}, io.BytesIO(b"capacity"))
        return io.BytesIO(json.dumps(OK).encode())


def main():
    import usage
    import narrate
    import describe
    check("narrate defaults to Gemini 3.8 Flash", narrate.DEFAULT_MODEL == "gemini-3.8-flash")
    src = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    check("describe is launched with PIPELINE_MODEL (default 3.8 Flash)",
          'os.environ.get("PIPELINE_MODEL", "gemini-3.8-flash")' in src)

    real = narrate.urllib.request.urlopen if hasattr(narrate, "urllib") else None
    import urllib.request as ur
    saved = ur.urlopen
    narrate.time = __import__("time")
    try:
        # manual ingest: Standard
        usage.set_tier("")
        n = Net(); ur.urlopen = n.urlopen
        narrate.call_gemini_rest("gemini-3.8-flash", "hi", "AQ.test")
        check("manual: no service_tier sent (Standard)", "service_tier" not in n.sent[0][0])

        # autopilot: Flex with a long timeout
        usage.set_tier("flex")
        n = Net(); ur.urlopen = n.urlopen
        narrate.call_gemini_rest("gemini-3.8-flash", "hi", "AQ.test")
        check("autopilot: narrate requests service_tier=flex", n.sent[0][0].get("service_tier") == "flex")
        check("...with a timeout long enough for Flex queueing", n.sent[0][1] >= 900)
        check("...and the model is 3.8 Flash", n.sent[0][0]["model"] == "gemini-3.8-flash")

        # Flex refused at capacity -> retried on Standard
        import time as _t
        sleep = _t.sleep; _t.sleep = lambda s: None
        n = Net(fail_first=503); ur.urlopen = n.urlopen
        narrate.call_gemini_rest("gemini-3.8-flash", "hi", "AQ.test")
        _t.sleep = sleep
        check("Flex 503 -> narrate retries on Standard",
              len(n.sent) == 2 and n.sent[0][0].get("service_tier") == "flex"
              and "service_tier" not in n.sent[1][0])

        # describe (subprocess reads the tier from the environment)
        usage.set_tier("")
        os.environ["RECAP_SERVICE_TIER"] = "flex"
        n = Net(); ur.urlopen = n.urlopen
        describe._call_interactions_api("AQ.test", "gemini-3.8-flash", "aGk=", "image/png")
        check("describe subprocess requests Flex from RECAP_SERVICE_TIER",
              n.sent[0][0].get("service_tier") == "flex" and n.sent[0][1] >= 900)
        n = Net(fail_first=429); ur.urlopen = n.urlopen
        describe._call_interactions_api("AQ.test", "gemini-3.8-flash", "aGk=", "image/png")
        check("Flex 429 -> describe retries on Standard",
              len(n.sent) == 2 and "service_tier" not in n.sent[1][0])
        os.environ.pop("RECAP_SERVICE_TIER", None)
        n = Net(); ur.urlopen = n.urlopen
        describe._call_interactions_api("AQ.test", "gemini-3.8-flash", "aGk=", "image/png")
        check("describe without a tier stays Standard", "service_tier" not in n.sent[0][0])
    finally:
        ur.urlopen = saved
        usage.set_tier("")
        os.environ.pop("RECAP_SERVICE_TIER", None)

    check("Flex is billed at half the 3.8 Flash rate",
          abs(usage.token_cost("gemini-3.8-flash", 1_000_000, 1_000_000, tier="flex") - 2.25) < 1e-9)
    ssrc = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    check("server: only autopilot jobs get the Flex tier",
          'if INGEST[job_id].get("source") == "autopilot" else ""' in ssrc)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
