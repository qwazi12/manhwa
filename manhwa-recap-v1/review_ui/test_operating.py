"""Operating pieces added 2026-11-14: channel registry, chapter-level release
calendar, storage retention. Local files only."""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import channels as ch  # noqa: E402
import release_calendar as cal  # noqa: E402
import retention as ret  # noqa: E402

DAY = 86400.0
NOW = 1_800_000_000.0


# ------------------------------------------------------------------ channels

def _reg():
    return {"version": 1, "default_channel": "flamingo", "channels": [
        {"id": "flamingo", "name": "Flamingo Remix", "targets": ["mk:youtube"], "genres": ["murim"],
         "series": ["stellar", "hound"], "voice": {"provider": "gemini", "voice": "Charon"},
         "branding": {"badge_colour": "#FF5F1F"}, "packaging": {"privacy": "public"},
         "schedule": {"timezone": "America/New_York", "slots": ["10:00", "22:00"], "daily_max": 4}},
        {"id": "night", "name": "Night Tower", "targets": ["nt:youtube", "nt:tiktok"], "api_key_env": "UPLOADPOST_API_KEY_NIGHT",
         "genres": ["horror", "regression"], "series": ["fog-land"]}]}


def test_series_go_to_their_channel_then_genre_then_default():
    r = _reg()
    assert ch.problems(r) == []
    assert ch.targets_for(r, "fog-land") == ["nt:youtube", "nt:tiktok"]         # explicit assignment
    assert ch.channel_for(r, "new-series", ["Horror"])["id"] == "night"         # genre focus
    assert ch.channel_for(r, "new-series", ["romance"])["id"] == "flamingo"     # default
    assert ch.channel_for(r, "stellar", ["horror"])["id"] == "flamingo"         # assignment beats genre
    s = ch.summary(r, ["stellar", "fog-land", "orphan"])
    assert s["unassigned_series"] == ["hound", "orphan"][1:] or s["unassigned_series"] == ["orphan"]


def test_invalid_registries_are_refused_with_every_reason():
    r = _reg()
    r["channels"][1]["series"].append("stellar")                  # one series, two channels
    r["channels"][1]["targets"] = ["tiktok"]                      # not profile:network
    r["channels"][0]["schedule"]["slots"] = ["25:00"]
    r["channels"][0]["colour"] = "x"                              # unknown field
    r["default_channel"] = "nowhere"
    bad = ch.problems(r)
    for want in ("assigned to both", "targets", "HH:MM", "unknown field", "default_channel"):
        assert any(want in b for b in bad), (want, bad)
    assert ch.problems({"channels": []}) and ch.problems([]) and ch.problems({"channels": [{"id": "A b"}]})
    r = _reg()
    r["channels"].append(dict(r["channels"][0]))
    assert any("used twice" in b for b in ch.problems(r))


def test_a_credential_in_the_registry_is_refused():
    r = _reg()
    r["channels"][0]["api_key_env"] = "sk_live_abcdef"
    assert any("NAME" in b or "credential" in b for b in ch.problems(r))
    r = _reg()
    r["channels"][0]["notes"] = "key is " + "ghp_" + "x" * 20     # pasted into a free-text field
    assert any("credential" in b for b in ch.problems(r))
    assert ch.missing_credentials(_reg(), {"UPLOADPOST_API_KEY": "set"}) == [
        {"channel": "night", "variable": "UPLOADPOST_API_KEY_NIGHT"}]


def test_no_file_is_one_implicit_channel_and_a_broken_file_fails_loudly(tmp_path):
    root = str(tmp_path)
    r = ch.load(root, ["mk:youtube"])
    assert r["implicit"] and ch.targets_for(r, "anything") == ["mk:youtube"]
    ch.save(root, _reg())
    assert ch.load(root)["channels"][1]["id"] == "night" and not ch.load(root).get("implicit")
    with pytest.raises(ch.RegistryError):
        ch.save(root, {"channels": []})                           # refused, the good file stays
    assert ch.load(root)["channels"][0]["id"] == "flamingo"
    ch.save(root, _reg())                                         # second save keeps the previous file
    assert os.path.exists(os.path.join(root, ch.NAME + ".prev"))
    with open(os.path.join(root, ch.NAME), "w") as f:
        f.write("{broken")
    with pytest.raises(ch.RegistryError):                         # no silent fallback to a default channel
        ch.load(root, ["mk:youtube"])


