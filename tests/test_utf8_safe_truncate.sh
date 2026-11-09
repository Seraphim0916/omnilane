#!/usr/bin/env bash
# Prompt truncation must keep valid UTF-8, say how much it dropped, and warn on
# stderr. Covers truncate_payload in common.sh and the owned prompt copy path in
# job-worker.sh, which must stay byte-identical.
# Only an explicit child invocation from an isolated parent keeps fixture state.
# A stale environment marker alone must never bypass standalone isolation.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-utf8-trunc.XXXXXX")"
WORK="$(cd "$WORK" && pwd -P)"

# shellcheck source=../scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
eval "$(sed -n '/^owned_prompt_operation() {$/,/^}$/p' "$ROOT/scripts/lib/job-worker.sh")"
declare -F owned_prompt_operation >/dev/null || { echo "FAIL owned_prompt_operation not found" >&2; exit 1; }

fail() { echo "FAIL $*" >&2; exit 1; }

identity() { python3 -c 'import os,sys; i=os.stat(sys.argv[1]); print("%d:%d" % (i.st_dev, i.st_ino))' "$1"; }

# check_output FILE STDERR_FILE CAP ORIGINAL_SIZE
check_output() {
  python3 - "$1" "$2" "$3" "$4" <<'PY' || exit 1
import sys
out, err, cap, original = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
data = open(out, "rb").read()
try:
    data.decode("utf-8", "strict")
except UnicodeDecodeError as e:
    sys.exit("FAIL %s is not valid UTF-8 at offset %d" % (out, e.start))
text = data.decode("utf-8")
marker = "--- TRUNCATED (original: %d bytes, cap %d, dropped " % (original, cap)
if text.count(marker) != 1:
    sys.exit("FAIL %s marker missing: %r" % (out, marker))
dropped = int(text.split(marker, 1)[1].split(" ", 1)[0])
if dropped <= 0 or len(data) > cap + 200 or len(data) + dropped - len(marker) > original + 100:
    sys.exit("FAIL %s dropped count %d or size %d out of range" % (out, dropped, len(data)))
warning = open(err, encoding="utf-8").read()
if "omnilane: warning: prompt " not in warning or "is %d bytes, over the %d-byte cap; dropped %d middle bytes" % (original, cap, dropped) not in warning:
    sys.exit("FAIL %s stderr warning missing: %r" % (out, warning))
PY
}

# run_both NAME CAP: truncate WORK/NAME.src through both implementations.
run_both() {
  local name="$1" cap="$2" size dir
  size=$(($(wc -c < "$WORK/$name.src")))

  cp "$WORK/$name.src" "$WORK/$name.common"
  truncate_payload "$WORK/$name.common" "$cap" 2> "$WORK/$name.common.err" || fail "$name truncate_payload exit $?"
  check_output "$WORK/$name.common" "$WORK/$name.common.err" "$cap" "$size"

  dir="$WORK/$name.owned"
  mkdir "$dir"
  cp "$WORK/$name.src" "$dir/prompt"
  PROMPT_COPY_DIR="$dir" PROMPT_COPY_ID="$(identity "$dir")" PROMPT_IDENTITIES="prompt=$(identity "$dir/prompt")" \
    owned_prompt_operation truncate "$dir/prompt" "$cap:$size" 2> "$WORK/$name.owned.err" || fail "$name owned truncate exit $?"
  check_output "$dir/prompt" "$WORK/$name.owned.err" "$cap" "$size"

  cmp -s "$WORK/$name.common" "$dir/prompt" || fail "$name: common.sh and job-worker.sh outputs differ"
  printf 'PASS %s (%s bytes, cap %s)\n' "$name" "$size" "$cap"
}

# 1. Large synthetic Chinese prompt over the codex cap (same shape as the 11-08 failure).
python3 -c '
import sys
open(sys.argv[1], "wb").write(("[omnilane worker rules]\nheader\n\n" + "育苗穴盤介質底肥灌溉排程測試，" * 6000).encode())
' "$WORK/large.src"
run_both large 140000

# 2. Cut points on the 2nd and 3rd byte of a 3-byte character, at the head and at the tail.
# cap 1000: head cut at byte 700, tail starts at len - 250.
for spec in "head2:699:0" "head3:698:0" "tail2:600:2" "tail3:600:0"; do
  IFS=: read -r name prefix suffix <<< "$spec"
  python3 - "$WORK/$name.src" "$prefix" "$suffix" "$name" <<'PY'
import sys
path, prefix, suffix, name = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
data = b"a" * prefix + ("中" * 200).encode() + b"b" * suffix
head, tail = 700, len(data) - 250
want = {"head2": (head, 1), "head3": (head, 2), "tail2": (tail, 1), "tail3": (tail, 2)}[name]
index, position = want
start = index
while data[start] & 0xC0 == 0x80:
    start -= 1
assert data[index] & 0xC0 == 0x80 and index - start == position, (name, index, start)
open(path, "wb").write(data)
PY
  run_both "$name" 1000
done

# 3. Under the cap: untouched and silent.
printf '中文短提示\n' > "$WORK/small.src"
cp "$WORK/small.src" "$WORK/small.common"
truncate_payload "$WORK/small.common" 1000 2> "$WORK/small.err"
cmp -s "$WORK/small.src" "$WORK/small.common" || fail "small prompt changed"
[[ ! -s "$WORK/small.err" ]] || fail "small prompt warned"
printf 'PASS small prompt untouched\n'

printf 'PASS test_utf8_safe_truncate.sh\n'
