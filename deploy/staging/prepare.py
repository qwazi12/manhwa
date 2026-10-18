"""Prepare an offline development snapshot. Does not start or deploy services."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
RECAP = Path("manhwa-recap-v1")


def checked(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes staging root: {path.name}")
    return resolved


def environment(stage: Path, credentials: dict, inherited=None) -> dict:
    """Allowlist OS necessities; never inherit provider/account/DB credentials."""
    stage = stage.resolve()
    inherited = os.environ if inherited is None else inherited
    env = {k: inherited[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR") if k in inherited}
    # HOME must not expose the developer's cloud credentials or CLI sessions.
    env.update(HOME=str(stage / "home"), PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1", NEXT_TELEMETRY_DISABLED="1",
               BACKEND_URL="http://127.0.0.1:8138", FLAMINGO_STAGING="1",
               AUTOPILOT_SCHEDULER="0", MIRROR_DISCOVERY="0", BACKUP_ENABLED="0",
               MANHWA_DATA_DIR=str(stage / "data"),
               DATABASE_URL=f"sqlite:///{stage / 'data/staging.db'}",
               MAX_DAILY_SPEND_USD="0", MAX_DAILY_SPEND_CEILING_USD="0",
               BASIC_AUTH_USER=credentials["user"], BASIC_AUTH_PASSWORD=credentials["password"],
               SHARED_SECRET=credentials["shared_secret"])
    guard = checked(stage, stage / "app/deploy/staging")
    backend = checked(stage, stage / "app" / RECAP / "review_ui")
    env["PYTHONPATH"] = os.pathsep.join((str(guard), str(backend)))
    return env


def prepare(destination: Path, ref="HEAD", dependencies=True):
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    if not branch.startswith("dev/flamingo-"):
        raise ValueError("Prepare from a dev/flamingo-* branch, never main")
    destination = destination.resolve()
    work = (ROOT / "work").resolve()
    if not destination.is_relative_to(work) or destination == work:
        raise ValueError("Destination must be a new directory below repository work/")
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing staging environment")
    sha = subprocess.check_output(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", sha], cwd=ROOT)
    destination.mkdir(parents=True, mode=0o700)
    app = destination / "app"
    app.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(app, filter="data")
    for required in ("prepare.py", "sitecustomize.py", "staging_backend.py"):
        if not (app / "deploy/staging" / required).is_file():
            raise ValueError("Snapshot commit does not include the staging preparation tools")
    # git archive excludes local .env, projects, databases, queues and media.
    # Refuse tracked secrets or mutable state, rather than trusting ignore rules.
    forbidden = list(app.rglob(".env")) + list(app.rglob(".env.local"))
    for relative in (RECAP / "review_ui/projects", RECAP / "review_ui/data",
                     RECAP / "hyperframes/segments-workspace"):
        if (app / relative).exists():
            forbidden.append(app / relative)
    if forbidden:
        raise ValueError("Snapshot contains environment files or mutable application state")
    for name in ("home", "data", "logs", "tmp"):
        (destination / name).mkdir(mode=0o700)
    if dependencies:
        for relative in (RECAP / "web/node_modules", RECAP / "hyperframes/node_modules"):
            source = ROOT / relative
            if not source.is_dir():
                raise ValueError(f"Install pinned local dependencies first: {relative}")
            # A cross-root node_modules symlink broke Next's React client manifest.
            shutil.copytree(source, app / relative, symlinks=True)
    credentials = dict(user="flamingo-staging", password=secrets.token_urlsafe(24),
                       shared_secret=secrets.token_urlsafe(32))
    private = destination / "credentials.json"
    with open(private, "x", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        json.dump(credentials, handle)
    manifest = dict(commit=sha, branch=branch, mode="offline", prepared=True,
                    deployed=False, paid_calls_enabled=False,
                    frontend="http://127.0.0.1:3138", backend="http://127.0.0.1:8138")
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load(stage: Path):
    stage = stage.resolve()
    if not stage.is_relative_to((ROOT / "work").resolve()):
        raise ValueError("Staging directory must be inside this repository's work/")
    manifest = json.loads((stage / "manifest.json").read_text())
    if manifest["mode"] != "offline" or manifest.get("paid_calls_enabled"):
        raise ValueError("This launcher supports offline staging only")
    for relative in ("app", "home", "data", "app/manhwa-recap-v1/review_ui/projects",
                     "app/manhwa-recap-v1/hyperframes/segments-workspace"):
        checked(stage, stage / relative)
    credentials = json.loads((stage / "credentials.json").read_text())
    if any(not credentials.get(key) for key in ("user", "password", "shared_secret")):
        raise ValueError("Staging authentication is required")
    return stage, environment(stage, credentials)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build", "launch"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--owner-approved-launch", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        print(json.dumps(prepare(args.directory, args.ref), indent=2))
        return
    if args.action == "launch" and not args.owner_approved_launch:
        parser.error("Owner approval required before staging launch")
    stage, env = load(args.directory)
    web = stage / "app" / RECAP / "web"
    if args.action == "build":
        subprocess.run(["node", "node_modules/next/dist/bin/next", "build"], cwd=web, env=env, check=True)
        return
    if not (web / ".next/BUILD_ID").is_file():
        parser.error("Build this snapshot before launch")
    # No reload workers. Both listeners are loopback only; no public tunnel.
    import sys
    from contextlib import ExitStack
    handles = ExitStack()
    backend_log = handles.enter_context((stage / "logs/backend.log").open("a"))
    frontend_log = handles.enter_context((stage / "logs/frontend.log").open("a"))
    backend = None
    frontend = None
    try:
        backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "staging_backend:app",
                                    "--host", "127.0.0.1", "--port", "8138"],
                                   cwd=stage / "app", env=env, stdout=backend_log, stderr=subprocess.STDOUT)
        frontend = subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start",
                                     "--hostname", "127.0.0.1", "--port", "3138"],
                                    cwd=web, env=env, stdout=frontend_log, stderr=subprocess.STDOUT)
        print("Starting private local staging at http://127.0.0.1:3138; see staging logs/ for readiness.", flush=True)
        while backend.poll() is None and frontend.poll() is None:
            import time
            time.sleep(0.5)
    finally:
        for child in (frontend, backend):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        handles.close()


if __name__ == "__main__":
    main()
