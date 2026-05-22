#!/usr/bin/env bash
# Smoke test the in-UI update flow without going to GitHub.
#
# Strategy : reset the local repo N commits backwards, restart the service,
# and probe /api/update/check. The dashboard's footer + About tab will then
# show 'N commits behind' and the ⬇️ Mettre à jour button.
#
# Usage : scripts/test-update-flow.sh [BACK]
#   BACK = number of commits to roll back (default 3)
#
# After testing, run scripts/test-update-flow.sh --restore to git pull --ff-only
# + restart back to head.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${DASHBOARD_PORT:-9999}"
BASE="http://127.0.0.1:${PORT}"

restore_to_head() {
    echo "🔄 Restoring to remote HEAD..."
    cd "$REPO"
    git fetch origin --quiet
    git reset --hard origin/main
    git log --oneline -1
    echo ""
    echo "Restarting service..."
    systemctl --user restart gpu-dashboard.service
    sleep 3
    curl -s "$BASE/api/update/check" | python3 -m json.tool
}

if [ "${1:-}" = "--restore" ]; then
    restore_to_head
    exit 0
fi

BACK="${1:-3}"
if ! [[ "$BACK" =~ ^[0-9]+$ ]]; then
    echo "Usage: $0 [N_COMMITS_BACK | --restore]"
    exit 2
fi

cd "$REPO"

echo "📍 Current state :"
git log --oneline -1
echo ""

echo "🔙 Rolling back $BACK commits to simulate 'behind' state..."
TARGET="$(git rev-parse "HEAD~$BACK")"
git reset --hard "$TARGET"
git log --oneline -1
echo ""

echo "🔄 Restarting service..."
if systemctl --user is-active --quiet gpu-dashboard.service; then
    systemctl --user restart gpu-dashboard.service
else
    echo "⚠️  Service not running via systemd. You'll need to manually restart it."
fi
sleep 3

echo "🩺 /api/update/check :"
curl -s "$BASE/api/update/check" | python3 -m json.tool
echo ""

echo "✅ Done. Open $BASE/ → About tab → 'Mise à jour' section to test the UI."
echo "   When done, run :  scripts/test-update-flow.sh --restore"
