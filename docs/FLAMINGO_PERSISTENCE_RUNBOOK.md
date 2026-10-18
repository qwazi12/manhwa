# Ops database and backup persistence

`review_ui/ops_config.py` uses an explicit `MANHWA_DATA_DIR`, then writable
`/data`, then `review_ui/data`. Production's volume is mounted at `/app/data`.
The old entrypoint linked projects, segments-workspace and review_crops but
omitted `review_ui/data`; its SQLite database and database backups were on the
container filesystem. The pre-migration snapshot predates project sync and has
zero project rows; the current database has 72. Preserve the current database.

## Repair contract

`deploy/persist_ops_data.py` uses only Python's standard library. It copies the
entire ops directory into `/app/data/studio-ops`, takes an online SQLite snapshot
including committed WAL rows, checks database integrity and foreign keys, and
verifies ordinary-file hashes plus source stability. WAL/SHM are not copied
beside the self-contained database; SHM is a transient reader coordination file.
The completed copy has a private `.ops-persistence.json` manifest.

The default CLI mode only prepares the copy. It does not move the source,
switch the running database, load application modules, call providers, change
settings or touch job/project files. Existing conflicting or changed data,
corrupt SQLite, missing mount, copy errors and unrecognized/partial targets
fail closed. The tool never merges or overwrites another target.

`--activate` is used **only before starting the server**. It retains the original
directory as `data.pre-persistence` and links `review_ui/data` to the verified
volume directory. Failed link creation restores the original directory. A new
container uses the existing persistent database; it does not replace it with an
empty copy. The entrypoint refuses to initialize an unprepared target when the
volume already contains projects. An explicit `MANHWA_DATA_DIR` remains an
operator override and is not changed by this repair.

## Existing-production release sequence

1. Verify current live workers, queues, posting windows, volume identity and
   health. Preserve the protected job records and Chapter 34 stop state.
2. In the current container, confirm `review_ui/data`'s realpath, database row
   counts and files. Check that `/app/data` is the actual mount. Do not print
   environment values or application record contents.
3. Transfer the reviewed helper at an exact feature commit into `/tmp` and
   verify its SHA-256 before running it. Prepare only:

   ```sh
   python /tmp/reviewed-persist-helper.py \
     --source /app/manhwa-recap-v1/review_ui/data \
     --target /app/data/studio-ops --prepare-only
   ```

4. Re-run preparation without activation to verify source/copy fingerprints;
   compare database integrity and table counts. Verify protected job hashes.
   Keep the source unchanged until the gated release. If it changes, stop and
   preserve both versions for inspection; do not remove the target to force a
   fresh copy.
5. Create a current Railway **volume** backup after preparation and confirm it
   is available as a restore point. Database snapshots and per-project archives
   are partial recovery evidence, not full-volume backups. Never restore over
   production merely to test a backup.
6. Recheck all release gates, exact candidate SHA, tests and scheduled work.
   Merge only the approved combined PR once; allow normal automatic deployment.
   The entrypoint activates the prepared path before uvicorn starts.
7. Verify exact deployed commit, `/health`, runtime realpath, 72 retained project
   rows, backup directory on the volume, protected job hashes and existing
   projects/queues/settings. A startup pre-migration backup should now contain
   the populated database. Keep original recovery copies.

Rollback must retain `/app/data/studio-ops` and all project volume data. An older
image lacks this mapping; preserve the persistence entrypoint repair or use a
verified operator storage override in an idle recovery window. Do not reset a
volume or discard either database to make a rollback start.

## October 18 preparation evidence

The reviewed helper at `6218d1107d74212439570fd342aef0cb8cff15f0`, SHA-256
`04ba1a16f798ff3dfd7ddbc0c93c4a7d7d812385f49df3ed22cfe5b138a3241d`,
prepared three live files without activation. Original and copy both have 72
project rows; all nine record files for the four historical jobs and stopped
Chapter 34 match their pre-copy hashes. A fresh Railway volume backup is listed
at **2026-10-18 14:38 Eastern, 21.08 GB**; the older October 5 backup remains.
The dashboard shows no automatic volume-backup schedule. No schedule, account,
publishing destination or alert threshold was changed by this repair.

The volume subsequently reports 50,000 MB, with the same deployment/replica still
online. Its old notification refers to an 80% capacity event five days earlier;
keep the distinction between current capacity and the platform notification's
resolution state explicit. Consult the latest verification report/memory for
whether activation and release have actually happened.

Offline regression command: `python3 -m pytest deploy/test_ops_persistence.py -q`.
It covers committed WAL rows, backup recovery across container replacement,
idempotent startup, collision/corruption/source drift, failed copy/link and the
real entrypoint preserving a protected fixture job. No real providers or data
are loaded.