def test_default_targets_follow_the_registry_and_locked_videos_keep_theirs(tmp_path, monkeypatch):
    import ingest
    import server
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    d = os.path.join(root, "fog_46")
    os.makedirs(os.path.join(d, "exports"))
    json.dump({"id": "fog_46", "series": "Fog Land", "series_id": "fog-land", "chapter": "46"},
              open(os.path.join(d, "project.json"), "w"))
    monkeypatch.setattr(server, "_series_pack", lambda pdir, refresh=True: {})
    assert server._channel_targets(d) == []                       # no registry: the studio default applies
    ch.save(root, _reg())
    assert server._channel_targets(d) == ["nt:youtube", "nt:tiktok"]
    with open(os.path.join(root, ch.NAME), "w") as f:
        f.write("{broken")
    with pytest.raises(ch.RegistryError):
        server._channel_targets(d)
    out = server.api_channels()
    assert out["ok"] is False and out["problems"]


# ------------------------------------------------------------------ calendar

def _series(**mirror):
    return {"id": "saga", "title": "Saga", "mirrors": [mirror]}


def _iso(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def test_calendar_rows_and_states():
    rel = {"10": [_iso(NOW - 5 * DAY), False], "11": [_iso(NOW - 1 * DAY), False], "12": _iso(NOW - 4 * DAY)}
    s = _series(chapters=["9", "10", "11", "12", "13"], release_dates=rel,
                first_seen={"11": NOW - 20 * 3600, "13": NOW - 3 * 3600})
    day10 = cal._day_start(rel["10"][0])
    states = {("saga", "10"): {"produced": {"at": day10 + 10 * 3600}, "reviewed": {"status": "approved", "at": day10 + 20 * 3600},
                               "published": {"at": day10 + 30 * 3600, "video": "https://youtu.be/a", "verified": True}},
              ("saga", "12"): {"produced": {"at": NOW - 3 * DAY}, "reviewed": {"status": "approved"},
                               "published": {"at": cal._day_start(rel["12"]) + 60 * 3600, "video": "https://youtu.be/b"}},
              ("saga", "11"): {"produced": {"status": "running"}},
              ("saga", "9"): {"published": {"at": NOW - 2 * DAY, "video": "https://youtu.be/c"}}}
    out = cal.build([s], lambda sid, c: states.get((sid, c)), NOW, channel_of=lambda sid: "flamingo")
    rows = {r["chapter"]: r for r in out["rows"]}
    assert rows["10"]["state"] == "on_time" and rows["10"]["hours_to_live"] == 30.0
    assert rows["10"]["source"] == {"available_at": day10, "basis": "release_date", "uncertain_hours": 24.0}
    assert rows["12"]["state"] == "late" and rows["12"]["hours_to_live"] == 60.0
    assert rows["11"]["state"] in ("due", "overdue") and rows["11"]["next"] == "being made"
    assert rows["11"]["detected"]["at"] == NOW - 20 * 3600
    # only a first-seen time: scored from it, flagged as an upper bound
    assert rows["13"]["source"]["basis"] == "first_seen" and rows["13"]["source"]["uncertain_hours"] is None
    assert rows["13"]["state"] == "due" and rows["13"]["next"] == "make it" and rows["13"]["hours_left"] == 45.0
    # no source time at all: listed, never scored
    assert rows["9"]["state"] == "published_source_time_unknown" and "hours_to_live" not in rows["9"]
    sm = out["summary"]
    assert sm["measured"] == 2 and sm["unmeasured"] == 1 and sm["within_48h"] == 1 and sm["within_48h_pct"] == 50.0
    assert sm["by_channel"]["flamingo"]["chapters"] == 5 and sm["by_channel"]["flamingo"]["published"] == 3
    assert sm["releases_last_7_days"] == 3 and sm["releases_per_day_7d"] == 0.43
    assert out["rows"][0]["published"].get("at") is None            # open chapters first


def test_calendar_counts_a_compilation_once_per_day():
    s = _series(chapters=["1", "2", "3"], release_dates={c: _iso(NOW - 2 * DAY) for c in "123"})
    comp = {"at": NOW - DAY, "video": "https://youtu.be/comp"}
    out = cal.build([s], lambda sid, c: {"published": comp}, NOW)
    assert out["summary"]["releases_last_7_days"] == 1 and out["summary"]["measured"] == 3


def test_overdue_and_next_steps():
    s = _series(chapters=["5", "6", "7"], release_dates={c: _iso(NOW - 4 * DAY) for c in "567"})
    states = {("saga", "5"): {"produced": {"at": NOW - DAY}, "reviewed": {"status": "approved"}},
              ("saga", "6"): {"produced": {"at": NOW - DAY}, "reviewed": {"status": "approved"}, "scheduled": {"at": NOW + 3600}},
              ("saga", "7"): {"produced": {"status": "failed"}}}
    rows = {r["chapter"]: r for r in cal.build([s], lambda sid, c: states.get((sid, c)), NOW)["rows"]}
    assert [rows[c]["next"] for c in "567"] == ["schedule it", "waiting for its slot", "fix the failed chapter"]
    assert all(rows[c]["state"] == "overdue" and rows[c]["hours_left"] < 0 for c in "567")


# ----------------------------------------------------------------- retention

def _put(path, size=1000, age_days=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    t = NOW - age_days * DAY
    os.utime(path, (t, t))


def _project(root, pid, series, chapter=None, span=None, posted_days=None, review=None, drive=None, export="final.mp4"):
    d = os.path.join(root, pid)
    meta = {"id": pid, "series_id": series}
    meta.update({"kind": "range", "chapters_start": span[0], "chapters_end": span[1]} if span else {"chapter": str(chapter)})
    os.makedirs(d, exist_ok=True)
    json.dump(meta, open(os.path.join(d, "project.json"), "w"))
    _put(os.path.join(d, "exports", export), 5000, posted_days or 0)
    for sub in ret.WORK_DIRS:
        _put(os.path.join(d, sub, "a.bin"), 2000, posted_days or 0)
    for keepme in ("critique.json", "segments.json", "script.txt", "release_evidence.json"):
        _put(os.path.join(d, keepme), 10)
    _put(os.path.join(d, "exports", "_qa", export + ".qa.json"), 10)
    if posted_days is not None:
        json.dump({export: {"status": "published", "results": [{"status": "published", "published_at": NOW - posted_days * DAY}]}},
                  open(os.path.join(d, "publishes.json"), "w"))
    if review:
        json.dump({export: {"status": review}}, open(os.path.join(d, "reviews.json"), "w"))
    if drive:
        json.dump({"video": export, "verified": drive != "unverified", "files": {"video": {"id": "f"}},
                   **({"sha256": "ab"} if drive == "bound" else {})}, open(os.path.join(d, "drive.json"), "w"))
    return d


@pytest.fixture
def tree(tmp_path):
    root = str(tmp_path)
    _project(root, "s_1", "s", 1, posted_days=40, drive="bound")            # old, archived with checksum
    _project(root, "s_2", "s", 2, posted_days=40, drive="legacy")           # archived, no checksum on record
    _project(root, "s_3", "s", 3, posted_days=40)                           # posted, never archived
    _project(root, "s_4", "s", 4, posted_days=3, drive="bound")             # posted recently
    _project(root, "s_5", "s", 5, review="approved")                        # approved, not posted
    _project(root, "s_6", "s", 6)                                           # still in review
    _project(root, "s_ch1-3", "s", span=(1, 3), posted_days=30, drive="bound", export="r13.mp4")
    _project(root, "s_ch1-4", "s", span=(1, 4), posted_days=20, drive="bound", export="r14.mp4")   # newest posted compilation
    _project(root, "s_7", "s", 7)
    _put(os.path.join(root, "_trash", f"old-thing__{int(NOW - 45 * DAY)}", "f"), 3000, 45)
    _put(os.path.join(root, "_trash", f"new-thing__{int(NOW - 2 * DAY)}", "f"), 3000, 400)   # old files, deleted 2 days ago
    _put(os.path.join(root, "_revisions", "r1", "f"), 3000, 60)
    _put(os.path.join(root, "_ttscache", "c1"), 3000, 60)
    _put(os.path.join(root, "_ttscache", "c2"), 3000, 1)
    return root


def _paths(p, category=None):
    return sorted(c["path"] for c in p["candidates"] if category in (None, c["category"]))


def test_plan_lists_only_what_is_safe_and_says_why_the_rest_is_kept(tree):
    p = ret.plan(tree, now=NOW, queued=[("s_7", "final.mp4")])
    assert _paths(p, "archived") == ["s_1/exports/final.mp4", "s_ch1-3/exports/r13.mp4"]
    work = _paths(p, "work_files")
    for pid in ("s_1", "s_2", "s_3", "s_ch1-3", "s_ch1-4"):
        assert f"{pid}/crops" in work
    for pid in ("s_4", "s_5", "s_6", "s_7"):
        assert f"{pid}/crops" not in work
    assert _paths(p, "trash") == [f"_trash/old-thing__{int(NOW - 45 * DAY)}"] and _paths(p, "tts_cache") == ["_ttscache/c1"]
    why = {k["path"]: k["why"] for k in p["kept"]}
    assert "newest posted compilation" in why["s_ch1-4/exports/r14.mp4"]
    assert "no verified archive copy" in why["s_2/exports/final.mp4"] and "no verified archive copy" in why["s_3/exports/final.mp4"]
    assert "approved, not posted yet" in why["s_5/exports/final.mp4"]
    assert "posting queue" in why["s_7/exports/final.mp4"] and "posting queue" in why["s_7/crops"]
    assert "not posted" in why["s_6/exports/final.mp4"] and "kept for 14" in why["s_4/exports/final.mp4"]
    # evidence and records are never candidates
    every = " ".join(_paths(p))
    for never in ("critique.json", "release_evidence.json", "_qa", "segments.json", "script.txt", "project.json", "reviews.json"):
        assert never not in every
    assert p["reclaimable_bytes"] == sum(c["bytes"] for c in p["candidates"]) and "dry run" in p["note"]
    assert os.path.exists(os.path.join(tree, "s_1", "exports", "final.mp4"))          # nothing changed


def test_a_chapter_inside_a_posted_compilation_counts_as_posted(tree):
    d = _project(tree, "t_2", "t", 2)                                # never posted on its own
    _project(tree, "t_ch1-3", "t", span=(1, 3), posted_days=30, drive="bound", export="r.mp4")
    work = _paths(ret.plan(tree, now=NOW), "work_files")
    assert "t_2/crops" in work
    assert "t_2/exports/final.mp4" not in _paths(ret.plan(tree, now=NOW))    # its own video was never posted: kept
    assert os.path.isdir(os.path.join(d, "crops"))


def test_apply_moves_to_trash_and_can_be_undone(tree):
    with pytest.raises(ValueError):
        ret.apply(tree, [], now=NOW)
    with pytest.raises(ValueError):
        ret.apply(tree, ["everything"], now=NOW)
    with pytest.raises(ValueError):
        ret.apply(tree, ["trash"], now=NOW)                          # purge needs to be asked for by name
    out = ret.apply(tree, ["work_files"], now=NOW, max_bytes=5000)   # scope limit: 2 folders of 2000
    assert len(out["items"]) == 2 and out["freed_bytes"] == 4000 and out["skipped"]
    moved = out["items"][0]
    assert not os.path.exists(os.path.join(tree, moved["path"]))
    dest = [x for x in os.listdir(os.path.join(tree, "_trash")) if x.startswith("retention-")][0]
    assert os.path.exists(os.path.join(tree, "_trash", dest, moved["path"], "a.bin"))   # undo: move it back
    assert os.path.exists(os.path.join(tree, "s_1", "critique.json"))
    assert os.path.exists(os.path.join(tree, "_trash", "retention.log.jsonl"))
    purged = ret.apply(tree, ["trash"], confirm="purge", now=NOW)
    # what was just moved there is NOT purged with it (its files are old, its time in the trash is not)
    assert [d["path"] for d in purged["items"]] == [f"_trash/old-thing__{int(NOW - 45 * DAY)}"]
    assert os.path.exists(os.path.join(tree, "_trash", dest, moved["path"], "a.bin"))
    later = ret.plan(tree, now=NOW + 31 * DAY)
    assert f"_trash/{dest}" in _paths(later, "trash")                # …until its own 30 days have passed
    assert not os.path.exists(os.path.join(tree, "_trash", f"old-thing__{int(NOW - 45 * DAY)}"))
    assert os.path.exists(os.path.join(tree, "_trash", f"new-thing__{int(NOW - 2 * DAY)}"))


def test_windows_come_from_the_environment():
    assert ret.windows({})["WORK_DAYS"] == 14
    assert ret.windows({"RETENTION_WORK_DAYS": "45", "RETENTION_TRASH_DAYS": "junk"}) == {
        **ret.DEFAULTS, "WORK_DAYS": 45}
