"""Adversarial check of the release gate (2026-11-14): take a chapter and a
compilation whose evidence is complete, then damage ONE evidence file at a
time in many ways. The gate must never raise and must never answer "clear"
for damaged evidence."""
import json
import os
import random
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_gate as rg  # noqa: E402
from gate_fixtures import chapter, merge  # noqa: E402

N, R = "final.mp4", "range.mp4"
JUNK = [None, "", "x", 0, 1, -1, 1.5, True, [], [1], {}, {"a": 1}, "0" * 64, [{}], {"status": None}]


def _mutations(value, rnd, depth=0):
    """Variants of a JSON value with one thing wrong."""
    out = []
    if isinstance(value, dict):
        for k in value:
            dropped = {x: y for x, y in value.items() if x != k}
            out.append(dropped)
            for j in rnd.sample(JUNK, 4):
                out.append({**value, k: j})
            if depth < 2 and isinstance(value[k], (dict, list)):
                for sub in _mutations(value[k], rnd, depth + 1)[:6]:
                    out.append({**value, k: sub})
    elif isinstance(value, list) and value:
        out += [[], value[:-1], value + value, value[::-1] if len(value) > 1 else [None]]
        if depth < 2:
            for sub in _mutations(value[0], rnd, depth + 1)[:6]:
                out.append([sub] + value[1:])
    return out


def _targets(root):
    c1, c2 = chapter(root, "f_1"), chapter(root, "f_2")
    m = merge(root, "f_ch1-2", [(c1, N), (c2, N)], R)
    return c1, m


def test_intact_evidence_is_clear(tmp_path):
    c1, m = _targets(str(tmp_path))
    assert rg.verdict(c1, N)["release"] == "clear" and rg.verdict(m, R)["release"] == "clear"


@pytest.mark.parametrize("seed", [20261114, 1, 7])
@pytest.mark.parametrize("which", ["critique", "qa", "manifest", "evidence", "merge_qa", "merge_manifest"])
def test_damaged_evidence_never_raises_and_never_reads_clear(tmp_path, which, seed):
    rnd = random.Random(seed)
    root = str(tmp_path)
    c1, m = _targets(root)
    path, target = {
        "critique": (os.path.join(c1, "critique.json"), (c1, N)),
        "qa": (os.path.join(c1, "exports", "_qa", N + ".qa.json"), (c1, N)),
        "manifest": (os.path.join(c1, "exports", "_qa", N + ".manifest.json"), (c1, N)),
        "evidence": (os.path.join(m, rg.EVIDENCE), (m, R)),
        "merge_qa": (os.path.join(m, "exports", "_qa", R + ".qa.json"), (m, R)),
        "merge_manifest": (os.path.join(m, "exports", "_qa", R + ".manifest.json"), (m, R)),
    }[which]
    good_text = open(path).read()
    good = json.loads(good_text)
    same = json.dumps(good, sort_keys=True)
    variants = [json.dumps(v) for v in _mutations(good, rnd) if json.dumps(v, sort_keys=True) != same]
    variants += ["", "{", "null", "[]", "\x00\x01", good_text[: len(good_text) // 2], '"' + good_text]
    tried = clear_ok = 0
    for text in variants:
        with open(path, "w") as f:
            f.write(text)
        v = rg.verdict(*target)                       # must not raise
        tried += 1
        assert v["release"] in ("blocked", "unknown", "review", "clear")
        if v["release"] == "clear":
            # allowed only when the change did not touch anything the gate relies on
            # (e.g. a display-only field): the re-loaded file must still be bound end to end
            restored = json.loads(text)
            assert _still_bound(which, good, restored), (which, text[:200])
            clear_ok += 1
        rg.blocking(*target)
    with open(path, "w") as f:
        f.write(good_text)
    assert rg.verdict(*target)["release"] == "clear" and tried > 15
    os.remove(path)
    assert rg.verdict(*target)["release"] != "clear"                  # and missing is not clear either


def _still_bound(which, good, new):
    """Fields whose change MUST move the verdict off "clear"."""
    keys = {"critique": ("status", "outcome", "unresolved", "narration_sha256"),
            "qa": ("status", "export_digest", "manifest_digest", "input_digest", "version"),
            "manifest": ("events", "input_digest", "export_digest", "context"),
            "merge_qa": ("status", "export_digest", "manifest_digest", "input_digest", "version"),
            "merge_manifest": ("input_digest", "export_digest", "context", "inputs"),
            "evidence": ("exports",)}[which]
    if which == "critique":
        # issues/revised are only read for the oldest records (no "unresolved"); "verified" is informational
        # a missing "outcome" is fine (older records): it is derived from "unresolved"
        return all(new.get(k) == good.get(k) for k in keys if k != "outcome" or "outcome" in new)
    if which in ("manifest", "merge_manifest"):
        return False           # any change to a manifest changes its digest: QA no longer matches it
    if which == "evidence":
        g, n = good["exports"], new.get("exports")
        if not isinstance(n, dict) or R not in n:
            return False
        strip = lambda e: {k: v for k, v in e.items() if k in ("project", "name", "kind", "export_sha256", "findings", "components")}
        try:
            return [strip(e) for e in n[R]["components"]] == [strip(e) for e in g[R]["components"]]
        except Exception:
            return False
    return all(new.get(k) == good.get(k) for k in keys)


def test_missing_files_one_at_a_time(tmp_path):
    root = str(tmp_path)
    c1, m = _targets(root)
    for rel, target in (("script.txt", None), ("segments.json", None)):
        pass                                           # the export's own manifest carries the spoken lines
    for rel, target in ((os.path.join("exports", N), (c1, N)), (os.path.join("exports", "_qa", N + ".qa.json"), (c1, N)),
                        (os.path.join("exports", "_qa", N + ".manifest.json"), (c1, N)), ("critique.json", (c1, N)),
                        (os.path.join("exports", R), (m, R)), (rg.EVIDENCE, (m, R))):
        base = target[0]
        path = os.path.join(base, rel)
        keep = path + ".keep"
        shutil.move(path, keep)
        assert rg.verdict(*target)["release"] in ("unknown", "blocked"), rel
        shutil.move(keep, path)
        assert rg.verdict(*target)["release"] == "clear", rel
