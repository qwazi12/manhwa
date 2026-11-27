# A/B test runner (launchd `com.flamingo.abtest`)

Every new public Flamingo Recap video gets a YouTube Studio "Title and thumbnail" test with 3 pairs (plan titles A/B/C + series pictures A/B/C). The owner approved this on 2026-11-15 and restated it on 2026-11-26. YouTube has no API for tests, so a headless Claude session drives Studio in the owner's Chrome. A cron that lives only inside one Claude session is **not** enough: one stopped for 4 days (2026-11-22 → 11-26).

Installed copy: `~/dev/manhwa-ops/abtest/` (these files) + `~/Library/LaunchAgents/com.flamingo.abtest.plist`.

| What | How |
|---|---|
| Schedule | every 30 min at :17 and :47 local time (launchd) |
| Cheap check | `abtest.py check`: queue + YouTube oEmbed, no AI. Exits 3 only when a public, unheld video has no test |
| Session | `claude -p PROMPT.md --chrome --permission-mode dontAsk`. Tools: `Read`, Chrome, and Bash limited to `python3 …/abtest.py` |
| Secret | `abtest.py` reads `SHARED_SECRET` via `railway variables --json` in `~/dev/manhwa`; never printed, never written, never seen by the session |
| Holds | `holds.txt`, one video id per line (e.g. `nem5uWt4JMo`, owner's DEMOND decision) |
| Status | `status.json`: last check, due now, stuck, last session time and exit code |
| Logs | `check.log` (one line per check), `sessions.log` (each Claude run), `launchd.*.log` |
| Alerts | macOS notification: public video > 3 h without a test; check failing (max once per 6 h); session exit ≠ 0 |

## Operate
- Pause: `launchctl bootout gui/$(id -u)/com.flamingo.abtest`
- Resume: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.flamingo.abtest.plist`
- Run now: `launchctl kickstart gui/$(id -u)/com.flamingo.abtest`
- Hold a video: add its id to `holds.txt`. Mark by hand: `python3 abtest.py mark <id> running|failed|not_available|skipped "note"`
- Undo a test: YouTube Studio → video → A/B testing → stop test (the studio ledger can be re-marked).

## Gotchas
- Homebrew has an old `railway` 2.1.0 (no `--json`); `run.sh` puts the nvm one first on PATH.
- python.org Python needs certifi for TLS (`abtest.py` uses it when installed).
- Chrome must be running with the Claude extension, signed in to the channel's Google account. The Mac must be awake.
- YouTube limits thumbnail experiments per day. On that message the session doesn't mark, and the next run retries.
- Studio runs record progress in the studio event log (via `/api/abtest/mark`), not in `memory.md`. Unattended runs never push git, because a push to main deploys.
