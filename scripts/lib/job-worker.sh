#!/usr/bin/env bash
set -euo pipefail
# Internal worker boundary for one dispatch. The optional whole-job supervisor
# wraps this process so lock wait, retries, and vote rounds share one budget.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

[[ $# -eq 7 ]] || { echo "omnilane: internal job worker received invalid arguments" >&2; exit 2; }
VENDOR="$1"; MODE="$2"; WORKDIR="$3"; MODEL="$4"; EFFORT="$5"
PROMPT_FILE="$6"; OUTPUT_FILE="$7"

[[ "$VENDOR" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "omnilane: invalid worker vendor" >&2; exit 2; }
RUNNER="$OMNILANE_REPO/scripts/runners/run-$VENDOR.sh"
[[ -x "$RUNNER" ]] || { echo "omnilane: no runner for vendor '$VENDOR'" >&2; exit 2; }

# Two concurrent codex exec in one target dir corrupt its job index — serialize.
[[ "$VENDOR" == "codex" ]] && acquire_cwd_lock codex "$WORKDIR"

set +e
"$RUNNER" "$MODE" "$WORKDIR" "$MODEL" "$EFFORT" "$PROMPT_FILE" "$OUTPUT_FILE"
rc=$?
set -e
exit "$rc"
