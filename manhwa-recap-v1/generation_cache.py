"""Content identities for generation artifacts. Never stores API credentials.

Unknown or edited caches require deliberate review/rerun, never paid refresh.
The caller owns the small, explicit allowlist of effective generation inputs.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

VERSION = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    except FileNotFoundError:
        return None


def code_identity(root, names):
    return {name: file_digest(Path(root) / name) for name in names}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record(path, inputs, outputs):
    hashes = {name: file_digest(out) for name, out in outputs.items()}
    if not hashes or any(v is None for v in hashes.values()):
        raise ValueError("Cannot verify missing generation outputs")
    rec = {"version": VERSION, "input_digest": digest(inputs),
           "inputs": inputs, "outputs": hashes}
    write_json(path, rec)
    return rec


def status(path, inputs, outputs):
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(rec, dict) or rec.get("version") != VERSION
                or not isinstance(rec.get("outputs"), dict)
                or digest(rec["inputs"]) != rec["input_digest"]):
            return "unknown"
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"
    if rec["input_digest"] != digest(inputs):
        return "stale"
    hashes = {name: file_digest(out) for name, out in outputs.items()}
    if any(v is None for v in hashes.values()):
        return "incomplete"
    return "current" if hashes == rec["outputs"] else "edited"


def require_current(path, inputs, outputs, step):
    state = status(path, inputs, outputs)
    if state != "current":
        raise RuntimeError(
            f"{step} cache is {state}; existing work preserved. Review it, then "
            f"explicitly rerun {step} to refresh (may incur normal generation costs).")


def script_inputs(pdir, panels, bible, direct, title, alias, model):
    import direct_speech
    root = Path(__file__).parent
    # Include actual crop bytes as well as the normalized evidence fed to the
    # writer. Referenced panels are not proof of semantic narration coverage.
    sources = {}
    for p in panels:
        name = Path(p.get("file") or f"{p['panel_id']}.png").name
        sources[name] = file_digest(Path(pdir) / "crops" / name)
    return json.loads(json.dumps({"panels": panels, "source_images": sources, "bible": bible,
            "direct": direct_speech.enabled(direct), "title": title, "alias": alias, "model": model,
            "descriptions_sha256": file_digest(Path(pdir) / "descriptions.json"),
            "code": code_identity(root, ["narrate.py", "ocr_lines.py", "direct_speech.py",
                                          "series_bible.py", "panel_stats.py", "matcher.py"])}))


def voice_inputs(beats, engine):
    import speech_text
    return {"beats": [{"index": b["index"], "silent": bool(b.get("silent")),
                       "text": speech_text.speakable(b.get("text") or "")}
                      for b in beats],
            "voice": {k: engine.get(k, "") for k in ("provider", "model", "voice", "style")},
            "code": code_identity(Path(__file__).parent, ["gemini_tts.py", "speech_text.py"])}


def promote_sources(pdir, staging):
    """Promote a successful staged scrape/split, archiving replaced originals.

    Unchanged trees retain their existing paths and bytes. Partial filesystem
    failures retain the archive index and staging directory for recovery.
    This is not a multi-directory filesystem transaction.
    """
    pdir, staging = Path(pdir), Path(staging)
    archive = pdir / "_revisions" / ("source-" + uuid.uuid4().hex)
    moved = []
    complete = False
    def tree(path):
        return {str(p.relative_to(path)): file_digest(p) for p in path.rglob("*") if p.is_file()}
    for name in ("pages", "crops"):
        if not (staging / name).is_dir() or not tree(staging / name):
            raise ValueError(f"Refusing empty staged {name}")
    try:
        for name in ("pages", "crops"):
            src, dest = staging / name, pdir / name
            if dest.is_dir() and tree(src) == tree(dest):
                shutil.rmtree(src)
                continue
            if dest.exists():
                archive.mkdir(parents=True, exist_ok=True)
                os.replace(dest, archive / name)
                moved.append(name)
            os.replace(src, dest)
        complete = True
    finally:
        if moved:
            write_json(archive / "index.json", {"step": "source", "paths": moved,
                                                "status": "complete" if complete else "partial"})
    staging.rmdir()
    return str(archive.relative_to(pdir)) if moved else None
