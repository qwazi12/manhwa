"""Google Drive copy (rebuild step 6): folders by path, resumable upload in
chunks, a re-copy replaces the old file, the no-storage error is explained,
clips are freed only after a confirmed copy of the newest video, and nothing
runs without the Railway variables. Fake Drive; no network, no real key."""
import io
import json
import os
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


class Resp(io.BytesIO):
    def __init__(self, body=b"{}", headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDrive:
    def __init__(self, quota=False):
        self.folders, self.files, self.calls, self.trashed, self.quota = {}, {}, [], [], quota
        self.n = 0

    def __call__(self, req):
        url, m = req.full_url, req.get_method()
        self.calls.append((m, url.split("?")[0]))
        assert "supportsAllDrives=true" in url
        if self.quota and "upload" in url and m == "POST":
            raise urllib.error.HTTPError(url, 403, "x", {}, io.BytesIO(b'{"error":{"errors":[{"reason":"storageQuotaExceeded"}]}}'))
        if m == "GET" and url.split("?")[0].endswith("/files"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
            name = q.split("name = '")[1].split("'")[0]
            parent = q.split("' in parents")[0].split("'")[-1]
            pool = self.folders if "folder" in q else self.files
            hits = [{"id": i} for i, (nm, par) in pool.items() if nm == name and par == parent and i not in self.trashed]
            return Resp(json.dumps({"files": hits}).encode())
        if m == "POST" and "uploadType=resumable" in url:
            body = json.loads(req.data)
            self.n += 1
            self.pending = (f"u{self.n}", body["name"], body["parents"][0])
            return Resp(b"", {"Location": "https://upload.example/session1"})
        if m == "POST" and url.split("?")[0].endswith("/files"):
            body = json.loads(req.data)
            self.n += 1
            fid = f"f{self.n}"
            self.folders[fid] = (body["name"], body["parents"][0])
            return Resp(json.dumps({"id": fid}).encode())
        if m == "PATCH":
            self.trashed.append(url.split("/files/")[1].split("?")[0])
            return Resp(b"{}")
        if m == "PUT":
            rng = req.headers.get("Content-range")
            total = int(rng.split("/")[1])
            end = int(rng.split(" ")[1].split("-")[1].split("/")[0]) if total else -1
            self.chunks = getattr(self, "chunks", 0) + 1
            if end + 1 < total:
                raise urllib.error.HTTPError(req.full_url, 308, "resume", {"Range": f"bytes=0-{end}"}, io.BytesIO(b""))
            fid, nm, par = self.pending
            self.files[fid] = (nm, par)
            return Resp(json.dumps({"id": fid, "webViewLink": f"https://drive/{fid}"}).encode())
        if m == "GET" and "/files/" in url:
            return Resp(json.dumps({"id": "root", "name": "Flamingo Remix", "driveId": "0AOl"}).encode())
        raise AssertionError((m, url))


def main():
    import urllib.parse  # noqa
    import drive_store as ds
    os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)
    os.environ.pop("Flamingo_Remix_DRIVE_FOLDER_ID", None)
    check("not configured without the Railway variables", ds.configured() is False)
    check("status says so (no network)", ds.status()["configured"] is False)
    os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = json.dumps({"client_email": "omnistream-bot@manhwa-engine.iam.gserviceaccount.com"})
    os.environ["Flamingo_Remix_DRIVE_FOLDER_ID"] = "0AOlNsRvSE9zcUk9PVA"
    ds._tok.update(value="test-token", exp=9e12)   # no real key in tests
    ds._status_cache.update(at=0, value=None)
    check("configured from the env", ds.configured() and ds.robot().startswith("omnistream-bot"))

    p = tempfile.mkdtemp(prefix="dr_")
    os.makedirs(os.path.join(p, "exports"))
    os.makedirs(os.path.join(p, "clips"))
    open(os.path.join(p, "exports", "final_a.mp4"), "wb").write(b"v" * (ds.CHUNK + 1000))
    open(os.path.join(p, "script.txt"), "w").write("He walks in.")
    open(os.path.join(p, "thumb.jpg"), "wb").write(b"jpg")
    for i in range(3):
        open(os.path.join(p, "clips", f"seg_{i:03d}.mp4"), "wb").write(b"c" * 500000)
    fake = FakeDrive()
    rec = ds.copy_chapter(p, "final_a.mp4", "Murim Psychopath", "44", thumb_path=os.path.join(p, "thumb.jpg"), _open=fake)
    names = {v[0]: v[1] for v in fake.folders.values()}
    check("folders are Series / Ch N under the Shared Drive", names.get("Murim Psychopath") == "0AOlNsRvSE9zcUk9PVA" and "Ch 44" in names)
    check("video, thumbnail and script are uploaded", sorted(nm for nm, _ in fake.files.values()) == ["final_a.mp4", "script.txt", "thumbnail.jpg"])
    check("a big video goes up in chunks", fake.chunks >= 4)
    check("the chapter remembers its Drive folder", ds.record(p)["folder_link"].startswith("https://drive.google.com/drive/folders/")
          and rec["files"]["video"]["link"])
    fake.chunks = 0
    ds.copy_chapter(p, "final_a.mp4", "Murim Psychopath", "44", _open=fake)
    check("a re-copy reuses the folders and bins the old file (no duplicates)",
          len([1 for nm, _ in fake.folders.values() if nm == "Ch 44"]) == 1 and len(fake.trashed) >= 2)
    mb = ds.free_clips(p)
    check("render clips are freed after the copy", mb == 1.5 and not os.listdir(os.path.join(p, "clips")))
    check("...the video itself is kept for posting", os.path.exists(os.path.join(p, "exports", "final_a.mp4")))
    try:
        ds.copy_chapter(p, "final_a.mp4", "S", "1", _open=FakeDrive(quota=True))
        check("no-storage error is explained", False)
    except ds.DriveError as e:
        check("no-storage error is explained", "Shared Drive" in str(e))
    st = ds.status(p, _open=FakeDrive())
    check("status reads the folder name and that it's a Shared Drive", st["ok"] and st["folder_name"] == "Flamingo Remix")

    # server hook: copy after export, free clips only for the newest video
    import server
    calls = []
    saved = server._drive.copy_chapter
    server._drive.copy_chapter = lambda pdir, name, series, ch, thumb_path=None: calls.append((name, series, ch)) or {"folder_link": "x"}
    try:
        q = tempfile.mkdtemp(prefix="drs_")
        os.makedirs(os.path.join(q, "exports")); os.makedirs(os.path.join(q, "clips"))
        json.dump({"series": "Fog Land 9299", "chapter": "2", "url": "https://www.webtoons.com/en/x/fog-land/list?title_no=9299"},
                  open(os.path.join(q, "project.json"), "w"))
        open(os.path.join(q, "exports", "old.mp4"), "wb").write(b"1")
        open(os.path.join(q, "clips", "seg_000.mp4"), "wb").write(b"c" * 100)
        os.utime(os.path.join(q, "exports", "old.mp4"), (1, 1))
        open(os.path.join(q, "exports", "new.mp4"), "wb").write(b"2")
        server._drive_copy(q, "old.mp4")
        check("copying an OLDER video keeps the clips", os.path.exists(os.path.join(q, "clips", "seg_000.mp4")))
        server._drive_copy(q, "new.mp4")
        check("copying the newest video frees the clips", not os.path.exists(os.path.join(q, "clips", "seg_000.mp4")))
        check("the Drive folder uses the clean series name", calls[-1] == ("new.mp4", "Fog Land", "2"))
    finally:
        server._drive.copy_chapter = saved


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
