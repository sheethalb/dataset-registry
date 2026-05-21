#!/bin/bash
# sync.sh — Copies the local datasets.json into the git repo and pushes to GitHub.
# GitHub Pages will then serve the updated dashboard automatically.
#
# Usage:
#   ./sync.sh                  # scan + sync
#   ./sync.sh --no-scan        # skip re-scanning, just sync existing JSON
#   ./sync.sh --dataset MIMIC  # rescan one dataset, then sync

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — edit these if needed
# ---------------------------------------------------------------------------
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"     # this script lives in the repo root
DATASETS_ROOT="${DATASETS_ROOT:-/home/janus/iwi5-datasets}"
REGISTRY_JSON="${DATASETS_ROOT}/.registry/datasets.json"
PYTHON="${PYTHON:-/home/woody/iwi5/iwi5145h/software/private/conda/envs/mri/bin/python}"
# ---------------------------------------------------------------------------

NO_SCAN=false
SINGLE_DATASET=""

for arg in "$@"; do
  case $arg in
    --no-scan)    NO_SCAN=true ;;
    --dataset=*)  SINGLE_DATASET="${arg#*=}" ;;
    --dataset)    shift; SINGLE_DATASET="$1" ;;
  esac
done

echo "=== PR Lab Dataset Registry Sync ==="
echo "Repo:     $REPO_DIR"
echo "Datasets: $DATASETS_ROOT"
echo ""

# 1. Run scanner (unless --no-scan)
if [ "$NO_SCAN" = false ]; then
  if [ -n "$SINGLE_DATASET" ]; then
    echo "[1/3] Rescanning single dataset: $SINGLE_DATASET"
    DATASETS_ROOT="$DATASETS_ROOT" OUTPUT_JSON="$REGISTRY_JSON" \
      "$PYTHON" "$REPO_DIR/scanner.py" --root "$DATASETS_ROOT" \
      --output "$REGISTRY_JSON" --dataset "$SINGLE_DATASET"
    echo "  (Note: single-dataset mode prints JSON to stdout but does not update registry)"
    echo "  Run without --dataset to do a full registry update."
    NO_SCAN=true   # skip commit step for single-dataset preview
  else
    echo "[1/3] Running full scanner…"
    DATASETS_ROOT="$DATASETS_ROOT" OUTPUT_JSON="$REGISTRY_JSON" \
      "$PYTHON" "$REPO_DIR/scanner.py" --root "$DATASETS_ROOT" --output "$REGISTRY_JSON"
  fi
else
  echo "[1/3] Skipping scan (--no-scan flag set)"
fi

if [ "$NO_SCAN" = true ] && [ -n "$SINGLE_DATASET" ]; then
  echo "Done (preview only, no commit)."
  exit 0
fi

# 2. Copy JSON into the repo's data/ folder
echo "[2/3] Copying registry into repo…"
mkdir -p "$REPO_DIR/docs/data"
cp "$REGISTRY_JSON" "$REPO_DIR/docs/data/datasets.json"
echo "  Copied → $REPO_DIR/docs/data/datasets.json"

# 3. Git commit and push
echo "[3/3] Committing and pushing…"
cd "$REPO_DIR"

if ! git diff --quiet docs/data/datasets.json; then
  TIMESTAMP=$(date -u "+%Y-%m-%d %H:%M UTC")
  git add docs/data/datasets.json
  git commit -m "chore: update dataset registry [$TIMESTAMP]"
  git push
  echo ""
  echo "✓ Registry pushed to GitHub."
  echo "  Dashboard will update at: https://sheethalb.github.io/dataset-registry/"
else
  echo "  No changes to datasets.json — nothing to commit."
fi

echo ""
echo "Done."
