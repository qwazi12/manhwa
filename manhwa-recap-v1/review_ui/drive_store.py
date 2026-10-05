"""Google Drive copy of every finished chapter (rebuild step 6, owner plan
2026-10-04; Scrapper's drive_store.py and OmniStream's drive_api.py lessons).

After an export, the video, its thumbnail and the script go to
    <Shared Drive folder> / <Series> / Ch <N> /
in the Flamingo Remix Shared Drive. A service account has no storage of its
own, so the folder MUST be in a Shared Drive where the robot is a Content
manager (Scrapper's first attempt failed exactly there).

Credentials come only from the environment (Railway):
  GOOGLE_SERVICE_ACCOUNT_JSON       — the omnistream-bot key (JSON text)
  Flamingo_Remix_DRIVE_FOLDER_ID    — the Shared Drive / folder id (or DRIVE_FOLDER_ID)
No key, no copy: the export still finishes; the chapter just shows "not copied".

Once a copy is confirmed, the chapter's render clips are deleted to free disk
(they are rebuilt by any later render). The export itself is kept: posting
still uploads from it, and the 7-day rule applies as before.
"""

import json
import mimetypes
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive"
CHUNK = 8 * 1024 * 1024
RECORD = "drive.json"
NO_QUOTA = ("Google Drive refused the upload: the robot has no storage of its own and can only save into a "
            "Shared Drive. Add it to the Shared Drive as a Content manager, and point the folder variable at "
            "a folder inside that Shared Drive.")
_tok = {"value": None, "exp": 0.0}
_lock = threading.Lock()
_status_cache = {"at": 0.0, "value": None}


class DriveError(Exception):
    pass


def folder_id():
    return (os.environ.get("Flamingo_Remix_DRIVE_FOLDER_ID") or os.environ.get("DRIVE_FOLDER_ID") or "").strip()


def _key():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or ""
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise DriveError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON")


def configured():
    return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and folder_id())


def robot():
    try:
        return (_key() or {}).get("client_email")
    except DriveError:
        return None


def _token(_creds_factory=None):
    with _lock:
        if _tok["value"] and time.time() < _tok["exp"] - 120:
            return _tok["value"]
        info = _key()
        if not info:
            raise DriveError("GOOGLE_SERVICE_ACCOUNT_JSON is not set")
        if _creds_factory:
            value, exp = _creds_factory(info)
        else:
            from google.oauth2 import service_account
            import google.auth.transport.requests as gtr
            creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
            creds.refresh(gtr.Request())
            value = creds.token
            exp = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
        _tok.update(value=value, exp=exp)
        return value


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _request(method, url, params=None, body=None, headers=None, raw=None, _open=None, want_headers=False):
    """One Drive call with bounded retries (rule 21/39): 3 tries, exponential
    backoff with jitter, on 429/5xx and network errors."""
    import random
    q = dict(params or {})
    q.setdefault("supportsAllDrives", "true")
    full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(q)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    last = ""
    for attempt in range(3):
        h = {"Authorization": f"Bearer {_token()}", **(headers or {})}
        if body is not None and raw is None:
            h["Content-Type"] = "application/json; charset=UTF-8"
        req = urllib.request.Request(full, data=data, method=method, headers=h)
        try:
            opener = _open or (lambda r: urllib.request.urlopen(r, timeout=120, context=_ctx()))
            with opener(req) as r:
                txt = r.read().decode("utf-8") if hasattr(r, "read") else ""
                out = json.loads(txt) if txt.strip() else {}
                return (out, dict(r.headers)) if want_headers else out
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            if e.code == 308 and want_headers:        # resumable chunk accepted
                return {}, dict(e.headers)
            if "storageQuotaExceeded" in msg or "storage quota" in msg.lower():
                raise DriveError(NO_QUOTA)
            last = f"{e.code}: {msg[:300]}"
            if e.code not in (429, 500, 502, 503, 504):
                raise DriveError(f"Drive refused the request ({last})")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = str(e)[:300]
        time.sleep(min(20, 2 ** attempt + random.random()))
    raise DriveError(f"Drive did not answer after 3 tries ({last})")


def _q(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def find_or_create_folder(name, parent, _open=None):
    q = (f"name = '{_q(name)}' and '{parent}' in parents and "
         "mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    found = _request("GET", f"{API}/files", {"q": q, "fields": "files(id,name)", "includeItemsFromAllDrives": "true",
                                             "corpora": "allDrives"}, _open=_open)
    if found.get("files"):
        return found["files"][0]["id"]
    made = _request("POST", f"{API}/files", {"fields": "id"},
                    body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]}, _open=_open)
    return made["id"]


