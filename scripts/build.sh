#!/bin/bash
set -e

COMMAND=${1:-build}

case "$COMMAND" in
  build)
    cd ui
    npm run build
    cd src-tauri
    cargo check
    ;;
  dev)
    cd ui
    npm run tauri dev
    ;;
  test)
    python -m pytest tests backend/tests
    ;;
  clean)
    cd ui/src-tauri
    cargo clean
    ;;
  *)
    echo "Usage: $0 {build|dev|test|clean}"
    exit 1
    ;;
esac
