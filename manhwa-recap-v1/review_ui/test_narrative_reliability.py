"""M2 source fidelity and critique lifecycle. All model calls are fakes."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import narrate
import ocr_lines
import panel_stats
import beat_segmenter
import matcher


def panel(n, role="art", text="ART", page=1, structured=True):
    p = {"panel_id": f"page{page:03d}_panel_{n:03d}", "role": role, "ok": True,
         "ocr_text": text, "width": 600, "height": 400,
         "pix": {"ink": .4, "std": 80},
         "visual_description": "Standing near a door, the boy confronts a man."}
    if structured:
        p["lines"] = [{"text": text, "type": "speech", "speaker": "the old man",
                       "speaker_basis": "context" if role == "bubble" else "tail", "lang": "en"}]
    return p


@pytest.mark.parametrize("structured", [True, False])
def test_multiple_bubbles_preserve_source_order_and_typed_prompt(structured):
    ps = [panel(1, "bubble", "FIRST", structured=structured), panel(2, "bubble", "SECOND", structured=structured),
          panel(3, text="ART", structured=structured), panel(4, "bubble", "AFTER ONE", structured=structured),
          panel(5, "bubble", "AFTER TWO", structured=structured)]
    panel_stats.attach_bubble_dialogue(ps)
    assert ps[2]["ocr_text"] == "FIRST / SECOND / ART / AFTER ONE / AFTER TWO"
    assert [l["text"] for l in ps[2]["lines"]] == ["FIRST", "SECOND", "ART", "AFTER ONE", "AFTER TWO"]
    prompt = narrate.build_prompt([ps[2]])
    assert prompt.index('"FIRST"') < prompt.index('"SECOND"') < prompt.index('"ART"')
    assert ps[2]["lines"][0]["source_panel_id"] == ps[0]["panel_id"]
    assert not ocr_lines.is_attributable(ps[2]["lines"][0])
    if not structured:
        assert ps[2]["lines"][0]["type"] == "unknown"
    before = copy.deepcopy(ps[2])
    panel_stats.attach_bubble_dialogue(ps)
    assert ps[2] == before


def test_orphan_is_recorded_without_mutating_source_or_inventing_cross_page_placement(tmp_path):
    original = [panel(1, "bubble", "DO NOT LOSE THIS"), panel(1, page=2)]
    path = tmp_path / "descriptions.json"
    path.write_text(json.dumps(original))
    kept = narrate.load_panels(str(path))
    assert len(kept) == 1 and "DO NOT LOSE" not in kept[0]["ocr_text"]
    assert json.loads(path.read_text()) == original
    report = copy.deepcopy(narrate.LAST_SOURCE_REVIEW)
    assert report["status"] == "needs_review" and report["orphans"][0]["text"] == "DO NOT LOSE THIS"
    assert report["orphans"][0]["lines"][0]["source_line_id"].startswith(original[0]["panel_id"])
    (tmp_path / "source_review.json").write_text(json.dumps(report))
    import validator
    rows = validator.build_rows(str(tmp_path))
    assert any(f["category"] == "ocr" and f["severity"] == "high" and "placement" in f["issue"]
               for f in validator.rule_findings(rows))


def test_no_safe_story_art_stops_before_any_model_call(monkeypatch):
    monkeypatch.setattr(narrate, "generate_global_beatsheet", lambda *a: pytest.fail("must not call model"))
    with pytest.raises(ValueError, match="No eligible"):
        narrate.generate_narration([], verbose=False)


def test_flat_text_missing_from_structured_lines_remains_explicit_unknown_evidence():
    p = panel(1, text="FIRST / IMPORTANT SECOND")
    p["lines"][0]["text"] = "FIRST"
    lines = ocr_lines.source_lines(p)
    assert [l["text"] for l in lines] == ["FIRST", "IMPORTANT SECOND"]
    assert lines[1]["type"] == "unknown" and not ocr_lines.is_attributable(lines[1])


def test_transfer_retains_confident_original_speaker_instead_of_target_speaker():
    ps = [panel(1, "bubble", "I warned you."), panel(2, text="Then leave.")]
    ps[0]["lines"][0].update(speaker="Alice", speaker_basis="tail")
    ps[1]["lines"][0].update(speaker="Bob", speaker_basis="tail")
    panel_stats.attach_bubble_dialogue(ps)
    assert [l["speaker"] for l in ps[1]["lines"]] == ["Alice", "Bob"]
    assert all(ocr_lines.is_attributable(l) for l in ps[1]["lines"])


def test_source_line_identity_survives_script_beats_shots_and_segments(tmp_path):
    ps = [panel(1, "bubble", "A warning."), panel(2, text="A response.")]
    path = tmp_path / "descriptions.json"
    path.write_text(json.dumps(ps))
    kept = narrate.load_panels(str(path))
    scenes = narrate.provenance([(kept, f"[ID: {kept[0]['panel_id']}] The boy heard a warning and answered.")])
    refs = scenes[0]["source_line_refs"]
    assert {r["source_panel_id"] for r in refs} == {p["panel_id"] for p in ps}
    assert all(r["relation"] == "available_evidence" for r in refs)
    beats = beat_segmenter.segment_beats_scenes(scenes)
    for b in beats:
        b.update(start=0, end=2)
    shots = matcher.build_timeline(beats, kept, [{"beat_index": 0, "panel_index": 0, "score": 1, "held": False}])
    sys.path.insert(0, str(Path(narrate.HERE) / "hyperframes"))
    from segments import build_segments
    assert build_segments(shots)[0]["beats"][0]["source_line_refs"] == refs


@pytest.mark.parametrize("raw", ["review unavailable", "{}", "null", "[{}]", '[{"unit":true}]',
                                '[{"unit":99,"type":"hallucination","problem":"x","fix":"y"}]'])
def test_invalid_critique_can_never_be_an_empty_clean_result(raw):
    with pytest.raises(narrate.CritiqueError):
        narrate.parse_critique(raw, 1)


def test_clean_and_partial_critique_statuses_are_distinct():
    assert narrate.parse_critique("```json\n[]\n```", 1) == []
    good = {"unit": 0, "type": "hallucination", "problem": "unsupported claim", "fix": "remove it"}
    assert narrate.parse_critique(json.dumps([good]), 1) == [good]
    with pytest.raises(narrate.CritiqueError) as err:
        narrate.parse_critique(json.dumps([good, {"unit": 1}]), 2)
    assert err.value.issues == [good]


def fake_drafts(monkeypatch, n=5):
    panels = [panel(i) for i in range(n)]
    monkeypatch.setattr(narrate, "generate_global_beatsheet", lambda *a: "global context")
    monkeypatch.setattr(narrate, "group_into_scenes", lambda ps: [[p] for p in ps])
    monkeypatch.setattr(narrate, "merge_into_units", lambda scenes: scenes)
    monkeypatch.setattr(narrate, "narrate_scene", lambda sc, *a, **k: f"[ID: {sc[0]['panel_id']}] The boy entered the castle.")
    return panels


def test_revision_cap_context_and_unverified_findings_are_preserved(monkeypatch):
    panels = fake_drafts(monkeypatch)
    issues = [{"unit": i, "type": "hallucination", "problem": f"claim {i}", "fix": "remove it"} for i in range(5)]
    monkeypatch.setattr(narrate, "critique_units", lambda *a, **k: issues)
    received = []
    def rewrite(sc, *a, **kwargs):
        received.append(kwargs)
        return f"[ID: {sc[0]['panel_id']}] The boy stepped inside."
    monkeypatch.setattr(narrate, "revise_unit", rewrite)
    bible = {"characters": [{"name": "Canon Hero"}]}
    narrate.generate_narration(panels, verbose=False, direct=False, series_bible_data=bible,
                               series_title="Canon Series", opening_alias="Canon Alias")
    c = narrate.LAST_CRITIQUE
    assert len(received) == narrate.MAX_REVISED_UNITS == 3
    assert all(k["series_bible_data"] == bible for k in received)
    assert received[0]["opening"] and all(not k["opening"] for k in received[1:])
    assert c["outcome"] == "needs_review" and len(c["unresolved"]) == 5
    assert sum(i["resolution"] == "rewritten_unverified" for i in c["unresolved"]) == 3


@pytest.mark.parametrize("failure", [narrate.CritiqueError("bad JSON"), RuntimeError("provider unavailable")])
def test_unavailable_critique_is_visible_and_not_clean(monkeypatch, failure):
    panels = fake_drafts(monkeypatch, 1)
    def fail(*a, **k):
        raise failure
    monkeypatch.setattr(narrate, "critique_units", fail)
    narrate.generate_narration(panels, verbose=False, direct=False)
    assert narrate.LAST_CRITIQUE["outcome"] == "incomplete"
    assert not narrate.LAST_CRITIQUE["review_response_valid"]


def test_revision_prompt_retains_bible_opening_and_complete_dialogue(monkeypatch):
    captured = {}
    monkeypatch.setattr(narrate, "usage", None)
    monkeypatch.setattr(narrate, "build_prompt", lambda *a, **k: captured.update(k) or "base")
    monkeypatch.setattr(narrate, "call_gemini_rest", lambda *a: "fixed")
    bible = {"name": "canonical"}
    narrate.revise_unit([panel(1)], "draft", [{"type": "hallucination", "problem": "x", "fix": "y"}],
                        "fake", "context", "fake", series_bible_data=bible, opening="OPENING")
    assert captured["series_bible_data"] == bible and captured["opening"] == "OPENING"


def test_critique_receives_source_text_beyond_old_truncation(monkeypatch):
    captured = []
    monkeypatch.setattr(narrate, "usage", None)
    monkeypatch.setattr(narrate, "call_gemini_rest", lambda m, prompt, key: captured.append(prompt) or "[]")
    p = panel(1, text="Beginning. " * 20 + "CRUCIAL ENDING")
    assert narrate.critique_units([([p], "draft")], "fake", "fake") == []
    assert "CRUCIAL ENDING" in captured[0]


def test_spending_cap_is_never_swallowed_by_review(monkeypatch):
    panels = fake_drafts(monkeypatch, 1)
    import usage
    monkeypatch.setattr(narrate, "usage", usage)
    def fail(*a, **k):
        raise usage.UsageCapExceeded("test cap")
    monkeypatch.setattr(narrate, "critique_units", fail)
    with pytest.raises(usage.UsageCapExceeded):
        narrate.generate_narration(panels, verbose=False, direct=False)