def _existing(name, parent, _open=None):
    q = f"name = '{_q(name)}' and '{parent}' in parents and trashed = false"
    r = _request("GET", f"{API}/files", {"q": q, "fields": "files(id)", "includeItemsFromAllDrives": "true",
                                         "corpora": "allDrives"}, _open=_open)
    return [f["id"] for f in r.get("files") or []]


def upload(path, name, parent, stop=None, _open=None):
    """Resumable upload in 8 MB chunks; a file of the same name in that folder
    is moved to the bin first (recoverable for 30 days), so a re-render
    replaces its copy instead of piling up duplicates."""
    for fid in _existing(name, parent, _open=_open):
        _request("PATCH", f"{API}/files/{fid}", body={"trashed": True}, _open=_open)
    size = os.path.getsize(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    _, hdr = _request("POST", UPLOAD, {"uploadType": "resumable", "fields": "id,webViewLink"},
                      body={"name": name, "parents": [parent]},
                      headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)},
                      _open=_open, want_headers=True)
    session = hdr.get("Location") or hdr.get("location")
    if not session:
        raise DriveError("Drive did not open an upload session")
    sent, out = 0, {}
    with open(path, "rb") as f:
        while sent < size or size == 0:
            if stop and stop():
                raise DriveError("stopped")
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            rng = f"bytes {sent}-{end}/{size}" if size else "bytes */0"
            out, _h = _request("PUT", session, {}, raw=chunk, headers={"Content-Range": rng,
                                                                       "Content-Type": mime},
                               _open=_open, want_headers=True)
            sent += len(chunk)
            if size == 0:
                break
    return out


def copy_chapter(pdir, video_name, series, chapter, thumb_path=None, stop=None, _open=None):
    """Copy one chapter's finished files; writes <pdir>/drive.json. Returns the record."""
    root = folder_id()
    if not root:
        raise DriveError("Flamingo_Remix_DRIVE_FOLDER_ID is not set")
    s_id = find_or_create_folder(series or "Unknown series", root, _open=_open)
    c_id = find_or_create_folder(f"Ch {chapter}" if chapter else "Chapter", s_id, _open=_open)
    files = {}
    vid = os.path.join(pdir, "exports", video_name)
    files["video"] = upload(vid, video_name, c_id, stop=stop, _open=_open)
    if thumb_path and os.path.exists(thumb_path):
        files["thumbnail"] = upload(thumb_path, "thumbnail" + os.path.splitext(thumb_path)[1], c_id, stop=stop, _open=_open)
    script = os.path.join(pdir, "script.txt")
    if os.path.exists(script):
        files["script"] = upload(script, "script.txt", c_id, stop=stop, _open=_open)
    rec = {"at": time.time(), "video": video_name, "folder_id": c_id,
           "folder_link": f"https://drive.google.com/drive/folders/{c_id}",
           "files": {k: {"id": v.get("id"), "link": v.get("webViewLink")} for k, v in files.items()}}
    tmp = os.path.join(pdir, RECORD + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, os.path.join(pdir, RECORD))
    return rec


def record(pdir):
    try:
        with open(os.path.join(pdir, RECORD), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def free_clips(pdir):
    """Delete the render clips once the Drive copy of the CURRENT video is safe.
    Returns MB freed. Board thumbnails come from the panels, not the clips."""
    d = os.path.join(pdir, "clips")
    freed = 0
    for fn in os.listdir(d) if os.path.isdir(d) else []:
        if fn.startswith("seg_") and fn.endswith(".mp4"):
            p = os.path.join(d, fn)
            try:
                freed += os.path.getsize(p)
                os.remove(p)
            except OSError:
                pass
    return round(freed / 1e6, 1)


def status(projects_root=None, _open=None):
    """For Settings: configured? reachable? which folder? how many copied."""
    now = time.time()
    if _status_cache["value"] and now - _status_cache["at"] < 600 and _open is None:
        out = dict(_status_cache["value"])
    else:
        out = {"configured": configured(), "robot": robot(), "folder_id": folder_id(), "ok": False,
               "folder_name": None, "error": None}
        if out["configured"]:
            try:
                meta = _request("GET", f"{API}/files/{folder_id()}", {"fields": "id,name,driveId"}, _open=_open)
                out.update(ok=True, folder_name=meta.get("name"), shared_drive=bool(meta.get("driveId")))
                if not meta.get("driveId"):
                    out["error"] = "This folder is not in a Shared Drive, so uploads will be refused. " + NO_QUOTA
                    out["ok"] = False
            except DriveError as e:
                out["error"] = str(e)
        _status_cache.update(at=now, value=dict(out))
    if projects_root:
        n = 0
        for p in os.listdir(projects_root) if os.path.isdir(projects_root) else []:
            if os.path.exists(os.path.join(projects_root, p, RECORD)):
                n += 1
        out["copied"] = n
    return out
