"""The series search pack (owner 2026-11-14): search name in the title, the
other names in "Also known as", one locked hook, exactly three hashtags."""
import json
import os
import tempfile
import threading

import pytest

import series_search as S

DK = {"series_id": "return-of-the-apocalypse-class-death-knight",
      "search_title": "Return of the Apocalypse-Class Death Knight",
      "hook": "He Destroyed The World, But Regressed To Save It!",
      "aka_names": ["The Apocalypse-Level Death Knight Returns"], "characters": ["Damian Haxen", "Dorgo"],
      "hashtags": ["#ReturnOfTheApocalypseClassDeathKnight", "#ManhwaRecap", "#Regression"], "evidence": "supported"}
EXTRA = {"series_id": "the-extra-s-academy-survival-guide", "search_title": "The Extra’s Academy Survival Guide",
         "hook": "Disowned Noble Forced to SAVE THE WORLD", "aka_names": [], "characters": ["Ed Rothstaylor"],
         "hashtags": ["#TheExtrasAcademySurvivalGuide", "#ManhwaRecap", "#Academy"]}
DESC = ("The Apocalypse-Level Death Knight Returns Chapters 88-95 recap (English)! Full chapters 88-95 recap on Flamingo Recap!\n\n"
        "\U0001F4D6 Series: The Apocalypse-Level Death Knight Returns (Return Of The Apocalypse Class Death Knight)\n"
        "\U0001F525 New recaps every week! Subscribe: https://www.youtube.com/@flamingorecap\n\n"
        "⏱️ Timestamps:\n• 0:00 - Chapter 88\n• 9:13 - Chapter 89\n\n"
        "© Fair Use:\nThis channel may utilize copyrighted material.\n\n"
        "#ManhwaRecap #TheApocalypseLevelDeathKnightReturns #MangaRecap")


def _root(*recs):
    root = tempfile.mkdtemp(prefix="ssp_")
    if recs:
        S.upsert(root, list(recs), source="test")
    return root


def test_one_record_answers_to_every_id_the_series_goes_by():
    root = _root(DK, EXTRA)
    assert S.get(root, "the-extra-s-academy-survival-guide")["characters"] == ["Ed Rothstaylor"]
    assert S.get(root, "the-extras-academy-survival-guide-6465")["search_title"] == "The Extra's Academy Survival Guide"
    assert S.norm("the-heavenly-demon-can-t-live-a-normal-life") == S.norm("the-heavenly-demon-cant-live-a-normal-life")
    assert S.norm("fog-land-9299") == "fog-land" and S.norm("solo-leveling-ragnarok") == "solo-leveling-ragnarok"
    assert S.get(root, "murim-login") is None


def test_title_uses_the_search_name_and_the_locked_hook_and_is_never_cut():
    rec = S.get(_root(EXTRA), "the-extra-s-academy-survival-guide")
    assert S.title_for(rec, "114-122") == ("[114-122] Disowned Noble Forced to SAVE THE WORLD | The Extra's Academy Survival Guide", None)
    dk = S.get(_root(DK), DK["series_id"])
    t, why = S.title_for(dk, "88-96")
    assert t is None and "103 characters" in why and dk["hook"].endswith("!")      # reported, hook untouched


def test_description_in_the_pack_format():
    rec = S.get(_root(DK), DK["series_id"])
    out = S.description_for(DESC, rec, "88-96").split("\n")
    assert out[0] == "Return of the Apocalypse-Class Death Knight Chapters 88-96 recap (English)! Full chapters 88-95 recap on Flamingo Recap!"
    assert out[2:5] == ["\U0001F4D6 Series: Return of the Apocalypse-Class Death Knight",
                        "Also known as: The Apocalypse-Level Death Knight Returns",
                        "Main characters: Damian Haxen, Dorgo"]
    assert out[5].startswith("\U0001F525 New recaps") and "• 9:13 - Chapter 89" in out      # the rest is kept
    assert out[-1] == "#ReturnOfTheApocalypseClassDeathKnight #ManhwaRecap #Regression" and out[-2] == ""
    assert sum(1 for ln in out if ln.startswith("#")) == 1                        # exactly one hashtag line, three tags
    assert S.description_for("\n".join(out), rec, "88-96") == "\n".join(out)       # applying twice changes nothing


