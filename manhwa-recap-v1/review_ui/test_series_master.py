"""Series master thumbnails, style D (owner, 2026-11-07). A fake Drive only: no network.
Run: python3 -m pytest test_series_master.py -q"""
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import series_master as sm  # noqa: E402
from PIL import Image  # noqa: E402

BADGE = {"fill": "#1E90FF"}


class FakeDrive:
    """Files by (parent, name); download copies the local source."""

    def __init__(self, tmp, master=True, badge=BADGE, master_size=(1280, 720)):
        self.calls, self.src = [], {}
        self.tree = {(sm.FOLDER_ID, "murim-login"): {"id": "F1", "name": "murim-login"}}
        if master:
            p = os.path.join(tmp, "src_master.png")
            Image.new("RGB", master_size, (90, 140, 60)).save(p)
            self.src["M1"] = p
            self.tree[("F1", "MASTER_murim-login.png")] = {"id": "M1", "name": "MASTER_murim-login.png",
                                                           "md5Checksum": hashlib.md5(open(p, "rb").read()).hexdigest()}
        if badge is not None:
            p = os.path.join(tmp, "src_badge.json")
            open(p, "w").write(badge if isinstance(badge, str) else json.dumps(badge))
            self.src["B1"] = p
            self.tree[("F1", "BADGE_murim-login.json")] = {"id": "B1", "name": "BADGE_murim-login.json"}

    def configured(self):
        return True

    def _request(self, method, url, params=None, **k):
        q = params["q"]
        parent = q.split("'")[1]
        name = q.split("name = '")[1].split("'")[0]
        self.calls.append((parent, name))
        f = self.tree.get((parent, name))
        return {"files": [f] if f else []}

    def download(self, fid, dest):
        shutil.copyfile(self.src[fid], dest)


def test_no_master_keeps_todays_thumbnail(tmp_path):
    ds = FakeDrive(str(tmp_path), master=False)
    assert sm.upload_thumbnail("murim-login", "251", "/x/own.jpg", str(tmp_path / "w"), ds=ds) == "/x/own.jpg"
    assert sm.upload_thumbnail("other-series", "5", "/x/own.jpg", str(tmp_path / "w"), ds=ds) == "/x/own.jpg"


def _near(p, q, tol=30):
    return sum(abs(a - b) for a, b in zip(p, q)) < tol


def test_master_gets_the_style_d_badge(tmp_path):
    ds, logs = FakeDrive(str(tmp_path)), []
    out = sm.upload_thumbnail("murim-login", "251-252", None, str(tmp_path / "w"), ds=ds,
                              log=lambda m, level="ok": logs.append((level, m)))
    im = Image.open(out).convert("RGB")
    assert im.size == (1280, 720) and os.path.getsize(out) <= sm.MAX_BYTES
    assert _near(im.getpixel((28, 70)), (0, 0, 0))            # 6 px black outline at x=26
    assert _near(im.getpixel((37, 70)), (30, 144, 255))       # series fill inside it
    assert _near(im.getpixel((100, 24)), (0, 0, 0))           # top outline at y=22
    assert _near(im.getpixel((100, 18)), (90, 140, 60), 12)   # art above the box is untouched
    box = im.crop((46, 30, 300, 118))
    assert sum(1 for p in box.getdata() if min(p) > 230) > 800   # white "CH 251-252" text
    assert _near(im.getpixel((900, 500)), (90, 140, 60), 12)  # the rest of the master is untouched
    assert logs and logs[0][0] == "ok" and "CH 251-252" in logs[0][1]


def test_badge_geometry_follows_the_spec():
    im = Image.new("RGB", (1280, 720), (90, 140, 60))
    x0, y0, x1, y1 = sm.draw_badge(im, "CH 46", "#D61E2D")
    assert (x0, y0, y1 - y0) == (26, 22, 104)
    assert _near(im.getpixel((x1 - 30, y1 + 5)), (0, 0, 0))   # shadow box at (+6, +7): below
    assert _near(im.getpixel((x1 + 4, y0 + 40)), (0, 0, 0))   # and to the right
    assert _near(im.getpixel((x1 + 4, y0 + 2)), (90, 140, 60), 12)
    wide = sm.draw_badge(Image.new("RGB", (1280, 720)), "CH 251-252", "#D61E2D")
    assert wide[2] - wide[0] > x1 - x0                         # the box grows with the label


def test_colour_comes_from_badge_file_then_spec_table(tmp_path):
    assert sm.series_colour("the-stellar-swordmaster") == "#1E6EE6"
    assert sm.series_colour("the-stellar-swordmaster", {"fill": "#00FF00"}) == "#00FF00"
    ds = FakeDrive(str(tmp_path), badge=None)                  # master, no BADGE file, no spec colour
    logs = []
    got = sm.upload_thumbnail("murim-login", "251", "/x/own.jpg", str(tmp_path / "w"), ds=ds,
                              log=lambda m, level="ok": logs.append((level, m)))
    assert got == "/x/own.jpg" and logs[0][0] == "warn" and "colour" in logs[0][1]


def test_problems_fall_back_and_say_why(tmp_path):
    cases = (("{not json", (1280, 720)), (dict(fill="red"), (1280, 720)), (BADGE, (1000, 1000)))
    for i, (bad, size) in enumerate(cases):
        logs = []
        d = tmp_path / str(i)
        d.mkdir()
        ds = FakeDrive(str(d), badge=bad, master_size=size)
        got = sm.upload_thumbnail("murim-login", "251", "/x/own.jpg", str(d / "w"), ds=ds,
                                  log=lambda m, level="ok": logs.append((level, m)))
        assert got == "/x/own.jpg" and logs and logs[0][0] == "warn", bad


def test_off_switch_and_missing_label(tmp_path, monkeypatch):
    ds = FakeDrive(str(tmp_path))
    monkeypatch.setenv("SERIES_MASTER_OFF", "1")
    assert sm.upload_thumbnail("murim-login", "251", "/x/own.jpg", str(tmp_path / "w"), ds=ds) == "/x/own.jpg"
    assert ds.calls == []                                   # nothing asked of Drive
    monkeypatch.delenv("SERIES_MASTER_OFF")
    assert sm.upload_thumbnail("murim-login", None, "/x/own.jpg", str(tmp_path / "w"), ds=ds) == "/x/own.jpg"


def test_names_are_quoted_in_the_drive_query():
    assert sm._q("o'neil") == "o\\'neil"


def test_publish_job_hook_passes_series_and_merged_label(tmp_path, monkeypatch):
    import server
    seen = []
    monkeypatch.setattr(sm, "upload_thumbnail",
                        lambda sid, label, fb, work, ds=None, log=None: seen.append((sid, label, fb)) or "M.jpg")
    for meta, want in (({"kind": "range", "series_id": "murim-login", "chapters_start": 251, "chapters_end": 252},
                        ("murim-login", "251-252", "own.jpg")),
                       ({"series_id": "fog-land", "chapter": "46"}, ("fog-land", 46, "own.jpg"))):
        d = tmp_path / str(len(seen))
        d.mkdir()
        (d / "project.json").write_text(json.dumps(meta))
        assert server._series_master_thumb(str(d), "own.jpg") == "M.jpg"
        assert seen[-1] == want
