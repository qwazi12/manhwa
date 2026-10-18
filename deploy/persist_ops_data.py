"""Preserve review_ui/data on the mounted volume before starting the server.

No providers, jobs, settings or publication records are loaded. On a running
old container use --prepare-only, then verify the volume backup before release.
The entrypoint activates the link only before uvicorn starts. The original
directory is retained; conflicting data fails closed instead of being merged.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile


MARKER = ".ops-persistence.json"


def fingerprint(directory: Path) -> dict[str, str]:
    result = {}
    if not directory.exists():
        return result
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Refusing a symlink inside the source data directory")
        if path.is_file():
            # SQLite readers update the SHM coordination index. It contains no
            # durable rows and is never copied; WAL bytes remain checked.
            if path == directory / "manhwa.db-shm":
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[str(path.relative_to(directory))] = digest.hexdigest()
        elif not path.is_dir():
            raise RuntimeError("Refusing a non-regular data file")
    return result


def check_database(path: Path) -> None:
    if not path.is_file():
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RuntimeError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("SQLite foreign-key check failed")


def _snapshot(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
            # Produce a self-contained DB, not one requiring new WAL files.
            copied.execute("PRAGMA journal_mode=DELETE")
    check_database(destination)


def prepare(source: Path, target: Path, *, activate: bool = False,
            require_prepared: bool = False) -> dict:
    source, target = source.absolute(), target.absolute()
    if source.is_symlink():
        if source.resolve() == target.resolve() and target.is_dir():
            check_database(target / "manhwa.db")
            return {"state": "already_linked", "target": str(target)}
        raise RuntimeError("Source already links to another location; inspect it first")
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise RuntimeError("Target must be a real directory")
    if source == target or source in target.parents or target in source.parents:
        raise RuntimeError("Source and target must be separate directories")
    if not target.parent.is_dir():
        raise RuntimeError("Persistent mount is unavailable; refusing ephemeral fallback")
    if source.exists() and not source.is_dir():
        raise RuntimeError("Source must be a directory")

    before = fingerprint(source)
    state = "existing"
    if target.exists():
        marker_path = target / MARKER
        if not marker_path.is_file():
            raise RuntimeError("Existing target has no migration record; refusing overwrite")
        marker = json.loads(marker_path.read_text())
        if marker.get("version") != 1:
            raise RuntimeError("Unrecognized migration record")
        if "manhwa.db" in marker.get("copied_files", {}) and not (target / "manhwa.db").is_file():
            raise RuntimeError("Prepared database is missing; refusing an empty replacement")
        # A new image has no local data. A prepared old container may still have
        # its original directory; it must not have changed since preparation.
        if before and before != marker.get("source_files"):
            raise RuntimeError("Local data changed or conflicts with the persistent copy")
        # Before first activation ensure the prepared files have not drifted.
        if before:
            actual = fingerprint(target)
            actual.pop(MARKER, None)
            if actual != marker.get("copied_files"):
                raise RuntimeError("Prepared persistent copy changed; inspect it first")
        check_database(target / "manhwa.db")
    else:
        if require_prepared:
            raise RuntimeError("Existing studio needs a verified pre-release data copy")
        staging = Path(tempfile.mkdtemp(prefix=".ops-migration-", dir=target.parent))
        try:
            for name in before:
                # SQLite's online backup folds committed WAL data into the new
                # database; copying WAL/SHM alongside it would corrupt recovery.
                if name in {"manhwa.db-wal", "manhwa.db-shm"}:
                    continue
                old, new = source / name, staging / name
                new.parent.mkdir(parents=True, exist_ok=True)
                if name == "manhwa.db":
                    _snapshot(old, new)
                else:
                    shutil.copy2(old, new)
                new.chmod(0o600)
                if name != "manhwa.db" and fingerprint_file(new) != before[name]:
                    raise RuntimeError("Copied data checksum differs")
            if fingerprint(source) != before:
                raise RuntimeError("Source changed during preparation; retry when idle")
            copied = fingerprint(staging)
            (staging / MARKER).write_text(json.dumps({
                "version": 1, "source": str(source),
                "source_files": before, "copied_files": copied,
            }, indent=2) + "\n")
            (staging / MARKER).chmod(0o600)
            # Exclusive mkdir never replaces another operator's destination.
            # Publish the marker last: interrupted promotion fails closed on
            # the next invocation, retaining source + partial target to inspect.
            target.mkdir(mode=0o700)
            for child in staging.iterdir():
                if child.name != MARKER:
                    child.rename(target / child.name)
            (staging / MARKER).rename(target / MARKER)
            state = "prepared"
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    if activate:
        retained = source.with_name(source.name + ".pre-persistence")
        if retained.exists() or retained.is_symlink():
            raise RuntimeError("Retained original already exists; refusing overwrite")
        moved = source.exists()
        if moved:
            source.rename(retained)
        try:
            source.symlink_to(target, target_is_directory=True)
        except Exception:
            if moved:
                retained.rename(source)
            raise
        state = "linked"
    return {"state": state, "target": str(target), "source_files": len(before)}


def fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true",
                      help="Copy only (the default); do not switch the running server")
    mode.add_argument("--activate", action="store_true",
                      help="Link the verified copy before starting the server, never while live")
    parser.add_argument("--require-prepared-if-projects-exist", action="store_true")
    arguments = parser.parse_args()
    projects = arguments.target.parent / "projects"
    require_prepared = (arguments.require_prepared_if_projects_exist and
                        projects.is_dir() and any(projects.iterdir()))
    print(json.dumps(prepare(arguments.source, arguments.target,
                             activate=arguments.activate,
                             require_prepared=require_prepared)))
