#!/usr/bin/env bash

# Only an explicit child invocation from an isolated parent keeps fixture state.
# A stale environment marker alone must never bypass standalone isolation.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
# in the worker sandbox). Vendor runners remain unmodified.
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
      [[ "$rc" -eq "$expected_rc" ]] || { echo "FAIL unexpected exit: $rc (expected $expected_rc)" >&2; tail -n 10 "$job/stderr" >&2; exit 1; }
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
base, root = (pathlib.Path(arg).resolve() for arg in sys.argv[1:])
runtime = base / "runtime"
# Fault injection belongs only to this disposable copy, never production.
worker = runtime / "scripts/lib/job-worker.sh"
source = worker.read_text()
cleanup_hook = "            hook = os.environ.get(\"OMNILANE_TEST_CLEANUP_SWAP_HOOK\")\n            if hook:\n                subprocess.run([\"/bin/bash\", hook, os.path.join(path, name)], check=True)\n"
create_hook = "        hook = os.environ.get(\"OMNILANE_TEST_PROMPT_CREATE_HOOK\")\n        if hook:\n            subprocess.run([\"/bin/bash\", hook, os.path.join(directory, name)], check=True)\n"
# Strip the old hooks too, so this exact regression can run against 465fb54.
source = source.replace(cleanup_hook, "").replace(create_hook, "")
old_anchor = "            os.rename(name, temporary, src_dir_fd=fd, dst_dir_fd=fd)"
new_anchor = "                os.rename(name, name, src_dir_fd=fd, dst_dir_fd=quarantine_fd)"
if new_anchor in source:
    anchor, indent, target = new_anchor, "                ", "os.path.join(quarantine, name)"
else:
    anchor, indent, target = old_anchor, "            ", "os.path.join(path, temporary)"
assert source.count(anchor) == 1
source = source.replace(anchor, anchor + "\n" + indent + "import subprocess\n" + indent + "hook = os.environ.get(\"OMNILANE_TEST_CLEANUP_SWAP_HOOK\")\n" + indent + "if hook:\n" + indent + "    subprocess.run([\"/bin/bash\", hook, " + target + "], check=True)")
anchor = "        info = os.fstat(fd)"
assert source.count(anchor) == 1
source = source.replace(anchor, "        import subprocess\n" + create_hook + anchor)
source = source.replace("PROMPT_CLEANUP_LIMIT=1\n", "PROMPT_CLEANUP_LIMIT=30\n")
worker.write_text(source)
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
# EPERM from killpg does not prove the worker is gone, so only a bounded wait may
# excuse it. A worker that outlives the wait is reported instead of hanging the suite.
def stop_worker(process, killpg=os.killpg, timeout=10):
    try: killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError): pass
    try: process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AssertionError("cleanup failed: worker %s still running after killpg" % process.pid)
def denied_killpg(pid, sig):
    raise PermissionError(1, "injected EPERM")
# The alarm stops a teardown that regresses to an unbounded wait.
signal.alarm(30)
exited = subprocess.Popen(["/bin/sh", "-c", "exit 0"], stdin=subprocess.DEVNULL, start_new_session=True)
while exited.poll() is None: time.sleep(.01)
stop_worker(exited, denied_killpg, 1)
print("PASS teardown tolerates EPERM once the worker has exited", flush=True)
alive = subprocess.Popen(["/bin/sleep", "60"], stdin=subprocess.DEVNULL, start_new_session=True)
try:
    reported = False
    try: stop_worker(alive, denied_killpg, .2)
    except AssertionError as error: reported = "cleanup failed" in str(error)
    assert reported, "EPERM with a live worker was not reported"
    assert alive.poll() is None
finally:
    os.killpg(alive.pid, signal.SIGKILL)
    alive.wait(timeout=10)