def test_apostrophes_are_straight_and_the_name_appears_once_without_one():
    rec = S.get(_root(EXTRA), EXTRA["series_id"])
    d = S.description_for("The Extra’s Academy Survival Guide Chapter 120 recap (English)! Ed can’t rest.", rec, "120")
    assert d.split("\n")[0] == "The Extra's Academy Survival Guide Chapter 120 recap (English)! Ed can't rest."
    assert "Also known as: The Extras Academy Survival Guide" in d and "’" not in d
    assert "Main characters: Ed Rothstaylor" in d


def test_apply_reports_a_title_that_does_not_fit_and_leaves_it():
    rec = S.get(_root(DK), DK["series_id"])
    md = {"title": "[88-96] old title", "description": DESC, "tags": ["x"]}
    new, changed, probs = S.apply(md, rec, "88-96")
    assert new["title"] == "[88-96] old title" and len(probs) == 1 and "title" not in changed
    assert changed == ["description", "hashtags"] and new["hashtags"] == DK["hashtags"] and new["tags"] == ["x"]
    assert md["description"] == DESC                                              # input untouched


@pytest.mark.parametrize("bad, why", [
    ({**DK, "hashtags": ["#A", "#B"]}, "three"), ({**DK, "hashtags": ["#A", "#A", "#B"]}, "three"),
    ({**DK, "hashtags": ["A", "#B", "#C"]}, "three"), ({**DK, "hook": ""}, "required"),
    ({**DK, "search_title": "x" * 90}, "over 100"), ({**DK, "hook": "a <b>"}, "refuses"), ({"hook": "x"}, "series_id")])
def test_bad_records_are_refused_and_nothing_is_saved(bad, why):
    root = _root(EXTRA)
    with pytest.raises(S.PackError) as e:
        S.upsert(root, [DK, bad] if bad.get("series_id") != DK["series_id"] else [bad])
    assert why in str(e.value)
    assert list(S.load(root)["series"]) == [S.norm(EXTRA["series_id"])]


