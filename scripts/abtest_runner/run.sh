#!/bin/zsh -l
# launchd com.flamingo.abtest: every 30 min at :17 and :47. A quiet check costs one log line, no Claude.
# Claude starts only when a public video is still missing its A/B test (abtest.py check exits 3).
D=~/dev/manhwa-ops/abtest; LOG=$D/check.log; LOCK=$D/session.lock
# launchd's login shell does not load nvm: put railway (nvm node bin) and homebrew on PATH explicitly
# order matters: later entries end up first. Homebrew has an old railway 2.1.0 (no --json); the nvm one (5.x) must win.
for d in /opt/homebrew/bin /Library/Frameworks/Python.framework/Versions/3.13/bin ~/.nvm/versions/node/*/bin(N); do PATH=$d:$PATH; done; export PATH
H="python3 $D/abtest.py"
if [ -e $LOCK ] && kill -0 $(cat $LOCK) 2>/dev/null; then echo "$(date -u +%FT%TZ) skipped: session $(cat $LOCK) still running" >> $LOG; exit 0; fi
eval $H check > $D/.check.out 2>&1; rc=$?; line=$(tail -1 $D/.check.out)
echo "$(date -u +%FT%TZ) rc=$rc $line" >> $LOG
if [ "$rc" != "0" ] && [ "$rc" != "3" ]; then
  # check itself broke: alert at most once per 6 h so a broken runner is never silent
  F=$D/.last_fail_alert
  if [ ! -e $F ] || [ $(( $(date +%s) - $(stat -f %m $F) )) -gt 21600 ]; then
    touch $F; osascript -e "display notification \"A/B runner check failing: ${line//\"/'}\" with title \"FLAMINGO A/B tests\" sound name \"Basso\""
  fi
  exit 0
fi
[ "$rc" = "3" ] || exit 0
echo $$ > $LOCK
{
  echo "===== $(date -u +%FT%TZ) session start"
  cd $D
  /Users/kwasiyeboah/.local/bin/claude -p "$(cat $D/PROMPT.md)" --chrome --max-turns 120 \
    --permission-mode dontAsk \
    --allowedTools "Bash(python3 /Users/kwasiyeboah/dev/manhwa-ops/abtest/abtest.py:*)" "Read" "mcp__claude-in-chrome__*"
  code=$?
  echo "===== $(date -u +%FT%TZ) session end (exit $code)"
  eval $H session $code
} >> $D/sessions.log 2>&1
rm -f $LOCK