signal.alarm(0)
print("PASS teardown reports EPERM while the worker is alive, without hanging", flush=True)
# These cases run the worker boundary, including its actual EXIT handler.
regressions = []
for scenario in ("production-hooks", "ledger-tamper", "cleanup-window-swap", "create-window-signal"):
    job = base / scenario
    job.mkdir()
    (job / "task.txt").write_bytes(b"original task\n")
    env = environment()
    hook = job / "hook.sh"
    marker = job / "hook-ready"
    env["HOOK_MARKER"] = str(marker)
    cmd = command(job)
    if scenario == "production-hooks":
        hook.write_text("printf executed >> \"$HOOK_MARKER\"\n")
        env["OMNILANE_TEST_CLEANUP_SWAP_HOOK"] = str(hook)
        env["OMNILANE_TEST_PROMPT_CREATE_HOOK"] = str(hook)
        env["OMNILANE_JOB_WORKER_REPO"] = str(runtime)
        cmd[1] = str(root / "scripts/lib/job-worker.sh")
    if scenario == "cleanup-window-swap":
        hook.write_text("mv \"$1\" \"$1.saved\"\nprintf \"foreign-swap:%s\" \"${1##*/}\" > \"$1\"\nprintf \"%s\\n\" \"$1\" >> \"$HOOK_MARKER\"\n")
        env["OMNILANE_TEST_CLEANUP_SWAP_HOOK"] = str(hook)
    if scenario == "create-window-signal":
        hook.write_text("printf ready > \"$HOOK_MARKER\"\nwhile :; do sleep 1; done\n")
        env["OMNILANE_TEST_PROMPT_CREATE_HOOK"] = str(hook)
    stderr = (job / "stderr").open("wb")
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
    try:
        if scenario == "create-window-signal":
            deadline = time.monotonic() + 3
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert marker.exists(), "creation hook never reached"
            private = next(job.glob("task.txt.copies.*"))
            created = list(private.iterdir())
            assert len(created) == 1, created
            foreign = private / "foreign.txt"
            foreign.write_bytes(b"foreign-create")
            os.killpg(process.pid, signal.SIGTERM)
            assert process.wait(timeout=10) == 143
            assert foreign.read_bytes() == b"foreign-create"
            assert created[0].exists(), "unreceipted creation must be retained"
            diagnostics = (job / "stderr").read_text()
            for item in (foreign, created[0]):
                assert "omnilane: left foreign file " + item.name in diagnostics
        else:
            events = job / "out.txt.events.jsonl"
            wait_until(lambda: (job / "inbox.ready").exists() and events.exists() and "fixture result" in events.read_text(), process)
            private = next(job.glob("task.txt.copies.*"))
            if scenario == "ledger-tamper":
                import json
                foreign = private / "foreign.txt"
                foreign.write_bytes(b"foreign-ledger")
                info = foreign.stat()
                entry = json.dumps([foreign.name, info.st_dev, info.st_ino]) + "\n"
                # Mutate every existing file in place, including a ledger if any.
                for item in private.iterdir():
                    if item != foreign and item.is_file():
                        with item.open("a") as out: out.write(entry)
            process.send_signal(signal.SIGUSR1)
            assert process.wait(timeout=10) == 0
            diagnostics = (job / "stderr").read_text()
            if scenario == "production-hooks":
                assert not marker.exists(), "production executed test hook environment variables"
                clean(job)
            elif scenario == "ledger-tamper":
                assert foreign.exists() and foreign.read_bytes() == b"foreign-ledger", "forged receipt deleted foreign file"
                assert "omnilane: left foreign file foreign.txt" in diagnostics
            else:
                assert marker.exists(), "cleanup swap hook never reached"
                for original in marker.read_text().splitlines():
                    content = ("foreign-swap:" + pathlib.Path(original).name).encode()
                    swapped = [p for p in private.iterdir() if p.is_file() and p.read_bytes() == content]
                    assert swapped, "swapped-in foreign content was deleted: " + original
                    assert any("omnilane: left foreign file " + p.name in diagnostics for p in swapped)
                    quarantine = pathlib.Path(original).parent
                    assert quarantine != private and quarantine.parent == private, "post-rename candidate exposed in parent listing"
                    assert quarantine.stat().st_mode & 0o777 == 0o700
        print("PASS R165 " + scenario, flush=True)
    except (AssertionError, OSError, subprocess.TimeoutExpired) as error:
        regressions.append(scenario)
        print("FAIL R165 %s: %s" % (scenario, error), flush=True)
    finally:
        try: stop_worker(process)
        finally: stderr.close()
