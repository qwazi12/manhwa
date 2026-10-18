"""Versioned, local final-media QA; enforcement is deliberately opt-in.

Reports and manifests live beside exports, never in a shared/global project.
No function synthesizes, trims, publishes or modifies original media.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

RECAP = str(Path(__file__).resolve().parent.parent)
if RECAP not in sys.path:
    sys.path.insert(0, RECAP)
from audio_paths import beat_audio_path
import media_audio

VERSION = 1
_HASH_CACHE = {}
_LOCK = threading.RLock()
_RUNNING = set()
_ANALYSIS_SLOT = threading.Semaphore(1)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def file_digest(path):
    """Full SHA256, memoized only while inode, size, mtime AND ctime agree."""
    try:
        st = os.stat(path)
        key = (os.path.realpath(path), st.st_dev, st.st_ino, st.st_size,
               st.st_mtime_ns, st.st_ctime_ns)
        with _LOCK:
            hit = _HASH_CACHE.get(key)
        if hit:
            return hit
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        after = os.stat(path)
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (st.st_size, st.st_mtime_ns, st.st_ctime_ns):
            return None  # never associate bytes with a moving file
        value = h.hexdigest()
        with _LOCK:
            if len(_HASH_CACHE) > 4096:
                _HASH_CACHE.clear()
            _HASH_CACHE[key] = value
        return value
    except (OSError, TypeError):
        return None


def _paths(pdir, name):
    name = os.path.basename(name or "")
    if not name.endswith(".mp4"):
        raise ValueError("an MP4 export filename is required")
    base = Path(pdir) / "exports" / "_qa"
    return base / (name + ".manifest.json"), base / (name + ".qa.json")


def _save(path, value):
    path = Path(path)
    tmp = None
    try:
        # QA owns only exports/_qa, not the project/export directories. A
        # late worker must not recreate them after Delete or Unmerge moves
        # the project away, including a move during the atomic save.
        path.parent.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".qa-", dir=path.parent)
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2, allow_nan=False)
        os.replace(tmp, path)
    except FileNotFoundError:
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
    return True


def _selected(pdir):
    review = _read(Path(pdir) / "review.json", {}) or {}
    return [s for s in (_read(Path(pdir) / "segments.json", []) or [])
            if s.get("user_included") and
            (review.get(str(s["seg_index"])) or {}).get("status") != "rejected"]


def _asset(path):
    return {"path": os.fspath(path) if path else "", "sha256": file_digest(path)}


def input_manifest(pdir, context):
    """Current relevant inputs; derived clips may be cleaned without staling QA."""
    if context.get("kind") == "range":
        return {"kind": "range", "components": [
            {"project": c["project"], "name": c["name"],
             "export": file_digest(Path(c["project"]) / "exports" / c["name"]),
             "inputs": identity(c["project"], c["name"]).get("input_digest")}
            for c in context.get("components", [])]}
    segs = []
    for s in _selected(pdir):
        beats = []
        for b in s.get("beats", []):
            try:
                audio = _asset(beat_audio_path(Path(pdir) / "audio", b))
            except (ValueError, KeyError, TypeError):
                audio = {"path": "", "sha256": None}
            beats.append({"index": b.get("index"), "file": b.get("file"),
                          "text": b.get("text", ""), "start": b.get("start"),
                          "end": b.get("end"), "audio": audio})
        segs.append({k: s.get(k) for k in ("seg_index", "panel_id", "start", "end", "dur",
                                         "crop_bbox_norm", "silent_hold", "intentional_pause")})
        segs[-1].update(panel=_asset(s.get("panel_file")), beats=beats)
    return {"kind": "chapter", "segments": segs,
            "speed": context.get("speed", 1.0),
            "cards": [{"role": c["role"], "asset": _asset(c["path"])} for c in context.get("cards", [])],
            "bgm": _asset(Path(pdir) / "bgm.mp3"),
            "voice": {k: v for k, v in (_read(Path(pdir) / "tts.json", {}) or {}).items()
                      if k in ("provider", "model", "voice", "style")},
            "renderer_code": {name: file_digest(Path(RECAP) / "hyperframes" / name)
                              for name in ("render_segments.py", "fast_render.py")},
            "assembly_code": file_digest(Path(RECAP) / "media_concat.py"),
            "renderer": {k: os.environ.get(k, "") for k in
                         ("RENDERER", "RENDER_FPS", "FAST_RENDER_CRF", "FAST_RENDER_PRESET", "EXPORT_LUFS")}}


def capture_export(pdir, name, speed=1.0, cards=(), components=None):
    """Freeze the delivered schedule at export time, not from later board state.

    A legacy export without this evidence remains incomplete until explicitly
    assessed/re-rendered; do not fabricate its speed/cards from a filename.
    """
    speed = float(speed)
    if not 0.25 <= speed <= 4:
        raise ValueError("invalid export speed")
    context = {"kind": "range" if components is not None else "chapter", "speed": speed,
               "cards": [{"role": role, "path": str(path)} for role, path in cards if path]}
    events, cursor, errors = [], 0.0, []
    if components is not None:
        context["components"] = []
        for cp, cn in components:
            cn = os.path.basename(cn)
            child = _read(_paths(cp, cn)[0], {}) or {}
            context["components"].append({"project": str(cp), "name": cn})
            for event in child.get("events", []):
                e = dict(event)
                e.update(start=event["start"] + cursor, end=event["end"] + cursor,
                         source_project=event.get("source_project") or str(cp))
                events.append(e)
            if not child or child.get("capture_errors"):
                errors.append(f"component {cn} lacks a complete render manifest")
            cursor += media_audio.duration(Path(cp) / "exports" / cn)
    else:
        for card in context["cards"]:
            if card["role"] == "intro":
                cursor += media_audio.duration(card["path"])
        for s in _selected(pdir):
            for b in s.get("beats", []):
                try:
                    audio = beat_audio_path(Path(pdir) / "audio", b)
                    seconds = media_audio.duration(audio)
                except (ValueError, OSError, KeyError, subprocess.SubprocessError):
                    audio, seconds = "", max(0, float(b.get("end", 0)) - float(b.get("start", 0)))
                offset = float(b.get("start", 0)) - float(s.get("start", 0))
                events.append({"beat": b.get("index"), "segment": s["seg_index"],
                               "panel": s.get("panel_id"), "audio": str(audio),
                               "text": b.get("text", ""), "source_project": str(pdir),
                               "start": (cursor + offset) / speed,
                               "end": (cursor + offset + seconds) / speed,
                               "speed": speed, "intentional_pause": bool(s.get("intentional_pause") or b.get("intentional_pause"))})
            try:
                cursor += media_audio.duration(Path(pdir) / s["clip"])
            except (ValueError, OSError, KeyError, subprocess.SubprocessError):
                errors.append(f"segment {s['seg_index']} clip duration unavailable")
                cursor += float(s.get("dur", 0))
        for card in context["cards"]:
            if card["role"] == "outro":
                cursor += media_audio.duration(card["path"])
        cursor /= speed
    inputs = input_manifest(pdir, context)
    manifest = {"version": VERSION, "context": context, "events": events,
                "expected_duration": cursor, "capture_errors": errors,
                "input_digest": _digest(inputs), "inputs": inputs,
                "export_digest": file_digest(Path(pdir) / "exports" / name)}
    _save(_paths(pdir, name)[0], manifest)
    return manifest


def identity(pdir, name):
    manifest = _read(_paths(pdir, name)[0], {}) or {}
    current = _digest(input_manifest(pdir, manifest["context"])) if manifest else None
    return {"export_digest": file_digest(Path(pdir) / "exports" / os.path.basename(name)),
            "input_digest": current, "manifest_digest": _digest(manifest) if manifest else None,
            "manifest_current": bool(manifest and current == manifest.get("input_digest"))}


def status(pdir, name):
    manifest_path, report_path = _paths(pdir, name)
    ident = identity(pdir, name)
    report = _read(report_path, {}) or {}
    report_current = bool(report and report.get("version") == VERSION and
                          all(report.get(k) == ident[k] for k in ("export_digest", "input_digest", "manifest_digest")))
    fresh = report_current and ident["manifest_current"]
    state = report.get("status") if report_current else ("stale" if report else "not_checked")
    if state == "passed" and not fresh:
        state = "incomplete"
    with _LOCK:
        running = (os.path.realpath(pdir), name) in _RUNNING
    return {"status": "running" if running else state, "fresh": fresh,
            "enforced": enforcement_enabled(), "findings": report.get("findings", []),
            "checked_at": report.get("checked_at"), "identity": ident,
            "checks": report.get("checks", {}), "metrics": report.get("metrics", {}),
            "note": "Signal checks do not verify spoken words. Thresholds await real-media calibration."}


def enforcement_enabled():
    return os.environ.get("MEDIA_QA_ENFORCE", "0") == "1"


def record_incomplete(pdir, name, error):
    _save(_paths(pdir, name)[1], {"version": VERSION, **identity(pdir, name),
          "status": "incomplete", "checked_at": time.time(), "findings": [
              {"rule": "checker-error", "severity": "critical", "explanation": str(error)[:300]}]})


def blockers(pdir, name, approval=None):
    if not enforcement_enabled():
        return []  # no new production blockers until explicit owner approval
    s = status(pdir, name)
    out = [] if s["fresh"] and s["status"] == "passed" else [
        f"Final-media QA is {s['status']}; run and review the exact export before publishing."]
    approved = (approval or {}).get("media_identity")
    if not approved or approved != s["identity"]:
        out.append("This exact export and its render inputs need human re-approval.")
    return out


def inspect(pdir, name):
    """Inspect an existing manifest/export. Original inputs remain untouched."""
    manifest_path, report_path = _paths(pdir, name)
    manifest = _read(manifest_path, {}) or {}
    before = identity(pdir, name)
    report = {"version": VERSION, **before, "checked_at": time.time(), "findings": [],
              "checks": {"semantic_speech": "not_run", "thresholds": "uncalibrated"}, "metrics": {}}
    findings = report["findings"]

    def finding(rule, severity, explanation, event=None, start=None, end=None):
        e = event or {}
        findings.append({"rule": rule, "severity": severity, "confidence": .9 if severity == "critical" else .6,
                         "explanation": explanation, "beat": e.get("beat"), "segment": e.get("segment"),
                         "panel": e.get("panel"), "source_project": e.get("source_project"),
                         "start": start if start is not None else e.get("start"),
                         "end": end if end is not None else e.get("end"),
                         "suggestion": "Review the affected source audio and timing; repair only the affected stage."})

    incomplete = not manifest or not before["manifest_current"] or bool(manifest.get("capture_errors"))
    if incomplete:
        finding("manifest-incomplete", "warning", "Render-input evidence is absent, changed or incomplete; do not infer a clean export.")
    final = Path(pdir) / "exports" / os.path.basename(name)
    delivered = None
    try:
        info = media_audio.probe(final)
        video = [s for s in info["streams"] if s.get("codec_type") == "video"]
        if not video or not video[0].get("width") or not video[0].get("height"):
            raise ValueError("video stream is missing or has invalid dimensions")
        decoded = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(final),
                                  "-map", "0:v:0", "-an", "-progress", "pipe:1", "-nostats", "-f", "null", "-"],
                                 capture_output=True, text=True, timeout=900)
        frames = re.findall(r"^frame=(\d+)", decoded.stdout, re.M)
        if decoded.returncode or decoded.stderr.strip() or not frames or int(frames[-1]) == 0:
            raise ValueError("video full-decode failed: " + decoded.stderr[-200:])
        delivered = media_audio.measure(final)
        report["metrics"] = {"final_audio": media_audio.public_metrics(delivered),
                             "width": video[0]["width"], "height": video[0]["height"],
                             "frame_rate": video[0].get("avg_frame_rate")}
        report["checks"]["final_decode"] = "passed"
        expected = manifest.get("expected_duration")
        if expected and abs(delivered["duration"] - expected) > max(.3, expected * .002):
            finding("export-duration", "critical", f"Decoded audio is {delivered['duration']:.3f}s; delivered schedule expects {expected:.3f}s.")
        if delivered["clipped_fraction"] > .01:
            finding("final-clipping", "critical" if delivered["clipped_fraction"] > .05 else "warning",
                    "Decoded samples repeatedly approach full scale; inspect possible distortion.")
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        finding("export-broken", "critical", str(exc))
        report["checks"]["final_decode"] = "failed"
    measured = {}
    for event in manifest.get("events", []):
        if not any(c.isalnum() for c in event.get("text", "")):
            continue
        try:
            path = event.get("audio") or ""
            if path not in measured:
                measured[path] = media_audio.measure(path)
            source = measured[path]
            if source["active_seconds"] < min(.06, source["duration"] * .1):
                finding("narration-silent", "critical", "Expected narration has no measurable voice activity.", event)
                continue
            if source["clipped_fraction"] > .01:
                finding("voice-clipping", "critical" if source["clipped_fraction"] > .05 else "warning",
                        "Source narration approaches full scale frequently; inspect possible clipping.", event)
            for a, b in source["silences"]:
                if b - a >= 1.0 and not event.get("intentional_pause"):
                    finding("voice-pause", "warning", f"{b-a:.2f}s of silence inside a narration file.", event,
                            event["start"] + a / event["speed"], event["start"] + b / event["speed"])
            if delivered is not None:
                support = media_audio.correspondence(source, delivered, event["start"], event.get("speed", 1))
                if support < .6:
                    finding("voice-unconfirmed", "critical", f"Only {support:.0%} of expected active audio frames have corresponding signal in the final mix; inspect missing, mismatched or truncated voice.", event)
                if event["end"] > delivered["duration"] + .15:
                    finding("narration-truncated", "critical", "Expected narration extends beyond the delivered soundtrack.", event)
        except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
            finding("narration-broken", "critical", str(exc), event)
    events = sorted(manifest.get("events", []), key=lambda e: e["start"])
    for a, b in zip(events, events[1:]):
        if a.get("source_project") != b.get("source_project"):
            continue  # chapter cards/joins are deliberate boundaries
        gap = b["start"] - a["end"]
        if gap < -.15:
            finding("voice-overlap", "critical", f"Expected narration files overlap by {-gap:.2f}s in the delivered schedule.",
                    b, b["start"], min(a["end"], b["end"]))
        trailing = measured.get(a.get("audio"), {}).get("trailing_silence", 0) / a.get("speed", 1)
        leading = measured.get(b.get("audio"), {}).get("leading_silence", 0) / b.get("speed", 1)
        if gap + leading + trailing > 1.5 and not (a.get("intentional_pause") or b.get("intentional_pause")):
            finding("scheduled-pause", "warning", f"{gap + leading + trailing:.2f}s voice gap including scheduled and embedded silence; review pacing intent.",
                    b, a["end"] - trailing, b["start"] + leading)
    if identity(pdir, name) != before:
        incomplete = True
        finding("inputs-changed", "warning", "Media or render inputs changed while inspection was running; rerun QA.")
    report["status"] = "failed" if any(f["severity"] == "critical" for f in findings) else "incomplete" if incomplete else "passed"
    _save(report_path, report)
    return status(pdir, name)


def start(pdir, name):
    key = (os.path.realpath(pdir), name)
    with _LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)

    def run():
        try:
            with _ANALYSIS_SLOT:
                inspect(pdir, name)
        except Exception as exc:
            # Even an unexpected checker failure must be a visible non-pass.
            record_incomplete(pdir, name, exc)
        finally:
            with _LOCK:
                _RUNNING.discard(key)
    threading.Thread(target=run, daemon=True).start()
    return True
