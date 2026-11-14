"""Release gate (release_gate.py) and the scorecard built from the same
records. The gate's rule: a check counts only when it is proven to be about
the export being released; anything else is "unknown", never "clear".
No model calls, no network."""
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_gate as rg  # noqa: E402
import scorecard as sc  # noqa: E402
from gate_fixtures import (CLEAN, OPEN, UNVERIFIED, LINES, chapter, merge, sha, w, write_board,  # noqa: E402
                           write_export, write_manifest, write_qa)

N = "final.mp4"


@pytest.fixture(autouse=True)
def _policy(monkeypatch):
    monkeypatch.delenv("RELEASE_GATE", raising=False)
    monkeypatch.delenv("RELEASE_GATE_LEGACY", raising=False)


def codes(v, level=None):
    items = v[level] if level else v["critical"] + v["unknown"] + v["review"]
    return [x["code"] for x in items]


# ------------------------------------------------------------ single chapter

def test_fully_bound_chapter_is_clear_and_defects_block(tmp_path):
    root = str(tmp_path)
    assert rg.verdict(chapter(root, "a_1"), N) == {"release": "clear", "critical": [], "unknown": [], "review": []}
    v = rg.verdict(chapter(root, "a_2", OPEN), N)
    assert v["release"] == "blocked" and codes(v, "critical") == ["script_unresolved"]
    v = rg.verdict(chapter(root, "a_3", UNVERIFIED), N)
    assert v["release"] == "review" and codes(v) == ["script_unverified"]
    v = rg.verdict(chapter(root, "a_4", qa="failed"), N)
    assert v["release"] == "blocked" and codes(v, "critical") == ["media_failed"]
    v = rg.verdict(chapter(root, "a_5", {"status": "skipped: provider down", "outcome": "incomplete"}), N)
    assert v["release"] == "unknown" and codes(v) == ["script_unchecked"]
    v = rg.verdict(chapter(root, "a_6", validation={"findings": [{"severity": "high", "issue": "order"}]}), N)
    assert v["release"] == "review" and codes(v) == ["validator_high"]


def test_nothing_blocks_until_the_gate_is_switched_on(tmp_path, monkeypatch):
    bad = chapter(str(tmp_path), "b_1", OPEN, qa="failed")
    assert rg.mode() == "off" and rg.blocking(bad, N) == []
    monkeypatch.setenv("RELEASE_GATE", "critical")
    assert len(rg.blocking(bad, N)) == 2 and all(x.startswith("Release check (") for x in rg.blocking(bad, N))
    soft = chapter(str(tmp_path), "b_2", UNVERIFIED)
    assert rg.blocking(soft, N) == []                        # review items do not block in "critical"
    unknown = chapter(str(tmp_path), "b_3", None)
    assert any("script_missing" in b for b in rg.blocking(unknown, N))    # unknown does
    monkeypatch.setenv("RELEASE_GATE", "strict")
    assert len(rg.blocking(soft, N)) == 1
    monkeypatch.setenv("RELEASE_GATE", "nonsense")
    assert rg.mode() == "off"


# ---- review at 0c02668: the four reproduced cases

def test_hashes_on_record_but_script_and_export_absent_is_not_clear(tmp_path):
    """Reproduced at 0c02668: a finished critique with a script hash and a
    passed media report with an export hash returned "clear" although neither
    the script nor the export was there."""
    p = chapter(str(tmp_path), "r_1")
    os.remove(os.path.join(p, "script.txt"))
    os.remove(os.path.join(p, "exports", N))
    v = rg.verdict(p, N)
    assert v["release"] == "unknown" and "export_missing" in codes(v)
    # the oldest shape of that case: nothing but the two reports
    q = os.path.join(str(tmp_path), "r_1b")
    w(os.path.join(q, "project.json"), {"id": "r_1b"})
    w(os.path.join(q, "critique.json"), {**CLEAN, "script_sha256": "ab" * 32})
    w(os.path.join(q, "exports", "_qa", N + ".qa.json"), {"status": "passed", "findings": [], "export_digest": "cd" * 32})
    v = rg.verdict(q, N)
    assert v["release"] == "unknown" and set(codes(v)) == {"export_missing", "narration_unbound"}