assert not regressions, "R165 regressions: " + ", ".join(regressions)
swap_failures = []
for scenario in ("close", "failure", "TERM", "HUP", "INT", "timeout", "replaced-directory", "swap-runner", "swap-worker"):
    job = base / ("lifecycle-" + scenario)
    job.mkdir()
    (job / "task.txt").write_bytes(b"original task\n")
    env = environment()
    if scenario == "failure": env["LIVE_FAIL"] = "1"
    cmd = command(job)
    if scenario == "timeout":
        cmd = ["perl", str(runtime / "scripts/lib/job-timeout.pl"), "3"] + cmd
    stderr = (job / "stderr").open("wb")
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
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
        if scenario.startswith("swap-"):
            owned = list(job.glob("task.txt.copies.*"))
            assert len(owned) == 1
            replacement = owned[0] / ("task.txt.runner" if scenario == "swap-runner" else "task.txt.worker")
            before = replacement.lstat()
            # Hold the old inode open so rm + create cannot recycle it.
            old_file = replacement.open("rb")
            replacement.unlink()
            replacement.write_bytes(b"foreign same-directory replacement\n")
            identity = replacement.lstat()
            assert (before.st_dev, before.st_ino) != (identity.st_dev, identity.st_ino)
            old_file.close()
        if scenario in ("close", "replaced-directory", "swap-runner", "swap-worker"): process.send_signal(signal.SIGUSR1)
        elif scenario in ("TERM", "HUP", "INT"):
            os.killpg(process.pid, getattr(signal, "SIG" + scenario))
        rc = process.wait(timeout=20)
        expected = {"close": 0, "failure": 23, "TERM": 143, "HUP": 129, "INT": 130, "timeout": 124, "replaced-directory": 0, "swap-runner": 0, "swap-worker": 0}[scenario]
        assert rc == expected, (scenario, rc, expected)
        if scenario.startswith("swap-"):
            stderr.flush()
            preserved = replacement.exists() and replacement.read_bytes() == b"foreign same-directory replacement\n"
            diagnostic = "omnilane: left foreign file " + replacement.name
            reported = diagnostic in (job / "stderr").read_text().splitlines()
            if not preserved or not reported:
                swap_failures.append(scenario)
                print("FAIL %s preserved=%s diagnostic=%s" % (scenario, preserved, reported), flush=True)
            else:
                assert replacement.lstat().st_ino == identity.st_ino
                assert list(replacement.parent.iterdir()) == [replacement]
                print("PASS %s foreign inode/content preserved; %s" % (scenario, diagnostic), flush=True)
        elif scenario == "replaced-directory":
            assert replacement.stat().st_ino == identity
            assert (replacement / "task.txt.runner").read_bytes() == b"foreign replacement\n"
            print("PASS replacement directory identity preserved", flush=True)
        else:
            clean(job)
            print("PASS live lifecycle %s exit=%s; copies removed" % (scenario, rc), flush=True)
    finally:
        try: stop_worker(process)
        finally: stderr.close()
assert not swap_failures, "foreign-file cleanup regressions: " + ", ".join(swap_failures)
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
' "$TEST_ROOT" "$ROOT"
# Direct-API vendors reach the shared runner through a wrapper that execs it, and
# EXIT cleanup must not be able to hold the job's exit receipt. Run the production
# worker on the fixture's libraries; the fakes are a curl and a python3 on PATH.
python3 -B -c '
import json, os, pathlib, shutil, signal, subprocess, sys, time
base, root = map(pathlib.Path, sys.argv[1:])
runtime = base / "runtime"
worker = root / "scripts/lib/job-worker.sh"
payload = b"0123456789abcdef" * 20000
failures = []
def new_job(name, size):
    job = base / name
    job.mkdir()
    (job / "task.txt").write_bytes(payload[:size])
    return job
def environment(job, **extra):
    return dict(os.environ, OMNILANE_HOME=str(base / "home"), OMNILANE_SESSION_MODE="single-shot", OMNILANE_JOB_WORKER_REPO=str(runtime), CAPTURE=str(job / "received"), **extra)
def command(vendor, job, path=worker):
    return ["/bin/bash", str(path), vendor, "advise", str(job), "fixture", "low", str(job / "task.txt"), str(job / "out.txt")]
def private_dirs(job):
    return sorted(job.glob("task.txt.copies.*"))
def check(name, case, *args):
    try:
        case(*args)
        print("PASS " + name, flush=True)
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        failures.append(name)
        print("FAIL %s: %s" % (name, error), flush=True)

