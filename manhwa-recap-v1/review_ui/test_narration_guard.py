"""AI assistant text must never reach a render (Mercenary ch.100, 2026-10-26)."""
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
import narration_guard as ng


@pytest.mark.parametrize("line", [
    "Would you like a summary of the next chapter?",
    "As an AI, I cannot describe this panel.",
    "Here is the narration for panel 12:",
    "Here's a revised version of the recap.",
    "Let me know if you want more detail.",
    "I hope this helps!",
    "Shall I continue with the next part?",
])
def test_assistant_text_is_caught(line):
    assert ng.is_assistant_text(line)


@pytest.mark.parametrize("line", [
    "Would you like to die here, boy?",            # character dialogue
    "He asked whether she would like more time.",
    "Next time, on The Regressed Mercenary.",
    "The summary of his crimes was read aloud.",
    "Let me go!",
    "She hoped this would help them escape.",
    "",
])
def test_story_lines_are_not_flagged(line):
    assert not ng.is_assistant_text(line)


def test_assistant_lines_reports_segment_and_beat():
    segs = [{"seg_index": 3, "beats": [{"index": 9, "text": "The blade fell."}]},
            {"seg_index": 4, "beats": [{"index": 10, "text": "Would you like a summary of the next chapter?"}]}]
    assert ng.assistant_lines(segs) == [(4, 10, "Would you like a summary of the next chapter?")]
    assert ng.assistant_lines(segs, [3]) == []


def test_render_gate_refuses_assistant_text(tmp_path, monkeypatch):
    import server
    from fastapi import HTTPException
    segs = [{"seg_index": 0, "start": 0, "end": 2, "dur": 2, "beats": [
        {"index": 0, "start": 0, "end": 1.5, "text": "Would you like a summary of the next chapter?"}]}]
    (tmp_path / "segments.json").write_text(json.dumps(segs))
    monkeypatch.setattr(server, "active_project_dir", lambda: str(tmp_path))
    import storyboard_edit
    monkeypatch.setattr(storyboard_edit, "validate_timeline", lambda p: {"errors": []})
    with pytest.raises(HTTPException) as e:
        server._gate_timeline([0], "render")
    assert e.value.status_code == 400 and "AI assistant text" in e.value.detail


def test_validator_rule_flags_it():
    import validator
    row = {"n": 0, "panel_id": "p1", "file": "p1.png", "width": 800, "height": 1200, "pix": None,
           "spoken": "Would you like a summary of the next chapter?", "dialogue_orphan": False,
           "timing": {"seg_index": 0, "pos": 0, "seg_count": 1, "beat_count": 1, "dur": 4.0, "start": 0.0,
                      "in_video": True, "spoken_words": 9, "silent": False},
           "placement": {"role": "placed", "unit": 0, "text": "x"}, "role": None,
           "desc": "A man holds a sword in a ruined hall.", "ocr": "", "desc_ok": True}
    found = [f for f in validator.rule_findings([row]) if f.get("category") == "narration"]
    assert found and found[0]["severity"] == "high"
