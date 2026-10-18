# Isolated local staging (development only)

This prepares the real integrated Next.js/FastAPI studio from a committed
feature-branch snapshot. It does not provision infrastructure or launch a site.
The owner's October 18 testing brief requires approval **before launch**, even
for this local, zero-infrastructure-cost option. No approval has been received.

```sh
python3 deploy/staging/prepare.py prepare work/ch34-private --ref HEAD
python3 deploy/staging/prepare.py build work/ch34-private
# Only after explicit owner launch approval:
python3 deploy/staging/prepare.py launch work/ch34-private --owner-approved-launch
```

Run from the repository. The prepare step uses `git archive`, not the dirty
working tree. It refuses existing destinations and tracked project/environment
state; it copies installed, pinned frontend/renderer dependencies rather than
using a cross-root dependency symlink. Installing packages is a separate step.
Python, ffmpeg, ffprobe and the renderer's Chrome dependency must be available.
The current Mac lacks Tesseract; offline fallback OCR cannot run yet.

Private credentials live in `work/ch34-private/credentials.json` (0600, inside
a 0700 root). Never commit or print them in logs/PRs. Login uses the existing
studio login form. The frontend is **http://127.0.0.1:3138** and backend
**http://127.0.0.1:8138**, bound only to this Mac. These are proposed addresses,
not live URLs until launch and health/auth/proxy checks pass. No tunnel/public
hostname is created. This is not a remotely accessible Railway/Vercel preview.

The snapshot has its own app/project/media tree, SQLite database, HOME and
usage ledger. The environment allowlists OS necessities and supplies new
staging auth; no provider, publishing, production DB, account or CLI credentials
are inherited. Next staging middleware refuses missing auth or any backend
other than the explicit staging loopback address. Normal production behavior
is unaffected because this profile is opt-in and no production code is deployed.

The offline backend wraps the actual application, retaining its normal startup
and data behavior within the snapshot. Scheduling is disabled; publishing,
OAuth/account changes, external backups/Drive, automatic jobs and spend-cap
changes are rejected before routing. Metadata/storyboard editing and local
render controls remain actual app routes. Python workers inherit an external
socket denial guard; loopback and Unix sockets remain available. This is an
application guard, **not an OS-level network sandbox**, and does not constrain
arbitrary native processes. Do not place external credentials in the snapshot.

All model spending is disabled. This launcher deliberately has no paid-mode
switch. Approved paid processing needs a separately reviewed provider-specific
allowlist, explicit key injection and bounded usage settings, preserving the
existing accounting rather than disabling its gates. Never remove the network
guard or raise the zero cap to make an unapproved test pass.

Queue/posting controls are currently disabled and return `staging_disabled`.
**A publishing simulation is not implemented.** Implement an explicitly labeled,
local-only queue simulation before promising the owner that review step.
The normal queue must never be wired to a real account for this test.

Backend/frontend process logs are saved under staging `logs/`; Ctrl-C terminates
both services. A launch failure also terminates its sibling. Inspect logs and
verify authentication, proxy target and health before handing the URL over.
The process launcher intentionally does not assert that spawn means ready.

Boundary verification (no staging deployment, no external services):

```sh
python3 -m pytest deploy/staging/test_staging.py deploy/staging/test_frontend.py -q
```

See `docs/FLAMINGO_CH34_STAGING_TEST.md` for readiness, blocked stages, resource
approval and the actual-media evaluation protocol. Do not copy production
scripts, voices, caches, approvals, queues or autopilot records into staging.
