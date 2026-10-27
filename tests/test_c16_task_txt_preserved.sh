#!/usr/bin/env bash
# Use the same isolated tools and fake CLI contracts as tests/run.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -B -c 'import runpy,sys; sys.argv=[sys.argv[1]]+sys.argv[2:]; runpy.run_path(sys.argv[0],run_name="__main__")' \
    "$ROOT/tests/offline_env.py" /bin/bash "$0" --omnilane-offline-child
fi
mkdir -p "$ROOT/.test-scratch"
TEST_ROOT="$(mktemp -d "$ROOT/.test-scratch/c16-trunc.XXXXXX")"
test_completed=0
trap 'test_rc=$?; trap - EXIT
if [[ "$test_rc" -eq 0 && "$test_completed" -ne 1 ]]; then test_rc=1; fi
rm -rf -- "$TEST_ROOT"
exit "$test_rc"' EXIT
mkdir -p "$TEST_ROOT/runtime/scripts" "$TEST_ROOT/home"
cp -R "$ROOT/scripts/lib" "$ROOT/scripts/runners" "$TEST_ROOT/runtime/scripts/"
cp -R "$ROOT/config" "$TEST_ROOT/runtime/"
# Scope this fixture to prompt delivery. Locks, policy and the watchdog have
# separate integration tests; bypass their process inspection (ps is denied
# in the worker sandbox). The worker and vendor runners remain unmodified.
printf '\nacquire_cwd_lock() { :; }\naa_policy_gate() { :; }\nrun_with_timeout() { shift; "$@"; }\n' \
  >> "$TEST_ROOT/runtime/scripts/lib/common.sh"
