#!/bin/bash
# setup_cron.sh — Installs:
#   1. A weekly cron job (Monday 02:00) to rescan all datasets and push
#   2. An inotifywait background daemon that fires the scanner when a new
#      top-level directory appears in DATASETS_ROOT (i.e. a new dataset upload)
#
# Run once:  bash setup_cron.sh
# To remove: bash setup_cron.sh --remove

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DATASETS_ROOT="${DATASETS_ROOT:-/home/janus/iwi5-datasets}"
SYNC_SCRIPT="$REPO_DIR/sync.sh"
INOTIFY_SCRIPT="$REPO_DIR/watch_datasets.sh"
INOTIFY_LOG="$REPO_DIR/logs/inotify.log"
CRON_TAG="# dataset-registry-auto"

REMOVE=false
[ "${1:-}" = "--remove" ] && REMOVE=true

mkdir -p "$REPO_DIR/logs"

# ---------------------------------------------------------------------------
# inotifywait watcher script
# ---------------------------------------------------------------------------
cat > "$INOTIFY_SCRIPT" << 'WATCHER'
#!/bin/bash
# watch_datasets.sh — watches DATASETS_ROOT for new directories and triggers sync
DATASETS_ROOT="${DATASETS_ROOT:-/home/janus/iwi5-datasets}"
SYNC_SCRIPT="$(dirname "$0")/sync.sh"
LOG="$(dirname "$0")/logs/inotify.log"

mkdir -p "$(dirname "$LOG")"

echo "[$(date -u)] Watcher started. Monitoring: $DATASETS_ROOT" >> "$LOG"

# Check inotifywait is available
if ! command -v inotifywait &>/dev/null; then
  echo "[$(date -u)] WARNING: inotifywait not found. Install inotify-tools." >> "$LOG"
  echo "Install with: sudo dnf install inotify-tools  (or yum / apt)"
  exit 1
fi

inotifywait -m -e create --format "%T %f" --timefmt "%Y-%m-%dT%H:%M:%S" \
  "$DATASETS_ROOT" 2>>"$LOG" | while read -r timestamp name; do
  # Only react to new directories (new dataset folder)
  if [ -d "$DATASETS_ROOT/$name" ]; then
    echo "[$timestamp] New dataset detected: $name — triggering sync" >> "$LOG"
    # Wait 60s to let the upload finish before scanning
    sleep 60
    bash "$SYNC_SCRIPT" >> "$LOG" 2>&1 || echo "[$timestamp] sync.sh failed (see above)" >> "$LOG"
  fi
done
WATCHER
chmod +x "$INOTIFY_SCRIPT"

# ---------------------------------------------------------------------------
# Install / remove
# ---------------------------------------------------------------------------
if [ "$REMOVE" = true ]; then
  echo "Removing cron jobs tagged [$CRON_TAG]…"
  (crontab -l 2>/dev/null | grep -v "$CRON_TAG") | crontab -
  echo "  Done. Kill the inotifywait process manually if running:"
  echo "  pkill -f watch_datasets.sh"
  exit 0
fi

# Add weekly cron (Monday 02:00 AM)
CRON_LINE="0 2 * * 1 bash $SYNC_SCRIPT >> $REPO_DIR/logs/cron.log 2>&1 $CRON_TAG"
EXISTING=$(crontab -l 2>/dev/null || true)

if echo "$EXISTING" | grep -qF "$CRON_TAG"; then
  echo "Weekly cron already installed — skipping."
else
  echo "Installing weekly cron (Mon 02:00 AM)…"
  (echo "$EXISTING"; echo "$CRON_LINE") | crontab -
  echo "  Added: $CRON_LINE"
fi

# Start inotifywait watcher in the background (persists in current shell session)
echo ""
echo "Starting inotifywait watcher in background…"
echo "  (This only lasts for your current session.)"
echo "  To make it persistent across logins, add this to your ~/.bashrc or a systemd user service:"
echo "  nohup bash $INOTIFY_SCRIPT &"
echo ""

# Check if already running
if pgrep -f "watch_datasets.sh" > /dev/null 2>&1; then
  echo "  Watcher already running (PID $(pgrep -f watch_datasets.sh))."
else
  nohup bash "$INOTIFY_SCRIPT" >> "$INOTIFY_LOG" 2>&1 &
  echo "  Watcher started (PID $!)."
  echo "  Log: $INOTIFY_LOG"
fi

echo ""
echo "Cron jobs now set:"
crontab -l | grep "$CRON_TAG" || true
echo ""
echo "To verify inotifywait is running:  pgrep -af watch_datasets"
echo "To remove everything:              bash setup_cron.sh --remove"
