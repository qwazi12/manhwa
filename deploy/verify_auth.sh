#!/usr/bin/env bash
# Verify the shared-secret auth gate on a running Recap Studio deployment.
#
# Usage:
#   deploy/verify_auth.sh <base-url> <secret> [user]
# Example (post-deploy, against the live site):
#   deploy/verify_auth.sh https://manhwa.nodepilot.dev "$RECAP_AUTH_TOKEN"
#   deploy/verify_auth.sh https://recap-studio-production.up.railway.app "$RECAP_AUTH_TOKEN"
#
# Exits 0 only if EVERY check passes:
#   - every protected path returns 401 with no credentials
#   - the same paths return non-401 (200/404/422 = past the gate) WITH the secret
#   - a wrong secret returns 401
# A 404/422 with valid auth is a PASS: it means auth was accepted and the route
# ran (the resource just doesn't exist / body failed validation on an empty
# workspace). Only 401/403 means "blocked at the gate".

set -u
BASE="${1:?usage: verify_auth.sh <base-url> <secret> [user]}"
SECRET="${2:?missing secret}"
USER="${3:-recap}"
BASE="${BASE%/}"

PROTECTED=(/api/project /clip/0 /thumb/0 /audio/0 /panelimg/x /export/foo.mp4 /api/preview /api/projects /)
fail=0

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }

echo "== $BASE =="
echo "-- unauthenticated (expect 401) --"
for p in "${PROTECTED[@]}"; do
  c=$(code "$BASE$p")
  ok="OK"; [ "$c" = "401" ] || [ "$c" = "403" ] || { ok="FAIL"; fail=1; }
  printf "  %-18s %s  [%s]\n" "$p" "$c" "$ok"
done

echo "-- POST /api/ingest unauthenticated (expect 401; this is the spend endpoint) --"
# Body is an invalid (non-http) url on purpose: if the gate is OFF the handler
# rejects it with 400 BEFORE starting any job (no scrape, no Gemini/TTS spend);
# if the gate is ON the 401 fires first. Either way this probe never triggers
# a real ingestion.
c=$(code -X POST -H 'Content-Type: application/json' -d '{"url":"not-a-url"}' "$BASE/api/ingest")
ok="OK"; [ "$c" = "401" ] || [ "$c" = "403" ] || { ok="FAIL"; fail=1; }
printf "  %-18s %s  [%s]\n" "POST /api/ingest" "$c" "$ok"

echo "-- authenticated via X-Auth-Token (expect NOT 401/403) --"
for p in /api/project /api/preview /api/projects /; do
  c=$(code -H "X-Auth-Token: $SECRET" "$BASE$p")
  ok="OK"; { [ "$c" = "401" ] || [ "$c" = "403" ] || [ "$c" = "000" ]; } && { ok="FAIL"; fail=1; }
  printf "  %-18s %s  [%s]\n" "$p" "$c" "$ok"
done

echo "-- authenticated via HTTP Basic (curl -u) and cookie (expect NOT 401) --"
c=$(code -u "$USER:$SECRET" "$BASE/api/project")
ok="OK"; { [ "$c" = "401" ] || [ "$c" = "403" ] || [ "$c" = "000" ]; } && { ok="FAIL"; fail=1; }
printf "  %-18s %s  [%s]\n" "basic /api/project" "$c" "$ok"
c=$(code --cookie "auth_token=$SECRET" "$BASE/api/project")
ok="OK"; { [ "$c" = "401" ] || [ "$c" = "403" ] || [ "$c" = "000" ]; } && { ok="FAIL"; fail=1; }
printf "  %-18s %s  [%s]\n" "cookie /api/project" "$c" "$ok"

echo "-- wrong secret (expect 401) --"
c=$(code -H "X-Auth-Token: definitely-wrong" "$BASE/api/project")
ok="OK"; [ "$c" = "401" ] || [ "$c" = "403" ] || { ok="FAIL"; fail=1; }
printf "  %-18s %s  [%s]\n" "wrong header" "$c" "$ok"

echo
if [ "$fail" = "0" ]; then echo "RESULT: PASS — gate is live and enforcing."; else echo "RESULT: FAIL — see [FAIL] rows above."; fi
exit "$fail"
