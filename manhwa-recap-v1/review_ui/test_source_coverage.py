"""Deterministic source linkage, deliberately not a semantic quality score."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generation_cache as cache
import ocr_lines
import source_coverage as coverage


def write(pdir, panels=None, scenes=None, script="He returns to the room."):
    pdir.mkdir(parents=True, exist_ok=True)
    panels = panels if panels is not None else [{
        "panel_id": "p1", "width": 600, "height": 400, "ok": True, "role": "art",
        "visual_description": "A boy opens the door.", "ocr_text": "Come back.",
        "lines": [{"text": "Come back.", "type": "speech", "speaker": "Taro", "speaker_basis": "tail"}]}]
    scenes = scenes if scenes is not None else [{"panel_ids": ["p1"], "text": script,
                                                 "source_line_refs": ocr_lines.source_lines(panels[0])}]
    cache.write_json(pdir / "descriptions.json", panels)
    cache.write_json(pdir / "script.json", scenes)
    (pdir / "script.txt").write_text(script)
    return panels, scenes


def test_available_evidence_is_not_asserted_as_semantic_coverage(tmp_path):
    write(tmp_path, script="An unrelated sentence.")
    report = coverage.inspect(tmp_path)
    assert report["status"] == "evidence_linked" and report["semantic_coverage"] == "not_assessed"
    assert report["lines"][0]["evidence_link"] == "linked"
    assert not report["lines"][0]["verbatim_in_script"]
    assert report["findings"] == []  # link presence is the only tested claim


def test_missing_or_changed_line_references_are_review_candidates(tmp_path):
    panels, scenes = write(tmp_path)
    scenes[0]["source_line_refs"][0]["text"] = "Stale text."
    cache.write_json(tmp_path / "script.json", scenes)
    report = coverage.inspect(tmp_path)
    assert report["status"] == "needs_review"
    assert report["findings"][0]["source_line_id"] == "p1:line:0"
    assert report["findings"][0]["text"] == panels[0]["ocr_text"]


def test_bubble_transfer_identity_remains_linked_to_its_original_panel(tmp_path):
    panels, scenes = write(tmp_path)
    bubble = copy.deepcopy(panels[0])
    bubble.update(panel_id="p0", role="bubble")
    scenes[0]["source_line_refs"] += ocr_lines.source_lines(bubble)
    cache.write_json(tmp_path / "descriptions.json", [bubble, *panels])
    cache.write_json(tmp_path / "script.json", scenes)
    report = coverage.inspect(tmp_path)
    assert report["status"] == "evidence_linked"
    assert report["lines"][0]["source_panel_id"] == "p0"


def test_silent_provenance_does_not_claim_spoken_coverage(tmp_path):
    panels, scenes = write(tmp_path)
    scenes[0]["silent"] = True
    cache.write_json(tmp_path / "script.json", scenes)
    kinds = {f["kind"] for f in coverage.inspect(tmp_path)["findings"]}
    assert {"source_line_unlinked", "source_event_unlinked"} <= kinds


def test_unrepresented_artwork_and_legacy_lines_remain_uncertain(tmp_path):
    panels, scenes = write(tmp_path)
    scenes[0]["source_line_refs"] = []
    scenes[0]["panel_ids"] = []
    cache.write_json(tmp_path / "script.json", scenes)
    report = coverage.inspect(tmp_path)
    assert report["status"] == "needs_review"
    assert len(report["findings"]) == 2
    assert report["semantic_coverage"] == "not_assessed"


def test_sfx_and_credit_cards_do_not_become_dialogue_omissions(tmp_path):
    panels, scenes = write(tmp_path)
    panels[0]["lines"].append({"text": "BOOM", "type": "sfx", "lang": "en"})
    panels.append({**panels[0], "panel_id": "credits", "role": "credits"})
    cache.write_json(tmp_path / "descriptions.json", panels)
    report = coverage.inspect(tmp_path)
    assert len(report["lines"]) == 1 and report["status"] == "evidence_linked"


@pytest.mark.parametrize("bad", ["missing", "malformed", "empty", "wrong_schema"])
def test_unavailable_provenance_is_incomplete(tmp_path, bad):
    write(tmp_path)
    path = tmp_path / "script.json"
    if bad == "missing":
        path.unlink()
    elif bad == "malformed":
        path.write_text("{")
    elif bad == "empty":
        path.write_text("[]")
    else:
        path.write_text('["not a scene"]')
    report = coverage.inspect(tmp_path)
    assert report["status"] == "incomplete" and report["error"]


def test_independently_edited_plain_script_is_not_a_clean_scan(tmp_path):
    write(tmp_path)
    (tmp_path / "script.txt").write_text("An editorial replacement.")
    assert coverage.inspect(tmp_path)["findings"][-1]["kind"] == "script_provenance_disagree"


def test_route_is_project_scoped_and_read_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import ingest
    import server
    write(tmp_path / "first")
    write(tmp_path / "second", script="A second chapter.")
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    response = TestClient(server.app).get("/api/source-coverage?project=second")
    assert response.status_code == 200
    assert response.json()["identity"] == coverage.inspect(tmp_path / "second")["identity"]
    assert response.json()["identity"] != coverage.inspect(tmp_path / "first")["identity"]
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
