"""Phase 0 matcher tests (Session 27): no silent degradation.

Iron-Blooded ch1 matched a whole chapter on bag-of-words token overlap because
_gemini_embed issued ONE API CALL PER STRING (133 for that chapter), died after
19 on a transient failure, and returned None inside `except Exception`. Nothing
recorded why. These tests pin the three fixes: batching, retries, and a reason
that survives.

Run: python3 test_matcher_phase0.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matcher


class _FakeEmb:
    def __init__(self, n): self.values = [0.1] * 8


class _FakeModels:
    def __init__(self, fail_times=0, exc=None):
        self.calls = []
        self.fail_times = fail_times
        self.exc = exc or RuntimeError("429 RESOURCE_EXHAUSTED")

    def embed_content(self, model=None, contents=None, config=None):
        self.calls.append(list(contents))
        if len(self.calls) <= self.fail_times:
            raise self.exc
        return types.SimpleNamespace(embeddings=[_FakeEmb(i) for i in contents])


def _install_fake_sdk(models):
    genai = types.ModuleType("google.genai")
    genai.Client = lambda api_key=None: types.SimpleNamespace(models=models)
    gtypes = types.ModuleType("google.genai.types")
    gtypes.EmbedContentConfig = lambda **kw: None
    google = types.ModuleType("google")
    google.genai = genai
    genai.types = gtypes
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai
    sys.modules["google.genai.types"] = gtypes


def main():
    r = []
    os.environ["GEMINI_API_KEY"] = "test-key"
    matcher._load_embed_cache = lambda: {}       # never touch the real cache
    matcher._save_embed_cache = lambda c: None
    matcher._usage = None

    # ---- transient classification
    r.append(("rate-limit errors are treated as transient",
              matcher._is_transient(RuntimeError("429 RESOURCE_EXHAUSTED"))))
    r.append(("a permanent error is NOT retried as transient",
              not matcher._is_transient(ValueError("invalid model name"))))

    # ---- batching: 70 texts must NOT be 70 calls
    m = _FakeModels(); _install_fake_sdk(m)
    out = matcher._gemini_embed([f"text {i}" for i in range(70)])
    r.append(("70 texts embed in ceil(70/32)=3 batched calls, not 70",
              out is not None and len(m.calls) == 3))
    r.append(("every text still gets a vector back",
              out is not None and out.shape[0] == 70))

    # ---- an SDK that IGNORES batching must still embed everything.
    # Production pins only "google-genai>=1.0.0" and returned 1 vector for a
    # 32-text batch while the same call gave 3-for-3 on a dev box, which is
    # what kept Iron-Blooded on lexical even after the first fix.
    class _NoBatch(_FakeModels):
        def embed_content(self, model=None, contents=None, config=None):
            self.calls.append(contents)
            n = 1 if isinstance(contents, str) else len(contents)
            # always returns ONE vector, however many texts were sent
            return types.SimpleNamespace(embeddings=[_FakeEmb(0)])
    nb = _NoBatch(); _install_fake_sdk(nb)
    out = matcher._gemini_embed([f"t{i}" for i in range(5)])
    r.append(("an SDK that ignores batching still embeds every text",
              out is not None and out.shape[0] == 5))
    r.append(("...by dropping to one call per text rather than failing",
              all(isinstance(c, str) for c in nb.calls[1:])))
    r.append(("...and reports no error once it succeeds",
              matcher.LAST_EMBED_ERROR == ""))

    # ---- retry absorbs a transient failure (the Iron-Blooded failure mode)
    m = _FakeModels(fail_times=2); _install_fake_sdk(m)
    matcher.time = types.SimpleNamespace(sleep=lambda s: None)   # no real backoff
    out = matcher._gemini_embed(["a", "b"])
    r.append(("a transient failure is retried and then succeeds",
              out is not None and matcher.LAST_EMBED_ERROR == ""))

    # ---- a permanent failure gives up but RECORDS WHY
    m = _FakeModels(fail_times=99, exc=ValueError("invalid model name"))
    _install_fake_sdk(m)
    out = matcher._gemini_embed(["a", "b"])
    r.append(("a permanent failure returns None", out is None))
    r.append(("...and the REASON is recorded, not swallowed",
              "invalid model name" in matcher.LAST_EMBED_ERROR))
    r.append(("...and it did not burn all four retries on a permanent error",
              len(m.calls) == 1))

    # ---- a missing key is explained rather than silently None
    os.environ.pop("GEMINI_API_KEY")
    r.append(("a missing API key is explained",
              matcher._gemini_embed(["a"]) is None
              and "GEMINI_API_KEY" in matcher.LAST_EMBED_ERROR))
    os.environ["GEMINI_API_KEY"] = "test-key"

    # ---- build_scorer names the fallback AND publishes the reason
    matcher.EMBED_FALLBACK_REASON.clear()
    matcher._gemini_embed = lambda texts, model_name=None: None
    matcher.LAST_EMBED_ERROR = "simulated outage"
    beats = [{"text": "a knight kneels"}, {"text": "a baby cries"}]
    panels = [{"visual_description": "knight", "ocr_text": ""},
              {"visual_description": "baby", "ocr_text": ""}]
    _score, method = matcher.build_scorer(beats, panels, None)
    r.append(("a failed embed run is named 'lexical', not passed off as semantic",
              method == "lexical"))
    r.append(("the fallback reason is published for the project record",
              matcher.EMBED_FALLBACK_REASON
              and "simulated outage" in matcher.EMBED_FALLBACK_REASON[-1]))

    # ---- hold cap reports what it could NOT split
    matcher.HOLD_CAP_REPORT.clear()
    beats = [{"text": "x", "start": 0.0, "end": 9.0},
             {"text": "y", "start": 9.0, "end": 18.0}]
    panels = [{"panel_id": "p0", "visual_description": "a"},
              {"panel_id": "p1", "visual_description": "b"}]
    assigns = [{"beat_index": 0, "panel_index": 0},
               {"beat_index": 1, "panel_index": 0}]
    matcher.enforce_hold_cap(beats, panels, assigns, cap_s=12.0)
    r.append(("an 18s hold with no free panel is REPORTED, not silently ignored",
              len(matcher.HOLD_CAP_REPORT) == 1
              and matcher.HOLD_CAP_REPORT[0]["seconds"] == 18.0))
    matcher.HOLD_CAP_REPORT.clear()
    beats2 = [{"text": "x", "start": 0.0, "end": 2.0},
              {"text": "y", "start": 2.0, "end": 4.0}]
    matcher.enforce_hold_cap(beats2, panels, [{"beat_index": 0, "panel_index": 0},
                                              {"beat_index": 1, "panel_index": 0}],
                             cap_s=12.0)
    r.append(("a short hold reports nothing", matcher.HOLD_CAP_REPORT == []))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
