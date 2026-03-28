#!/usr/bin/env bash
# Sync data/, docs/, and skills/ to ../CW-BoardGames/ for Claude Cowork access.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TARGET_DIR="$PROJECT_DIR/../CW-BoardGames"

mkdir -p "$TARGET_DIR"

echo "Syncing to $TARGET_DIR …"

rsync -av --delete "$PROJECT_DIR/data/"   "$TARGET_DIR/data/"
rsync -av --delete "$PROJECT_DIR/docs/"   "$TARGET_DIR/docs/"
rsync -av --delete "$PROJECT_DIR/skills/"     "$TARGET_DIR/skills/"
rsync -av --delete "$PROJECT_DIR/dashboard/" "$TARGET_DIR/dashboard/"

echo "✔ Sync complete."
