#!/bin/bash

# Only an explicit child invocation from an isolated parent keeps fixture state.
# A stale environment marker alone must never bypass standalone isolation.
if [[ "${1:-}" != "--omnilane-offline-child" ]]; then
  exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/offline_env.py" \
    /bin/bash "$0" --omnilane-offline-child "$@"
fi
shift
# jobs close against a holder that never finishes: it must wait the full 11s
# before giving up, wherever the wall-clock second happens to be.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$ROOT" python3 - <<'PY'
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import time

root = pathlib.Path(os.environ['ROOT'])
scratch = pathlib.Path(tempfile.mkdtemp(prefix='close-floor-', dir=root / '.test-scratch'))
holder = None
try:
    home = scratch / 'home'
    job_id = '20260905-160000-12345-1'
    job = home / 'jobs' / job_id
    job.mkdir(parents=True)
    (job / 'meta.json').write_text('{"lane":"triage","vendor":"claude","session_mode":"live"}')
    # Stands in for a worker that takes the close signal but never finishes.
    holder = subprocess.Popen(['/bin/sh', '-c', 'trap "" USR1; exec sleep 60'], start_new_session=True)
    (job / 'inbox.holder.pid').write_text(f'{holder.pid}\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('OMNILANE_AA_')}
    env.update(OMNILANE_HOME=str(home), OMNILANE_AA_OPERATOR_ASSERTED_HUMAN='1')
    # Start just before a wall-clock second ticks over: an integer-second
    # deadline then gives up a whole second early.
    time.sleep((0.9 - time.time() % 1) % 1)
    started = time.monotonic()
    result = subprocess.run(['/bin/bash', str(root / 'scripts/jobs.sh'), 'close', job_id],
                            env=env, capture_output=True, text=True, timeout=20)
    elapsed = time.monotonic() - started
    assert result.returncode == 124, (result.returncode, result.stdout, result.stderr)
    assert elapsed >= 10.95, ('jobs close gave up before its 11s deadline', elapsed)
    assert elapsed < 12.0, ('jobs close overran its 11s deadline', elapsed)
    print(f'PASS jobs close waited {elapsed:.3f}s before 124', flush=True)
finally:
    if holder is not None and holder.poll() is None:
        os.killpg(holder.pid, signal.SIGKILL)
        holder.wait()
    shutil.rmtree(scratch)
PY
