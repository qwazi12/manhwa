from copy import deepcopy
import pytest
import editorial_cut as cut


def fixture():
    return [{"seg_index": i, "panel_id": p, "panel_file": f"/source/crops/{p}.png",
             "start": i * 20, "end": i * 20 + 18, "dur": 18, "user_included": True,
             "beats": [{"index": i, "text": text, "start": i * 20, "end": i * 20 + 18,
                        "source_line_refs": [{"source_line_id": f"{p}:line:0"}]}]}
            for i, (p, text) in enumerate([
                ("revival", "The elder returns."), ("border", "Decorative filler."),
                ("rescue", "Makli Wangshin threatens the wounded ally."),
                ("barrier", "The barrier closes their escape.")])]


def recipe():
    return {"forbidden_panels": ["border"],
            "aliases": {"Makli Wangshin": "Makli Hwangshin"},
            "keep": [{"prefix": "The elder", "text": "The elder returns. His friend needs time to escape."},
                     {"prefix": "Makli Wangshin"}, {"prefix": "The barrier"}]}


def test_cut_removes_border_and_repetition_and_tracks_changed_speech():
    original = fixture(); before = deepcopy(original)
    planned = cut.plan(original, recipe())
    assert original == before
    assert [x["segment"]["panel_id"] for x in planned] == ["revival", "rescue", "barrier"]
    assert [x["changed"] for x in planned] == [True, True, False]
    assert "Hwangshin" in planned[1]["text"]
    assert "Wangshin" not in " ".join(x["text"] for x in planned)


def test_actual_audio_durations_replace_old_offsets_and_preserve_evidence():
    planned = cut.plan(fixture(), recipe()); before = deepcopy(planned)
    audio = {x["beat"]["index"]: {"file": f"edited/{x['beat']['index']}.mp3",
             "text": x["text"], "duration": 3.5} for x in planned}
    segments, script = cut.materialize(planned, source="/source", destination="/revision", audio=audio)
    assert planned == before
    assert [s["start"] for s in segments] == [0, 3.58, 7.16]
    assert segments[-1]["end"] == 10.74
    assert all(s["beats"][0]["end"] <= s["end"] for s in segments)
    assert all(s["panel_file"].startswith("/revision/crops/") for s in segments)
    assert script[1]["source_line_refs"] == [{"source_line_id": "rescue:line:0"}]


@pytest.mark.parametrize("fault", ["missing", "ambiguous", "border", "duplicate", "reorder", "excluded", "sliced"])
def test_unsafe_or_stale_editorial_selections_fail_closed(fault):
    segments, rec = fixture(), recipe()
    if fault == "missing": rec["keep"][0]["prefix"] = "No match"
    if fault == "ambiguous": segments[2]["beats"][0]["text"] = "The elder also returns."
    if fault == "border": rec["keep"].insert(1, {"prefix": "Decorative"})
    if fault == "duplicate": rec["keep"].append(rec["keep"][0])
    if fault == "reorder": rec["keep"].reverse()
    if fault == "excluded": segments[0]["user_included"] = False
    if fault == "sliced": segments.append(deepcopy(segments[0]))
    with pytest.raises(ValueError): cut.plan(segments, rec)


@pytest.mark.parametrize("fault", ["same_project", "panel_escape", "audio_escape", "wrong_speech", "duration"])
def test_materialization_refuses_cross_project_or_unverified_media(fault):
    planned = cut.plan(fixture(), recipe())
    audio = {x["beat"]["index"]: {"file": "beat.mp3", "text": x["text"], "duration": 4} for x in planned}
    destination = "/source" if fault == "same_project" else "/revision"
    if fault == "panel_escape": planned[0]["segment"]["panel_file"] = "/outside/p.png"
    if fault == "audio_escape": audio[0]["file"] = "../secret.mp3"
    if fault == "wrong_speech": audio[0]["text"] = "Old speech"
    if fault == "duration": audio[0]["duration"] = 0
    with pytest.raises(ValueError): cut.materialize(planned, source="/source", destination=destination, audio=audio)


def test_manual_export_without_override_uses_saved_studio_speed(monkeypatch):
    import server
    monkeypatch.setattr(server._studio, "export_speed", lambda: 1.15)
    monkeypatch.setattr(server, "_do_export", lambda speed: {"speed": speed})
    assert server.export(server.ExportIn()) == {"speed": 1.15}
    assert server.export(server.ExportIn(speed=1.0)) == {"speed": 1.0}