def test_passed_report_that_names_no_file_is_not_clear(tmp_path):
    """Reproduced at 0c02668: a passing report without export_digest returned
    no findings even with the export on disk."""
    p = chapter(str(tmp_path), "r_2")
    path = os.path.join(p, "exports", "_qa", N + ".qa.json")
    q = json.load(open(path))
    for bad in (None, "", 0):
        w(path, {**q, "export_digest": bad})
        assert codes(rg.verdict(p, N)) == ["media_unbound"], bad
    del q["export_digest"]
    w(path, q)
    assert rg.verdict(p, N)["release"] == "unknown"


def test_board_edits_change_what_the_script_check_covers(tmp_path):
    """script.txt is untouched; the line is edited / reordered / excluded on
    the board, which is what gets voiced."""
    root = str(tmp_path)
    for i, edited in enumerate((["He draws his sword.", "The gate EXPLODES.", "Nobody speaks."],   # edited
                                [LINES[1], LINES[0], LINES[2]],                                    # reordered
                                LINES[:2])):                                                       # excluded
        p = chapter(root, f"e_{i}")
        assert rg.verdict(p, N)["release"] == "clear"
        write_board(p, edited)                       # the board changes…
        assert rg.verdict(p, N)["release"] == "clear"    # …the old export still speaks the checked lines
        assert rg.script_findings(p)[0]["code"] == "narration_changed"   # the board itself no longer does
        write_export(p, N, f"re-render {i}".encode())    # …and is rendered again
        write_manifest(p, N, edited)
        write_qa(p, N)
        v = rg.verdict(p, N)
        assert v["release"] == "unknown" and codes(v) == ["narration_changed"], edited
        # a person approves exactly this export: stale evidence becomes a review item, not a pass
        import media_qa
        m = json.load(open(os.path.join(p, "exports", "_qa", N + ".manifest.json")))
        w(os.path.join(p, "reviews.json"), {N: {"status": "approved", "media_identity": {
            "export_digest": m["export_digest"], "manifest_digest": media_qa._digest(m)}}})
        v = rg.verdict(p, N)
        assert v["release"] == "review" and codes(v) == ["narration_edited"]
        # the approval was for an earlier render: it does not carry over
        write_export(p, N, b"rendered once more")
        write_manifest(p, N, edited)
        write_qa(p, N)
        assert codes(rg.verdict(p, N)) == ["narration_changed"]


def test_ingest_binds_the_critique_to_the_lines_it_built(tmp_path):
    p = chapter(str(tmp_path), "i_1", bind=False)
    assert codes(rg.verdict(p, N)) == ["narration_unbound"]
    assert rg.bind_critique(p) is False                       # no script hash: cannot be about this script
    c = json.load(open(os.path.join(p, "critique.json")))
    w(os.path.join(p, "critique.json"), {**c, "script_sha256": sha(os.path.join(p, "script.txt"))})
    assert rg.bind_critique(p) is True
    assert rg.verdict(p, N)["release"] == "clear"
    with open(os.path.join(p, "script.txt"), "a") as f:
        f.write("changed")
    assert rg.bind_critique(p) is False                       # refuses to vouch for another script
    assert codes(rg.verdict(p, N)) == ["script_outdated"]


# ------------------------------------------------- missing / unreadable / stale

