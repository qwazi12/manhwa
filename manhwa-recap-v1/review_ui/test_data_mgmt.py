"""Export retention + project delete tests (Sessions 26-27).

Exports were never deleted; they only LOOKED like they vanished because the
drawer listed the ACTIVE project's folder only. Deleting projects had no UI at
all. Bulk delete must be partial-success: one undeletable project (the open
one) must not abort the rest.

Run: python3 test_data_mgmt.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import server as srv
import ingest as ing


def _proj(root, name, mb=0.2, export_age_days=None):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    with open(os.path.join(d, "blob.bin"), "wb") as f:
        f.write(b"\0" * int(mb * 1e6))
    if export_age_days is not None:
        p = os.path.join(d, "exports", "final_test.mp4")
        open(p, "wb").write(b"\0" * 1000)
        old = time.time() - export_age_days * 86400
        os.utime(p, (old, old))
    return d


def main():
    r = []
    root = tempfile.mkdtemp(prefix="datamgmt_")
    ing.PROJECTS = root
    active = _proj(root, "active_one")
    srv.active_project_dir = lambda: active
    _proj(root, "old_one", export_age_days=30)      # past retention
    _proj(root, "new_one", export_age_days=1)       # inside retention

    # ---- guards
    ok, why, _ = srv._delete_one_project("active_one")
    r.append(("the OPEN project can never be deleted",
              not ok and "currently open" in why))
    r.append(("path traversal is refused",
              srv._delete_one_project("../etc")[0] is False))
    r.append(("an unknown project is refused",
              srv._delete_one_project("no_such")[1] == "unknown project"))
    r.append(("an internal '_' dir is refused",
              srv._delete_one_project("_jobs")[0] is False))

    # ---- retention prune
    removed = srv.prune_exports()
    names = {x["project"] for x in removed}
    r.append(("an export past the retention window is pruned", "old_one" in names))
    r.append(("an export inside the window is kept", "new_one" not in names))
    r.append(("the kept file is still on disk",
              os.path.exists(os.path.join(root, "new_one", "exports", "final_test.mp4"))))
    r.append(("retention default is 7 days", srv.EXPORT_RETENTION_DAYS == 7))

    # ---- single delete reports freed space
    ok, why, mb = srv._delete_one_project("old_one")
    r.append(("deleting a project removes it and reports freed MB",
              ok and mb > 0 and not os.path.isdir(os.path.join(root, "old_one"))))

    # ---- bulk: partial success
    _proj(root, "bulk_a"); _proj(root, "bulk_b")
    res = srv.delete_project(srv.ProjectDelIn(ids=["bulk_a", "active_one", "bulk_b"]))
    r.append(("bulk delete removes every deletable project",
              set(res["deleted"]) == {"bulk_a", "bulk_b"}))
    r.append(("...and skips the open one WITHOUT aborting the batch",
              [s["id"] for s in res["skipped"]] == ["active_one"]))
    r.append(("...and the open project survives",
              os.path.isdir(os.path.join(root, "active_one"))))
    r.append(("bulk delete reports total freed MB", res["freed_mb"] > 0))

    # ---- duplicate ids are collapsed
    _proj(root, "dup_one")
    res = srv.delete_project(srv.ProjectDelIn(ids=["dup_one", "dup_one"]))
    r.append(("a repeated id is only acted on once", res["deleted"] == ["dup_one"]))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