fake_bin = base / "direct-api-bin"
fake_bin.mkdir()
(base / "direct-api-response.json").write_text(json.dumps({"choices": [{"message": {"content": "fixture result"}}]}))
(fake_bin / "curl").write_text("#!/bin/bash\nfor arg in \"$@\"; do case \"$arg\" in @*.request.json) /bin/cp \"${arg#@}\" \"$CAPTURE\" ;; esac; done\n/bin/cat \"" + str(base / "direct-api-response.json") + "\"\n")
(fake_bin / "curl").chmod(0o755)
def direct_api_case(vendor, size):
    cap = 140000
    job = new_job("direct-api-%s-%s" % (vendor, size), size)
    env = environment(job, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"], **{vendor.upper() + "_API_KEY": "fixture-key"})
    result = subprocess.run(command(vendor, job), env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    assert result.returncode == 0, (result.returncode, result.stderr)
    data = payload[:size]
    assert (job / "task.txt").read_bytes() == data
    assert (job / "out.txt").read_text() == "fixture result"
    assert not private_dirs(job), [sorted(p.name for p in d.iterdir()) for d in private_dirs(job)]
    sent = json.loads((job / "received").read_text())["messages"][0]["content"].encode()
    original = subprocess.check_output(["wc", "-c"], input=data).decode().rstrip("\n")
    expected = data if size <= cap else data[:cap * 7 // 10] + ("\n\n--- TRUNCATED (original: %s bytes) ---\n\n" % original).encode() + data[-(cap // 4):]
    assert sent == expected, "direct API truncation mismatch"
for vendor in ("openrouter", "deepseek", "zai", "mistral", "groq", "cerebras"):
    check("direct API %s over the cap removes its copies" % vendor, direct_api_case, vendor, 200000)
for size in (4096, 140000, 140001):
    check("direct API groq %s bytes removes its copies" % size, direct_api_case, "groq", size)
def wrapper_with_extra_line_case():
    # Identity checks must keep what an untracked runner replaced; only a pure wrapper is rerouted.
    source = (runtime / "scripts/runners/run-groq.sh").read_text()
    custom = runtime / "scripts/runners/run-groqplus.sh"
    custom.write_text(source.replace("export OMNILANE_OAI_VENDOR=groq\n", "export OMNILANE_OAI_VENDOR=groq\necho wrapper-extra-line >&2\n"))
    custom.chmod(0o755)
    job = new_job("direct-api-extra-line", 200000)
    env = environment(job, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"], GROQ_API_KEY="fixture-key")
    result = subprocess.run(command("groqplus", job), env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert b"wrapper-extra-line" in result.stderr, result.stderr
    assert b"omnilane: left foreign file task.txt.runner" in result.stderr, result.stderr
    private = private_dirs(job)
    assert len(private) == 1 and (private[0] / "task.txt.runner").exists(), private
check("wrapper with an extra line keeps the plain exec and reports its copy", wrapper_with_extra_line_case)

python_bin = base / "cleanup-python-bin"
python_bin.mkdir()
(base / "hang.py").write_text("import os, pathlib, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\npathlib.Path(os.environ[\"HANG_PID\"]).write_text(str(os.getpid()))\ntime.sleep(3600)\n")
(python_bin / "python3").write_text("#!/bin/bash\ncase \"$*\" in\n  *.cleanup-*)\n    if [[ -n \"${HANG_PID:-}\" ]]; then exec \"$REAL_PYTHON\" -I \"" + str(base / "hang.py") + "\"; fi\n    : > \"$SLOW_MARKER\"\n    /bin/sleep .3 ;;\nesac\nexec \"$REAL_PYTHON\" \"$@\"\n")
(python_bin / "python3").chmod(0o755)
def cleanup_env(job, **extra):
    return environment(job, PATH=str(python_bin) + os.pathsep + os.environ["PATH"], REAL_PYTHON=shutil.which("python3"), CLAUDE_BIN=str(base / "fake-claude"), **extra)
def variant(name, *replacements):
    # Disposable copy: a slow fake must not race the limit it is not testing.
    source = worker.read_text()
    for old, new in replacements:
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    path = runtime / "scripts/lib" / name
    path.write_text(source)
    return path
def hung_cleanup_case(expected_rc):
    job = new_job("cleanup-hang-%s" % expected_rc, 4096)
    pid_file = job / "hang.pid"
    env = cleanup_env(job, HANG_PID=str(pid_file), FAKE_CLI_EXIT=str(expected_rc))
    process = subprocess.Popen(command("claude", job), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        # Pipes also catch any process left holding the worker stdout or stderr.
        _, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=10)
        raise AssertionError("hung prompt cleanup held the worker past 20s")
    stage = time.time() - pid_file.stat().st_mtime
    assert process.returncode == expected_rc, (process.returncode, stderr)
    assert stage < 5, "cleanup stage took %.2fs" % stage
    hung_pid = int(pid_file.read_text())
    try:
        os.kill(hung_pid, 0)
    except ProcessLookupError:
        pass
    else:
        os.kill(hung_pid, signal.SIGKILL)
        raise AssertionError("hung cleanup process survived the worker")
    private = private_dirs(job)
    assert len(private) == 1, private
    assert (private[0] / "task.txt.runner").exists()
    assert ("omnilane: prompt copy cleanup exceeded 1s; left " + str(private[0])).encode() in stderr, stderr
    assert (job / "task.txt").read_bytes() == payload[:4096]
for expected_rc in (0, 23):
    check("hung prompt cleanup is bounded, keeps exit %s and reports its copies" % expected_rc, hung_cleanup_case, expected_rc)
def signal_during_cleanup_case():
    job = new_job("cleanup-signal", 4096)
    marker = job / "slow-marker"
    path = variant("job-worker-long-limit.sh", ("PROMPT_CLEANUP_LIMIT=1\n", "PROMPT_CLEANUP_LIMIT=30\n"))
    env = cleanup_env(job, SLOW_MARKER=str(marker))
    process = subprocess.Popen(command("claude", job, path), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), "cleanup never reached"
        os.killpg(process.pid, signal.SIGTERM)
        _, stderr = process.communicate(timeout=20)
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
        if process.poll() is None: process.communicate(timeout=10)
    assert not private_dirs(job), stderr
check("TERM during prompt cleanup does not abort it", signal_during_cleanup_case)
def no_perl_case():
    job = new_job("cleanup-no-perl", 4096)
    path = variant("job-worker-no-perl.sh", ("if command -v perl >/dev/null 2>&1; then\n    prompt_cleanup_bounded", "if false; then\n    prompt_cleanup_bounded"))
    result = subprocess.run(command("claude", job, path), env=environment(job, CLAUDE_BIN=str(base / "fake-claude")), stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert not private_dirs(job), result.stderr
    assert b"omnilane: no perl; prompt copy cleanup runs without a time limit" in result.stderr, result.stderr
check("without perl prompt cleanup still runs and says it is unbounded", no_perl_case)
idle_fake = base / "idle-live-cli"
idle_fake.write_text("#!/usr/bin/env python3\nimport os, pathlib, sys, time\npathlib.Path(os.environ[\"RUNNER_PID\"]).write_text(str(os.getpid()))\nsys.stdin.readline()\ntime.sleep(60)\n")
idle_fake.chmod(0o755)
def signal_in_mailbox_wait_case(name, signum, to_group, expected_rc):
    # Without a supervisor nothing else ends a worker that fails to exit. The cleanup
    # outlasts the mailbox read timeout, and the wait below keeps a spin from holding the suite.
    job = new_job("mailbox-signal-" + name, 4096)
    env = cleanup_env(job, HANG_PID=str(job / "hang.pid"), RUNNER_PID=str(job / "runner.pid"))
    env.update(OMNILANE_SESSION_MODE="live", OMNILANE_IDLE_TIMEOUT="0", OMNILANE_JOB_SUPERVISED="1", CLAUDE_BIN=str(idle_fake))
    with open(job / "worker.log", "wb") as log:
        process = subprocess.Popen(command("claude", job), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    runner = None
    try:
        deadline = time.monotonic() + 20
        while not (job / "runner.pid").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert (job / "runner.pid").exists(), "runner never started"
        runner = int((job / "runner.pid").read_text())
        time.sleep(.3)
        (os.killpg if to_group else os.kill)(process.pid, signum)
        try:
            rc = process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            raise AssertionError("worker still running 8s after the signal")
        log = (job / "worker.log").read_bytes()
        assert rc == expected_rc, (rc, log[-400:])
        assert b"omnilane: prompt copy cleanup exceeded 1s; left " in log, log[-400:]
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
        if runner is not None:
            try: os.kill(runner, signal.SIGKILL)
            except (ProcessLookupError, PermissionError): pass
        if process.poll() is None: process.wait(timeout=10)
for name, signum, to_group, expected_rc in (("term-group", signal.SIGTERM, True, 143), ("term-worker", signal.SIGTERM, False, 143), ("hup-group", signal.SIGHUP, True, 129), ("int-group", signal.SIGINT, True, 130)):
    check("%s during the mailbox wait exits with %s while cleanup is slow" % (name, expected_rc), signal_in_mailbox_wait_case, name, signum, to_group, expected_rc)
# A repeated signal normally lands by chance under load. The worker sends it to itself at
# the two points that matter, so a missing ignore fails every time.
def repeated_signal_after_signal_exit_case(name, signum, expected_rc):
    job = new_job("repeat-after-signal-" + name, 4096)
    path = variant("job-worker-repeat-in-handler-%s.sh" % name, ("  local rc=$?\n  # Observed on Bash 3.2", "  local rc=$?\n  kill -%s $$\n  # Observed on Bash 3.2" % name))
    env = environment(job, RUNNER_PID=str(job / "runner.pid"))
    env.update(OMNILANE_SESSION_MODE="live", OMNILANE_IDLE_TIMEOUT="0", OMNILANE_JOB_SUPERVISED="1", CLAUDE_BIN=str(idle_fake))
    with open(job / "worker.log", "wb") as log:
        process = subprocess.Popen(command("claude", job, path), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    try:
        deadline = time.monotonic() + 20
        while not (job / "runner.pid").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert (job / "runner.pid").exists(), "runner never started"
        time.sleep(.3)
        os.killpg(process.pid, signum)
        rc = process.wait(timeout=8)
        assert rc == expected_rc, rc
        assert not private_dirs(job), [p.name for p in job.iterdir()]
        assert not list(job.glob("inbox.*")), [p.name for p in job.iterdir()]
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
        if process.poll() is None: process.wait(timeout=10)
def signal_after_normal_exit_case(name):
    job = new_job("signal-after-exit-" + name, 4096)
    path = variant("job-worker-signal-in-handler-%s.sh" % name, ("  trap - EXIT\n  if [[ \"$rc\" -eq 0 &&", "  trap - EXIT\n  kill -%s $$\n  if [[ \"$rc\" -eq 0 &&" % name))
    result = subprocess.run(command("claude", job, path), env=environment(job, CLAUDE_BIN=str(base / "fake-claude"), FAKE_CLI_EXIT="23"), stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    assert result.returncode == 23, (result.returncode, result.stderr[-300:])
    assert not private_dirs(job), [p.name for p in job.iterdir()]
for name, signum, expected_rc in (("TERM", signal.SIGTERM, 143), ("HUP", signal.SIGHUP, 129), ("INT", signal.SIGINT, 130)):
    check("%s repeated while a signal exit starts its cleanup keeps exit %s and cleans up" % (name, expected_rc), repeated_signal_after_signal_exit_case, name, signum, expected_rc)
    check("%s during the cleanup of a failed job keeps its exit status and cleans up" % name, signal_after_normal_exit_case, name)
assert not failures, "direct-API and cleanup-limit regressions: " + "; ".join(failures)
' "$TEST_ROOT" "$ROOT"
# Exercise the actual worker and both test scripts with Bash 3.2 nounset
# failures. Prefix fixtures retain each scripts own EXIT handler, but skip
# expensive test bodies; no production fault-injection switch is needed.
python3 -B -c '
import json, os, pathlib, subprocess, sys
base, root = map(pathlib.Path, sys.argv[1:])
runtime = base / "runtime"
worker = runtime / "scripts/lib/job-worker.sh"
original_worker = worker.read_text()
# Stop after writing to the parent-registered inode for both copy producers.
# The actual worker EXIT path must remove all parent-registered targets.
for operation in ("decorate", "truncate"):
    seam = "        out.flush()"
    assert original_worker.count(seam) == 1
    worker.write_text(original_worker.replace(seam, seam + "\n        if action == " + json.dumps(operation) + ": sys.exit(23)"))
    job = base / ("interrupted-" + operation)
    job.mkdir()
    (job / "task.txt").write_bytes(b"x" * 200000)
    env = dict(os.environ, OMNILANE_HOME=str(base / "home"), OMNILANE_SESSION_MODE="single-shot", CLAUDE_BIN=str(base / "fake-claude"), CAPTURE=str(job / "received"))
    result = subprocess.run(["/bin/bash", str(worker), "claude", "advise", str(job), "fixture", "low", str(job / "task.txt"), str(job / "out.txt")], env=env, capture_output=True, timeout=10)
    expected = 125 if operation == "decorate" else 23
    assert result.returncode == expected, (operation, result.returncode, result.stderr)
    assert not list(job.glob("task.txt.copies.*")), (operation, result.stderr)
    assert (job / "task.txt").read_bytes() == b"x" * 200000
    print("PASS interrupted %s registered copies removed exit=%s" % (operation, expected), flush=True)
worker.write_text(original_worker)
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
