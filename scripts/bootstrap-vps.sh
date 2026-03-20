#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sc6866/xiaohongshu-auto-publisher.git}"
BRANCH="${BRANCH:-main}"
TARGET_DIR="${TARGET_DIR:-/opt/xiaohongshu-auto-publisher}"

if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  SUDO=""
fi

step() {
  echo
  echo "==> $1"
}

if ! command -v git >/dev/null 2>&1; then
  step "Installing git"
  $SUDO apt-get update
  $SUDO apt-get install -y git
fi

if [[ ! -d "$TARGET_DIR/.git" ]]; then
  step "Cloning repository"
  $SUDO mkdir -p "$(dirname "$TARGET_DIR")"
  $SUDO git clone --branch "$BRANCH" "$REPO_URL" "$TARGET_DIR"
else
  step "Updating repository"
  $SUDO git -C "$TARGET_DIR" fetch origin "$BRANCH"
  $SUDO git -C "$TARGET_DIR" checkout "$BRANCH"
  $SUDO git -C "$TARGET_DIR" pull origin "$BRANCH"
fi

step "Running VPS deployment"
$SUDO bash "$TARGET_DIR/scripts/deploy-vps.sh" --https