def test_missing_evidence_and_the_legacy_policy(tmp_path, monkeypatch):
    root = str(tmp_path)
    no_critique, no_qa, unbound = chapter(root, "m_1", None), chapter(root, "m_2", qa=None), chapter(root, "m_3", bind=False)
    for p, code in ((no_critique, "script_missing"), (no_qa, "media_missing"), (unbound, "narration_unbound")):
        v = rg.verdict(p, N)
        assert v["release"] == "unknown" and codes(v) == [code]
    monkeypatch.setenv("RELEASE_GATE", "critical")
    assert rg.blocking(no_critique, N)
    monkeypatch.setenv("RELEASE_GATE_LEGACY", "review")       # the stated policy for older videos
    for p in (no_critique, no_qa, unbound):
        assert rg.verdict(p, N)["release"] == "review" and rg.blocking(p, N) == []
    monkeypatch.setenv("RELEASE_GATE", "strict")
    assert rg.blocking(no_critique, N)


def test_unreadable_unfinished_and_stale_are_never_downgraded(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setenv("RELEASE_GATE_LEGACY", "review")
    p = chapter(root, "u_1")
    for bad in ("{not json", "[1, 2]", "", '"text"'):
        with open(os.path.join(p, "critique.json"), "w") as f:
            f.write(bad)
        assert codes(rg.verdict(p, N), "unknown") == ["script_unreadable"], bad
    w(os.path.join(p, "critique.json"), {"status": "done", "outcome": "clean", "unresolved": "none"})
    assert "script_unreadable" in codes(rg.verdict(p, N), "unknown")

    p = chapter(root, "u_2")
    qa = os.path.join(p, "exports", "_qa", N + ".qa.json")
    good = json.load(open(qa))
    with open(qa, "w") as f:
        f.write("{oops")
    assert codes(rg.verdict(p, N), "unknown") == ["media_unreadable"]
    w(qa, {**good, "status": "incomplete"})
    assert codes(rg.verdict(p, N), "unknown") == ["media_unchecked"]
    w(qa, {**good, "status": "running"})
    assert codes(rg.verdict(p, N), "unknown") == ["media_unchecked"]
    w(qa, good)
    assert rg.verdict(p, N)["release"] == "clear"

    with open(os.path.join(p, "exports", N), "wb") as f:      # replaced after QA passed it
        f.write(b"second render, never checked")
    assert codes(rg.verdict(p, N), "unknown") == ["media_outdated"]

    p = chapter(root, "u_3")
    os.remove(os.path.join(p, "exports", "_qa", N + ".manifest.json"))
    assert "manifest_missing" in codes(rg.verdict(p, N), "unknown")

    p = chapter(root, "u_4")                                  # QA ran against other inputs / timeline
    mpath = os.path.join(p, "exports", "_qa", N + ".manifest.json")
    m = json.load(open(mpath))
    w(mpath, {**m, "input_digest": "other audio or images"})
    assert codes(rg.verdict(p, N), "unknown") == ["media_outdated"]
    w(mpath, m)
    w(qa.replace("u_2", "u_4"), {**json.load(open(qa.replace("u_2", "u_4"))), "version": 0})
    assert codes(rg.verdict(p, N), "unknown") == ["media_outdated"]

    p = chapter(root, "u_5")
    with open(os.path.join(p, "validation.json"), "w") as f:
        f.write("nope")
    assert codes(rg.verdict(p, N), "unknown") == ["validator_unreadable"]


def test_hash_that_cannot_be_computed_is_unknown(tmp_path, monkeypatch):
    p = chapter(str(tmp_path), "h_1")
    monkeypatch.setattr(rg, "_file_sha", lambda path: None)
    assert "media_unverifiable" in codes(rg.verdict(p, N), "unknown")


def test_archived_export_needs_a_verified_identity(tmp_path, monkeypatch):
    root = str(tmp_path)
    p = chapter(root, "v_1")
    digest = sha(os.path.join(p, "exports", N))
    os.remove(os.path.join(p, "exports", N))                  # local copy dropped
    assert codes(rg.verdict(p, N), "unknown") == ["export_missing"]          # absent, no archive: missing
    drive = {"video": N, "files": {"video": {"id": "drive-id", "md5": "x"}}, "verified": True}
    w(os.path.join(p, "drive.json"), drive)
    assert codes(rg.verdict(p, N), "unknown") == ["archive_unbound"]         # archived before hashes were kept
    monkeypatch.setenv("RELEASE_GATE_LEGACY", "review")
    assert rg.verdict(p, N)["release"] == "review"
    monkeypatch.delenv("RELEASE_GATE_LEGACY")
    w(os.path.join(p, "drive.json"), {**drive, "sha256": digest})
    assert rg.verdict(p, N)["release"] == "clear"                            # archived, identity verified
    assert rg.export_identity(p, N) == {"state": "archived", "sha256": digest}
    w(os.path.join(p, "drive.json"), {**drive, "sha256": "ee" * 32})
    assert codes(rg.verdict(p, N), "unknown") == ["media_outdated"]          # the archive holds another file
    w(os.path.join(p, "drive.json"), {**drive, "sha256": digest, "verified": False})
    assert codes(rg.verdict(p, N), "unknown") == ["archive_unbound"]         # upload never verified
    w(os.path.join(p, "drive.json"), {**drive, "sha256": digest, "video": "other.mp4"})
    assert codes(rg.verdict(p, N), "unknown") == ["export_missing"]          # the record is about another video


def test_drive_record_carries_the_sha256_of_what_was_uploaded(tmp_path, monkeypatch):
    import drive_store
    p = chapter(str(tmp_path), "d_1")
    monkeypatch.setattr(drive_store, "folder_id", lambda: "root")
    monkeypatch.setattr(drive_store, "find_or_create_folder", lambda *a, **k: "folder")
    monkeypatch.setattr(drive_store, "upload", lambda path, name, parent, stop=None, _open=None: {
        "id": "f1", "size": str(os.path.getsize(path)), "md5Checksum": drive_store._md5(path)})
    rec = drive_store.copy_chapter(p, N, "S", "1")
    assert rec["verified"] is True and rec["sha256"] == sha(os.path.join(p, "exports", N))
    os.remove(os.path.join(p, "exports", N))
    assert rg.verdict(p, N)["release"] == "clear"


# -------------------------------------------------------------- compilations

R = "final_range.mp4"


def test_compilation_answers_for_every_piece_from_its_stored_record(tmp_path):
    root = str(tmp_path)
    c1, c2 = chapter(root, "s_1"), chapter(root, "s_2", OPEN)
    m = merge(root, "s_ch1-2", [(c1, N), (c2, N)], R)
    v = rg.verdict(m, R)
    assert v["release"] == "blocked" and [(x["code"], x["chapter"]) for x in v["critical"]] == [("script_unresolved", "s_2")]
    ok = merge(root, "s_ch1-1", [(c1, N)], R)
    assert rg.verdict(ok, R)["release"] == "clear"
    ev = json.load(open(os.path.join(ok, rg.EVIDENCE)))["exports"][R]["components"][0]
    assert ev["export_sha256"] == sha(os.path.join(c1, "exports", N)) and ev["narration_sha256"] == rg.narration_digest(LINES)


def test_changing_a_chapter_later_neither_clears_nor_condemns_an_older_compilation(tmp_path):
    root = str(tmp_path)
    good, bad = chapter(root, "k_1"), chapter(root, "k_2", OPEN)
    m_good = merge(root, "k_ch1", [(good, N)], R)
    m_bad = merge(root, "k_ch2", [(bad, N)], R)
    assert rg.verdict(m_good, R)["release"] == "clear" and rg.verdict(m_bad, R)["release"] == "blocked"
    # the good chapter is later re-made with an open defect; the bad one is later fixed
    chapter(root, "k_1", OPEN, data=b"new render of 1")
    chapter(root, "k_2", CLEAN, data=b"new render of 2")
    assert rg.verdict(good, N)["release"] == "blocked" and rg.verdict(bad, N)["release"] == "clear"
    assert rg.verdict(m_good, R)["release"] == "clear"        # not falsely condemned
    assert rg.verdict(m_bad, R)["release"] == "blocked"       # not falsely cleared


def test_evidence_survives_cleanup_and_is_kept_per_export(tmp_path):
    root = str(tmp_path)
    c1, c2 = chapter(root, "p_1"), chapter(root, "p_2", OPEN)
    m = merge(root, "p_ch1-2", [(c1, N)], "v1.mp4")           # first build: chapter 1 only
    write_export(m, "v2.mp4", b"second build")
    write_manifest(m, "v2.mp4", components=[(c1, N), (c2, N)])
    write_qa(m, "v2.mp4")
    rg.store_evidence(m, "v2.mp4", [(c1, N), (c2, N)])        # second build of the same project
    shutil.rmtree(c1)
    shutil.rmtree(c2)
    assert rg.verdict(m, "v1.mp4")["release"] == "clear"      # each export keeps its own record
    v = rg.verdict(m, "v2.mp4")
    assert [(x["code"], x["chapter"]) for x in v["critical"]] == [("script_unresolved", "p_2")]


def test_nested_compilations_through_cleanup(tmp_path):
    """1-2 -> (1-2)+3 -> (1-3)+4, every earlier folder deleted on the way."""
    root = str(tmp_path)
    c1, c2 = chapter(root, "n_1"), chapter(root, "n_2", OPEN)
    m12 = merge(root, "n_ch1-2", [(c1, N), (c2, N)], "a.mp4")
    c3 = chapter(root, "n_3")
    m13 = merge(root, "n_ch1-3", [(m12, "a.mp4"), (c3, N)], "b.mp4")
    v = rg.verdict(m13, "b.mp4")
    assert [(x["code"], x["chapter"]) for x in v["critical"]] == [("script_unresolved", "n_2")]
    for d in (c1, c2, c3, m12):
        shutil.rmtree(d)
    c4 = chapter(root, "n_4")
    m14 = merge(root, "n_ch1-4", [(m13, "b.mp4"), (c4, N)], "c.mp4")
    shutil.rmtree(m13)
    v = rg.verdict(m14, "c.mp4")
    assert [(x["code"], x["chapter"]) for x in v["critical"]] == [("script_unresolved", "n_2")]
    assert v["unknown"] == []
    chapters = {x.get("chapter") for x in rg.findings(m14, "c.mp4")}
    assert chapters == {"n_2"}                                # the clean chapters add nothing


def test_piece_missing_when_the_compilation_was_built_is_unknown(tmp_path):
    root = str(tmp_path)
    c1 = chapter(root, "g_1")
    gone = os.path.join(root, "g_2")
    m = merge(root, "g_ch1-2", [(c1, N), (gone, N)], R)
    v = rg.verdict(m, R)
    assert v["release"] == "unknown" and ("component_missing", "g_2") in [(x["code"], x["chapter"]) for x in v["unknown"]]


def test_compilation_built_before_evidence_was_stored_is_matched_by_hash(tmp_path, monkeypatch):
    root = str(tmp_path)
    c1, c2 = chapter(root, "l_1"), chapter(root, "l_2")
    m = merge(root, "l_ch1-2", [(c1, N), (c2, N)], R, store=False)
    v = rg.verdict(m, R)
    assert codes(v) == ["evidence_missing"] and v["release"] == "unknown"     # hashes match, but nothing was stored
    monkeypatch.setenv("RELEASE_GATE_LEGACY", "review")
    assert rg.verdict(m, R)["release"] == "review"
    chapter(root, "l_2", OPEN, data=b"chapter 2 re-rendered later")          # piece changed since the build
    v = rg.verdict(m, R)
    assert ("component_changed", "l_2") in [(x["code"], x.get("chapter")) for x in v["unknown"]]
    assert v["critical"] == []                                # its new defect is not pinned on the old video
    shutil.rmtree(c1)
    v = rg.verdict(m, R)
    assert ("component_missing", "l_1") in [(x["code"], x.get("chapter")) for x in v["unknown"]]


def test_compilation_with_no_hashes_and_no_record_cannot_use_live_checks(tmp_path):
    root = str(tmp_path)
    c1 = chapter(root, "o_1")
    m = merge(root, "o_ch1", [(c1, N)], R, store=False)
    os.remove(os.path.join(m, "exports", "_qa", R + ".manifest.json"))       # only project.json knows the pieces
    v = rg.verdict(m, R)
    assert "component_unbound" in codes(v, "unknown") and v["release"] == "unknown"
    w(os.path.join(m, "project.json"), {"kind": "range"})
    assert "component_missing" in codes(rg.verdict(m, R), "unknown")


def test_stored_record_must_match_the_manifest_of_the_export(tmp_path):
    root = str(tmp_path)
    c1, c2 = chapter(root, "x_1"), chapter(root, "x_2")
    m = merge(root, "x_ch1-2", [(c1, N), (c2, N)], R)
    assert rg.verdict(m, R)["release"] == "clear"
    path = os.path.join(m, rg.EVIDENCE)
    ev = json.load(open(path))
    tampered = json.loads(json.dumps(ev))
    tampered["exports"][R]["components"][1]["export_sha256"] = "00" * 32      # record about another file
    w(path, tampered)
    assert "component_mismatch" in codes(rg.verdict(m, R), "unknown")
    tampered = json.loads(json.dumps(ev))
    tampered["exports"][R]["components"].pop()                                # record lists fewer pieces
    w(path, tampered)
    assert "component_mismatch" in codes(rg.verdict(m, R), "unknown")
    for bad in ({"exports": {R: {"components": [{"project": "x_1"}]}}}, {"exports": {R: {"components": []}}},
                {"exports": []}, {"exports": {R: {"components": [{"project": "x_1", "name": N,
                                                                   "findings": [{"code": "x", "level": "fine"}]}]}}}):
        w(path, bad)
        assert codes(rg.verdict(m, R), "unknown") == ["evidence_unreadable"], bad
    with open(path, "w") as f:
        f.write("{")
    assert codes(rg.verdict(m, R), "unknown") == ["evidence_unreadable"]


def test_cycles_and_depth_are_reported_not_skipped(tmp_path):
    root = str(tmp_path)
    c1 = chapter(root, "y_1")
    a = merge(root, "y_a", [(c1, N)], R, store=False)
    b = merge(root, "y_b", [(a, R)], R, store=False)
    write_manifest(a, R, components=[(b, R)])                 # a now claims to contain b, which contains a
    write_qa(a, R)
    v = rg.verdict(b, R)
    assert "component_cycle" in codes(v, "unknown") and v["release"] == "unknown"
    assert "component_cycle" in [f["code"] for e in rg.store_evidence(b, R, [(a, R)])
                                 for f in json.loads(json.dumps(e)).get("components", [{}])[0].get("findings", [])] \
        or "component_cycle" in codes(rg.verdict(b, R), "unknown")
    # a chain deeper than the limit
    prev, pname = c1, N
    for i in range(rg.MAX_DEPTH + 3):
        prev = merge(root, f"deep_{i}", [(prev, pname)], R)
        pname = R
    v = rg.verdict(prev, R)
    assert "component_depth" in codes(v, "unknown") and v["release"] == "unknown"
    # a stored record that contains itself (malformed data)
    path = os.path.join(a, rg.EVIDENCE)
    loop = {"project": "y_a", "name": R, "kind": "range", "findings": [], "components": []}
    w(path, {"exports": {R: {"components": [loop]}}})
    write_manifest(a, R, components=[(a, R)])
    write_qa(a, R)
    assert "component_cycle" in codes(rg.verdict(a, R), "unknown")


def test_legacy_policy_is_read_when_the_verdict_is_given_not_when_evidence_was_stored(tmp_path, monkeypatch):
    root = str(tmp_path)
    old = chapter(root, "t_1", None)                          # no critique
    m = merge(root, "t_ch1", [(old, N)], R)
    assert rg.verdict(m, R)["release"] == "unknown"
    monkeypatch.setenv("RELEASE_GATE_LEGACY", "review")
    assert rg.verdict(m, R)["release"] == "review"


# ------------------------------------------------------------------ scorecard

def test_scorecard_counts_and_says_what_it_cannot_know(tmp_path):
    root = str(tmp_path)
    chapter(root, "c_1")
    p2 = chapter(root, "c_2", OPEN, qa="failed", validation={"findings": [{"severity": "high", "issue": "x"}]})
    chapter(root, "c_3", None)
    with open(os.path.join(p2, "edits.log.jsonl"), "w") as f:
        f.write('{"op": "reorder"}\n{"op": "add_line"}\n')
    w(os.path.join(p2, "reviews.json"), {
        N: {"status": "approved", "history": [{"status": "sent_back"}], "reviewed_by": None},
        "old.mp4": {"status": "merged"}})
    w(os.path.join(root, "c_1", "reviews.json"), {N: {"status": "approved", "history": []}})
    q = sc.quality(root)
    assert q["script"]["chapters"] == 2 and q["script"]["issues"] == 2 and q["script"]["by_type"] == {"hallucination": 2}
    assert q["script"]["outcome"] == {"clean": 1, "needs_review": 1} and q["script"]["rechecked"] == 1
    assert q["media"]["passed"] == 2 and q["media"]["failed_rules"] == {"clipping": 1}
    assert q["timeline"]["high"] == 1
    c = q["corrections"]
    assert c["edits"] == 2 and c["exports_reviewed"] == 2 and c["sent_back"] == 1
    assert c["approved_first_time_pct"] == 50.0 and c["reviewer_recorded"] == 0 and "lower bounds" in c["caveat"]
    g = q["gate"]
    assert (g["blocked"], g["unknown"], g["clear"], g["mode"]) == (1, 1, 1, "off")
    assert g["by_code"]["script_missing"] == 1 and g["would_hold_pct"] == 66.7


def test_timeliness_from_release_and_from_detection():
    live = 1_800_000_000.0                                   # 2027-01-15 08:00 UTC
    series = {"mirrors": [{"release_dates": {"12": ["2027-01-14", False]}, "first_seen": {"12": live - 10 * 3600}},
                          {"release_dates": {"12": ["2027-01-13", True]}, "first_seen": {"12": 0}}]}
    posts = [{"project": "s_12", "label": "S 12", "url": "u1", "chapter_end": "12", "live_at": live},
             {"project": "s_13", "label": "S 13", "url": "u1", "chapter_end": "13", "live_at": live},
             {"project": "x_1", "label": "X 1", "url": "nope", "chapter_end": "1", "live_at": live}]
    t = sc.timeliness(posts, lambda url: series if url == "u1" else None)
    assert t["published"] == 3 and t["from_release"]["videos"] == 1 and t["from_detection"]["videos"] == 1
    r = t["videos"][0]
    assert r["release_day"] == "2027-01-13" and r["release_approx"] is True     # earliest any mirror reports
    assert r["hours_from_release"] == 56.0 and r["hours_from_detection"] == 10.0
    assert t["from_release"]["within_48h"] == 0 and t["from_detection"]["within_24h_pct"] == 100.0
    assert sum(t["unmeasured"].values()) == 2


def test_cost_summary():
    ledger = [{"cost": 1.2, "updated_at": 1}, {"cost": 0.4, "updated_at": 2}, {"cost": 0.9, "updated_at": 3}, {}]
    idx = {"by_stage": {("2026-11", "describe"): 3.0, ("2026-11", "unlabelled"): 1.0, ("2026-10", "voice"): 9.0}}
    c = sc.cost(ledger, idx, "2026-11")
    assert c["per_chapter_median"] == 0.9 and c["under_1_dollar_pct"] == 66.7 and c["under_50_cents_pct"] == 33.3
    assert c["month_by_stage"] == {"describe": 3.0, "unlabelled": 1.0} and c["month_labelled_pct"] == 75.0


