#!/usr/bin/env bash
# Use the same isolated tools and fake CLI contracts as tests/run.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -B -c 'import runpy,sys; sys.argv=[sys.argv[1]]+sys.argv[2:]; runpy.run_path(sys.argv[0],run_name="__main__")' \
    "$ROOT/tests/offline_env.py" /bin/bash "$0" --omnilane-offline-child
fi
TEST_ROOT="$(mktemp -d "$TMPDIR/c16-trunc.XXXXXX")"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/runtime/scripts" "$TEST_ROOT/home"
cp -R "$ROOT/scripts/lib" "$ROOT/scripts/runners" "$TEST_ROOT/runtime/scripts/"
cp -R "$ROOT/config" "$TEST_ROOT/runtime/"
# Scope this fixture to prompt delivery. Locks, policy and the watchdog have
# separate integration tests; bypass their process inspection (ps is denied
# in the worker sandbox). The worker and vendor runners remain unmodified.
printf '\nacquire_cwd_lock() { :; }\naa_policy_gate() { :; }\nrun_with_timeout() { shift; "$@"; }\n' \
  >> "$TEST_ROOT/runtime/scripts/lib/common.sh"
# Reuse the aggregate suite's fake CLIs, adding byte-exact input capture.
python3 -B -c 'import pathlib,sys; root,out=map(pathlib.Path,sys.argv[1:]); src=(root/"tests/run.sh").read_text(); capture="python3 -B -c '\''import os,pathlib,sys; a=sys.argv[1:]; data=sys.stdin.buffer.read() if \"-o\" in a else a[a.index(\"-p\")+1].encode(); pathlib.Path(os.environ[\"CAPTURE\"]).write_bytes(data)'\'' \"$@\"\n"; [(out/ ("fake-"+v)).write_text("#!/usr/bin/env bash\n"+capture+src.split("cat > \"$bin/fake-"+v+"\" <<'\''EOF'\''\n",1)[1].split("\nEOF",1)[0].split("\n",1)[1]) for v in ("codex","claude")]' "$ROOT" "$TEST_ROOT"
chmod +x "$TEST_ROOT/fake-codex" "$TEST_ROOT/fake-claude"
for vendor in codex claude; do
  case "$vendor" in codex) cap=140000 ;; claude) cap=102400 ;; esac
  for size in 4096 "$cap" "$((cap + 1))" 200000; do
    job="$TEST_ROOT/$vendor-$size"
    mkdir -p "$job"
    python3 -B -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes((b"0123456789abcdef"*20000)[:int(sys.argv[2])])' "$job/task.txt" "$size"
    cp "$job/task.txt" "$job/original"
    OMNILANE_HOME="$TEST_ROOT/home" OMNILANE_SESSION_MODE=single-shot \
      CODEX_BIN="$TEST_ROOT/fake-codex" CLAUDE_BIN="$TEST_ROOT/fake-claude" \
      CAPTURE="$job/received" bash "$TEST_ROOT/runtime/scripts/lib/job-worker.sh" \
      "$vendor" advise "$job" fixture low "$job/task.txt" "$job/out.txt" \
      > "$job/stdout" 2> "$job/stderr"
    cmp "$job/original" "$job/task.txt"
    # The boundary copy stays raw; the existing runner adds exactly one header.
    cmp "$job/original" "$job/task.txt.runner"
    source "$TEST_ROOT/runtime/scripts/lib/common.sh"
    printf '%s\n%s\n\n' "$OMNILANE_WORKER_RULES_MARKER" "$OMNILANE_WORKER_RULES" > "$job/expected"
    cat "$job/original" >> "$job/expected"
    python3 -B -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); cap=int(sys.argv[2]); data=p.read_bytes(); expected=data if len(data)<=cap else data[:cap*7//10]+("\n\n--- TRUNCATED (original: %s bytes) ---\n\n"%sys.argv[4]).encode()+data[-(cap//4):]; actual=pathlib.Path(sys.argv[3]).read_bytes(); assert actual==expected,"CLI truncation mismatch"' "$job/expected" "$cap" "$job/received" "$(wc -c < "$job/expected")"
    cmp "$job/received" "$job/task.txt.runner.worker"
    printf 'PASS %s %s bytes\n' "$vendor" "$size"
  done
done
printf 'PASS task.txt preserved; existing CLI truncation verified (8 cases)\n'