# Reuse the aggregate suite's fake CLIs, adding byte-exact input capture.
python3 -B -c 'import pathlib,sys; root,out=map(pathlib.Path,sys.argv[1:]); src=(root/"tests/run.sh").read_text(); capture="python3 -B -c '\''import os,pathlib,sys; a=sys.argv[1:]; data=sys.stdin.buffer.read() if \"-o\" in a else a[-1].encode(); pathlib.Path(os.environ[\"CAPTURE\"]).write_bytes(data)'\'' \"$@\"\n"; [(out/ ("fake-"+v)).write_text("#!/usr/bin/env bash\n"+capture+"if [[ ${FAKE_CLI_EXIT:-0} -ne 0 ]]; then exit \"$FAKE_CLI_EXIT\"; fi\n"+src.split("cat > \"$bin/fake-"+v+"\" <<'\''EOF'\''\n",1)[1].split("\nEOF",1)[0].split("\n",1)[1]) for v in ("codex","claude")]' "$ROOT" "$TEST_ROOT"
chmod +x "$TEST_ROOT/fake-codex" "$TEST_ROOT/fake-claude"
for vendor in codex claude; do
  case "$vendor" in codex) cap=140000 ;; claude) cap=102400 ;; esac
  for expected_rc in 0 23; do
    for size in 4096 "$cap" "$((cap + 1))" 200000; do
      job="$TEST_ROOT/$vendor-$size-$expected_rc"
      mkdir -p "$job"
      python3 -B -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes((b"0123456789abcdef"*20000)[:int(sys.argv[2])])' "$job/task.txt" "$size"
      cp "$job/task.txt" "$job/original"
      rc=0
      OMNILANE_HOME="$TEST_ROOT/home" OMNILANE_SESSION_MODE=single-shot \
        CODEX_BIN="$TEST_ROOT/fake-codex" CLAUDE_BIN="$TEST_ROOT/fake-claude" \
        FAKE_CLI_EXIT="$expected_rc" CAPTURE="$job/received" bash "$TEST_ROOT/runtime/scripts/lib/job-worker.sh" \
        "$vendor" advise "$job" fixture low "$job/task.txt" "$job/out.txt" \
        > "$job/stdout" 2> "$job/stderr" || rc=$?
      [[ "$rc" -eq "$expected_rc" ]] || { echo "FAIL unexpected exit: $rc (expected $expected_rc)" >&2; exit 1; }
      cmp "$job/original" "$job/task.txt" || exit 1
      for copy in task.txt.worker task.txt.runner task.txt.runner.worker; do
        [[ ! -e "$job/$copy" && ! -L "$job/$copy" ]] || { echo "FAIL leftover $copy" >&2; exit 1; }
      done
      for copy in "$job"/task.txt.copies.*; do
        [[ ! -e "$copy" && ! -L "$copy" ]] || { echo "FAIL leftover private copies: $copy" >&2; exit 1; }
      done
      # The received payload still contains exactly one header.
      source "$TEST_ROOT/runtime/scripts/lib/common.sh"
      printf '%s\n%s\n\n' "$OMNILANE_WORKER_RULES_MARKER" "$OMNILANE_WORKER_RULES" > "$job/expected"
      cat "$job/original" >> "$job/expected"
      python3 -B -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); cap=int(sys.argv[2]); data=p.read_bytes(); expected=data if len(data)<=cap else data[:cap*7//10]+("\n\n--- TRUNCATED (original: %s bytes) ---\n\n"%sys.argv[4]).encode()+data[-(cap//4):]; actual=pathlib.Path(sys.argv[3]).read_bytes(); assert actual==expected,"CLI truncation mismatch"' "$job/expected" "$cap" "$job/received" "$(wc -c < "$job/expected")" || exit 1
      printf 'PASS %s %s bytes exit=%s\n' "$vendor" "$size" "$expected_rc"
    done
  done
done
printf 'PASS task.txt preserved; existing CLI truncation verified and copies removed (16 success/failure cases)\n'

# Exercise the real worker/Claude runner lifecycle without dispatch policy or
# process inspection. Readiness and bounded process waits are the oracle;
# polling delays are never treated as proof of success.
python3 -B -c '
import os, pathlib, signal, subprocess, sys, time
base = pathlib.Path(sys.argv[1])
runtime = base / "runtime"
fake = base / "live-claude"
fake.write_text("#!/usr/bin/env python3\nimport json,os,sys\nfor line in sys.stdin:\n print(json.dumps(dict(type=\"result\",is_error=os.environ.get(\"LIVE_FAIL\")==\"1\",result=\"fixture result\")),flush=True)\n if os.environ.get(\"LIVE_FAIL\")==\"1\": sys.exit(23)\n")
fake.chmod(0o755)
def clean(job):
    assert (job / "task.txt").read_bytes() == b"original task\n"
    assert not list(job.glob("task.txt.copies.*")), list(job.iterdir())
    for name in ("task.txt.worker", "task.txt.runner", "task.txt.runner.worker"):
        assert not os.path.lexists(job / name), name
def wait_until(predicate, process):
    deadline = time.monotonic() + 15
    while not predicate():
        assert process.poll() is None, "worker exited before readiness: %s" % process.returncode
        assert time.monotonic() < deadline, "readiness deadline"
        time.sleep(.02)
def command(job):
    return ["/bin/bash", str(runtime / "scripts/lib/job-worker.sh"), "claude", "advise", str(job), "fixture", "low", str(job / "task.txt"), str(job / "out.txt")]
def environment():
    return dict(os.environ, OMNILANE_HOME=str(base / "home"), OMNILANE_SESSION_MODE="live", OMNILANE_IDLE_TIMEOUT="0", OMNILANE_JOB_SUPERVISED="1", CLAUDE_BIN=str(fake))
for scenario in ("close", "failure", "TERM", "HUP", "INT", "timeout", "replaced-directory"):
    job = base / ("lifecycle-" + scenario)
    job.mkdir()
    (job / "task.txt").write_bytes(b"original task\n")
    env = environment()
    if scenario == "failure": env["LIVE_FAIL"] = "1"
    cmd = command(job)
    if scenario == "timeout":
        cmd = ["perl", str(runtime / "scripts/lib/job-timeout.pl"), "3"] + cmd
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        if scenario != "failure":
            events = job / "out.txt.events.jsonl"
            wait_until(lambda: (job / "inbox.ready").exists() and events.exists() and "fixture result" in events.read_text(), process)
        if scenario == "replaced-directory":
            owned = list(job.glob("task.txt.copies.*"))
            assert len(owned) == 1
            replacement = owned[0]
            replacement.rename(job / "moved-owned-copies")
            replacement.mkdir()
            (replacement / "task.txt.runner").write_bytes(b"foreign replacement\n")
            identity = replacement.stat().st_ino
        if scenario in ("close", "replaced-directory"): process.send_signal(signal.SIGUSR1)
        elif scenario in ("TERM", "HUP", "INT"):
            os.killpg(process.pid, getattr(signal, "SIG" + scenario))
        rc = process.wait(timeout=20)
        expected = {"close": 0, "failure": 23, "TERM": 143, "HUP": 129, "INT": 130, "timeout": 124, "replaced-directory": 0}[scenario]
        assert rc == expected, (scenario, rc, expected)
        if scenario == "replaced-directory":
            assert replacement.stat().st_ino == identity
            assert (replacement / "task.txt.runner").read_bytes() == b"foreign replacement\n"
            print("PASS replacement directory identity preserved", flush=True)
        else:
            clean(job)
            print("PASS live lifecycle %s exit=%s; copies removed" % (scenario, rc), flush=True)
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
for name in ("task.txt.worker", "task.txt.runner", "task.txt.runner.worker"):
    for kind in ("file", "symlink", "directory"):
        job = base / ("existing-" + name + "-" + kind)
        job.mkdir()
        (job / "task.txt").write_bytes(b"original task\n")
        target = job / name
        if kind == "file": target.write_bytes(b"pre-existing\n")
        elif kind == "symlink": target.symlink_to("missing-target")
        else: target.mkdir()
        before = target.lstat()
        rc = subprocess.run(command(job), env=environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode
        assert rc == 125, (name, kind, rc)
        after = target.lstat()
        assert (before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode)
        if kind == "file": assert target.read_bytes() == b"pre-existing\n"
        if kind == "symlink": assert os.readlink(target) == "missing-target"
        assert (job / "task.txt").read_bytes() == b"original task\n"
        assert not list(job.glob("task.txt.copies.*"))
        print("PASS pre-existing %s %s preserved" % (name, kind), flush=True)
' "$TEST_ROOT"
# Exercise the actual worker and both test scripts with Bash 3.2 nounset
# failures. Prefix fixtures retain each scripts own EXIT handler, but skip
# expensive test bodies; no production fault-injection switch is needed.
python3 -B -c '
import os, pathlib, subprocess, sys
base, root = map(pathlib.Path, sys.argv[1:])
runtime = base / "runtime"
with (runtime / "scripts/lib/common.sh").open("a") as out:
    out.write("\naa_policy_gate() { unset R154_UNBOUND; : \"$R154_UNBOUND\"; }\n")
job = base / "nounset-worker"
job.mkdir()
(job / "task.txt").write_text("nounset regression\n")
env = dict(os.environ, OMNILANE_HOME=str(base / "home"), OMNILANE_SESSION_MODE="live", CLAUDE_BIN=str(base / "live-claude"))
cmd = ["/bin/bash", str(runtime / "scripts/lib/job-worker.sh"), "claude", "advise", str(job), "fixture", "low", str(job / "task.txt"), str(job / "out.txt")]
result = subprocess.run(cmd, env=env, capture_output=True, timeout=10)
assert result.returncode != 0 and b"R154_UNBOUND: unbound variable" in result.stderr, (result.returncode, result.stderr)
assert not list(job.glob("task.txt.copies.*"))
print("PASS worker nounset exits nonzero (%s) and cleans copies" % result.returncode, flush=True)
for name, boundary in (("test_c16_task_txt_preserved.sh", "mkdir -p \"$TEST_ROOT/runtime/scripts\""), ("test_live_mailbox.sh", "fail() {")):
    prefix = (root / "tests" / name).read_text().split(boundary, 1)[0]
    probe = base / name
    probe.write_text(prefix + "\nunset R154_UNBOUND\n: \"$R154_UNBOUND\"\n")
    result = subprocess.run(["/bin/bash", str(probe), "--omnilane-offline-child"], env=env, capture_output=True, timeout=10)
    assert result.returncode != 0 and b"R154_UNBOUND: unbound variable" in result.stderr, (name, result.returncode, result.stderr)
    print("PASS %s nounset exits nonzero (%s)" % (name, result.returncode), flush=True)
' "$TEST_ROOT" "$ROOT"
test_completed=1
