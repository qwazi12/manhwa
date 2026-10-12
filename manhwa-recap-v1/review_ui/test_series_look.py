"""One thumbnail look per series (2026-10-12)."""
import json
import os

import thumbnail_studio as t


def test_legacy_looks_fold_into_the_canonical_key(tmp_path, monkeypatch):
    root = str(tmp_path)
    t.save_style(root, "the-extra-s-academy-survival-guide", {"composition": "panel-hero", "approved": False})
    t.save_style(root, "the-extras-academy-survival-guide-6465", {"composition": "cover-center", "approved": True,
                                                                  "approved_at": 10})
    got = t.adopt_legacy_styles(root, [("the-extra-s-academy-survival-guide", "extras"),
                                       ("the-extras-academy-survival-guide-6465", "extras")])
    assert got == {"extras": "the-extras-academy-survival-guide-6465"}
    assert t.load_style(root, "extras")["composition"] == "cover-center"
    # a newer approved canonical look is never replaced
    t.save_style(root, "extras", {"composition": "cover-badge", "approved": True, "approved_at": 99})
    assert t.adopt_legacy_styles(root, [("the-extras-academy-survival-guide-6465", "extras")]) == {}
    assert t.load_style(root, "extras")["composition"] == "cover-badge"


def test_canon_resolver_wins_and_falls_back(monkeypatch):
    monkeypatch.setattr(t, "CANON", lambda m: "fog-land" if "webtoons" in (m.get("url") or "") else None)
    assert t.series_key({"series": "Fog Land 9299", "url": "https://www.webtoons.com/x"}) == "fog-land"
    assert t.series_key({"series": "Fog Land", "url": "https://www.mgeko.cc/x"}) == "fog-land"


def test_new_chapter_matches_the_series_look_and_consistency_lists_the_odd_one(tmp_path, monkeypatch):
    import ingest
    import server
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    monkeypatch.setattr(server, "_yt_root", lambda: str(tmp_path / "_yt"))
    monkeypatch.setattr(t, "CANON", None)
    for n, comp in ((45, "cover-badge"), (44, "cover-badge"), (46, "panel-hero")):
        d = tmp_path / f"murim_{n}"
        (d / "exports").mkdir(parents=True)
        (d / "exports" / "v.mp4").write_bytes(b"v")
        (d / "project.json").write_text(json.dumps({"series": "Murim Psychopath", "chapter": str(n)}))
        t.save_concepts(str(d), {"v.mp4": {"chosen": {"composition": comp}}})
    assert server._series_look(str(tmp_path / "murim_46")) == "cover-badge"     # precedent, no approved look
    t.approve_style(str(tmp_path / "_yt"), "murim-psychopath", {}, composition="cover-badge")
    rep = {r["series"]: r for r in server.api_thumbcopilot_consistency()["series"]}["murim-psychopath"]
    assert rep["look"] == "cover-badge" and [o["project"] for o in rep["off"]] == ["murim_46"]