def test_saves_from_many_threads_keep_every_record():
    root = _root()
    recs = [{**DK, "series_id": f"series-{i}"} for i in range(24)]
    ts = [threading.Thread(target=S.upsert, args=(root, [r])) for r in recs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(json.load(open(os.path.join(root, S.NAME)))["series"]) == 24
    open(os.path.join(root, S.NAME), "w").write("{")                              # damaged: the last good copy answers
    assert len(S.load(root)["series"]) == 23


# ------------------------------------------------------------------ in the studio
def _lib(tmp_path, monkeypatch):
    import ingest
    import server
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_yt_root", lambda: os.path.join(root, "_yt"))
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    d = tmp_path / "return-of-the-apocalypse-class-death-knight_ch88-96"
    (d / "exports").mkdir(parents=True)
    (d / "exports" / "final_range_88-96.mp4").write_bytes(b"v")
    (d / "project.json").write_text(json.dumps({
        "id": d.name, "kind": "range", "series": "The Apocalypse-Level Death Knight Returns",
        "series_id": "return-of-the-apocalypse-class-death-knight", "chapters_start": 88, "chapters_end": 96}))
    server.save_publish(str(d), {"final_range_88-96.mp4": {"title": "[88-96] old", "description": DESC, "tags": ["t"]}},
                        actor="owner")
    return server, root, d


def test_routes_save_read_preview_and_apply(tmp_path, monkeypatch):
    from fastapi import HTTPException
    server, root, d = _lib(tmp_path, monkeypatch)
    short = {**DK, "hook": "He Destroyed The World, Then Regressed"}
    with pytest.raises(HTTPException) as e:
        server.api_series_search_pack_save(server.SearchPackIn(records=[{**DK, "hashtags": []}]))
    assert e.value.status_code == 400 and server.api_series_search_pack()["series"] == []
    assert server.api_series_search_pack_save(server.SearchPackIn(records=[short], source="PACK"))["saved"] == [DK["series_id"]]
    assert server.api_series_search_pack()["series"][0]["search_title"] == DK["search_title"]
    ex = [[d.name, "final_range_88-96.mp4"]]
    prev = server.api_series_search_pack_apply(server.SearchPackApplyIn(exports=ex))["videos"][0]
    assert prev["changed"] == ["title", "description", "hashtags"] and "applied" not in prev
    assert server.load_publish(str(d))["final_range_88-96.mp4"]["title"] == "[88-96] old"        # a preview saves nothing
    done = server.api_series_search_pack_apply(server.SearchPackApplyIn(exports=ex, apply=True))["videos"][0]
    md = server.load_publish(str(d))["final_range_88-96.mp4"]
    assert done["applied"] and md["title"] == "[88-96] He Destroyed The World, Then Regressed | Return of the Apocalypse-Class Death Knight"
    assert md["description"].split("\n")[0].startswith("Return of the Apocalypse-Class Death Knight Chapters 88-96 recap")
    assert md["hashtags"] == DK["hashtags"] and md["tags"] == ["t"]
    again = server.api_series_search_pack_apply(server.SearchPackApplyIn(exports=ex, apply=True))["videos"][0]
    assert again["changed"] == [] and "applied" not in again


def test_a_posted_video_is_never_changed_and_a_series_without_a_pack_is_refused(tmp_path, monkeypatch):
    server, root, d = _lib(tmp_path, monkeypatch)
    ex = [[d.name, "final_range_88-96.mp4"]]
    assert "no search pack" in server.api_series_search_pack_apply(server.SearchPackApplyIn(exports=ex, apply=True))["videos"][0]["refused"]
    server.api_series_search_pack_save(server.SearchPackIn(records=[DK]))
    server.save_publishes(str(d), {"final_range_88-96.mp4": {"results": [{"status": "published", "network": "youtube"}]}})
    r = server.api_series_search_pack_apply(server.SearchPackApplyIn(exports=ex, apply=True))["videos"][0]
    assert "already posted" in r["refused"]
    assert server._apply_search_pack(str(d), "final_range_88-96.mp4") is None
    assert server.load_publish(str(d))["final_range_88-96.mp4"]["title"] == "[88-96] old"


def test_export_snapshot_carries_the_pack_and_matches_series_ids(tmp_path, monkeypatch):
    import series_pack
    import watchlist as W
    server, root, d = _lib(tmp_path, monkeypatch)
    s = W.add_series(root, "The Extra’s Academy Survival Guide")           # id: the-extra-s-academy-survival-guide
    W.add_mirror(root, s["id"], "https://www.webtoons.com/en/fantasy/the-extras-academy-survival-guide/list?title_no=6465")
    c = tmp_path / "the-extras-academy-survival-guide-6465_119"
    (c / "exports").mkdir(parents=True)
    (c / "exports" / "final.mp4").write_bytes(b"v")
    (c / "project.json").write_text(json.dumps({"id": c.name, "series": "The Extra's Academy Survival Guide",
                                                "series_id": "the-extras-academy-survival-guide-6465", "chapter": "119"}))
    series_pack.save(server._yt_root(), {"series_id": "the-extras-academy-survival-guide-6465",
                                         "title_lock": {"series_hook": "Disowned Noble Forced to SAVE THE WORLD", "approved_by_user": True}})
    server.api_series_search_pack_save(server.SearchPackIn(records=[EXTRA]))
    row = next(x for x in server.api_growth_snapshot()["series"] if x["series_id"] == s["id"])
    assert row["made_through"] == 119 and row["hook_locked"] and row["hook_text"].startswith("Disowned Noble")
    assert row["search_title"] == "The Extra's Academy Survival Guide" and row["hook_status"] == "locked"
    assert row["pack_hashtags"] == EXTRA["hashtags"] and row["characters"] == ["Ed Rothstaylor"]
