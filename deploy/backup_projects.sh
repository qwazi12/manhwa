#!/bin/sh
# E7: pull every project's paid artifacts (descriptions, TTS audio, scripts,
# segments, review state — no clips/crops) off the Railway volume into a
# dated local backup dir. Run from anywhere; needs the railway CLI linked
# to the recap-studio project (for SHARED_SECRET) and curl.
#
#   ./deploy/backup_projects.sh              # -> ~/dev/manhwa-backups/<date>/
#
# Volume data is the same risk class as unpushed code: one bad deploy or
# volume loss burns real Gemini/TTS spend. Cron- or session-start-friendly.
set -eu
BASE="https://recap-studio-production.up.railway.app"
DEST="${1:-$HOME/dev/manhwa-backups/$(date +%Y-%m-%d)}"
SECRET=$(cd "$(dirname "$0")/.." && railway variables --json 2>/dev/null \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('SHARED_SECRET',''))")
[ -n "$SECRET" ] || { echo "could not read SHARED_SECRET from railway" >&2; exit 1; }
mkdir -p "$DEST"
IDS=$(curl -sf -H "x-shared-secret: $SECRET" "$BASE/api/projects" \
  | python3 -c "import sys,json;[print(p['id']) for p in json.load(sys.stdin).get('projects',[])]")
[ -n "$IDS" ] || { echo "no projects listed"; exit 0; }
for id in $IDS; do
  echo "backing up $id ..."
  if curl -sf -H "x-shared-secret: $SECRET" "$BASE/api/backup/$id" \
      -o "$DEST/$id.tar.gz" 2>/dev/null; then
    echo "  -> $DEST/$id.tar.gz ($(du -h "$DEST/$id.tar.gz" | cut -f1))"
  else
    rm -f "$DEST/$id.tar.gz"
    echo "  skipped $id (not a backupable project dir)"
  fi
done
echo "done: $DEST"
