import json
import pytest
import validator


@pytest.mark.parametrize("role", ["fragment", "credits", "art", "bubble"])
@pytest.mark.parametrize("used", [False, True])
def test_only_excluded_non_story_panels_skip_missing_description(tmp_path, role, used):
    (tmp_path / "descriptions.json").write_text(json.dumps([
        {"panel_id": "p", "role": role, "ok": True, "visual_description": "", "ocr_text": ""}]))
    (tmp_path / "segments.json").write_text(json.dumps([
        {"seg_index": 0, "panel_id": "p", "start": 0, "dur": 2, "beats": [], "user_included": True}
    ] if used else []))
    findings = validator.rule_findings(validator.build_rows(str(tmp_path)))
    missing = [f for f in findings if f["category"] == "description"]
    assert bool(missing) == (used or role not in {"fragment", "credits"})
