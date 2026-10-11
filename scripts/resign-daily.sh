#!/usr/bin/env bash
# Optional scheduler entrypoint; no notifications or service installation.
set -eu
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$REPO/scripts/lib/resign.py" --daily "$@"
