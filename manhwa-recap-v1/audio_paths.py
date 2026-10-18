"""The renderer's beat-file contract, shared by diagnostics and timeline checks."""
import os
import hashlib


def beat_audio_path(audio_dir, beat):
    name = beat.get("file") or f"beat_{int(beat['index']):03d}.mp3"
    if os.path.isabs(name):
        return name  # existing projects can carry absolute paths
    # New records are relative to audio/, including slices/. Accept the old
    # project-relative spelling without accidentally adding audio/ twice.
    name = os.path.normpath(name)
    if name.startswith("audio" + os.sep):
        name = name[len("audio") + 1:]
    if name == ".." or name.startswith(".." + os.sep):
        raise ValueError("relative narration file escapes the audio directory")
    return os.path.join(os.fspath(audio_dir), name)


def beat_audio_asset(audio_dir, beat):
    path = beat_audio_path(audio_dir, beat)
    if os.path.isabs(beat.get("file") or ""):
        return "external_" + hashlib.sha256(path.encode()).hexdigest()[:16] + os.path.splitext(path)[1]
    return os.path.relpath(path, audio_dir)
