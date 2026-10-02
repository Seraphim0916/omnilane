"""An unconfirmed cleanup status is terminal, not permission for another attempt."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


class RetryCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='omnilane-retry-cleanup-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / 'tools', os.environ['PATH'])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.attempts = self.root / 'attempts'
        self.release = self.root / 'release'
        self.prompt = self.root / 'prompt'
        self.prompt.write_text('offline fixture')
        self.output = self.root / 'job/out.txt'
        self.output.parent.mkdir()
        self.grok = self.root / 'fake-grok'
        self.env.update(GROK_BIN=str(self.grok), RETRY_ROOT=str(self.root),
                        OMNILANE_GROK_MAX_ATTEMPTS='5', OMNILANE_TIMEOUT='1',
                        OMNILANE_PROCESS_JOB_DIR=str(self.output.parent))

    def command(self):
        return ['bash', str(ROOT / 'scripts/runners/run-grok.sh'), 'advise', str(self.root),
                'offline-model', '-', str(self.prompt), str(self.output)]

    def write_grok(self, code):
        self.grok.write_text('#!' + sys.executable + '\n' + code)
        self.grok.chmod(0o755)

    def test_unconfirmed_status_125_does_not_retry_empty_output(self):
        self.write_grok("import os,pathlib\np=pathlib.Path(os.environ['RETRY_ROOT'])/'attempts'\n"
                        "with p.open('a') as f:f.write('attempt\\n')\nraise SystemExit(125)\n")
        result = subprocess.run(self.command(), env=self.env, text=True, capture_output=True, timeout=8)
        self.assertEqual(125, result.returncode, result.stderr)
        self.assertEqual(1, len(self.attempts.read_text().splitlines()), 'unsafe retry after status 125')
        self.assertIn('after 1 attempts', Path(str(self.output) + '.stderr.log').read_text())

    def test_known_escape_prevents_next_grok_attempt(self):
        self.write_grok('''import os,pathlib,signal,time
root=pathlib.Path(os.environ['RETRY_ROOT'])
with (root/'attempts').open('a') as f:f.write('attempt\\n')
pid=os.fork()
if pid == 0:
    os.setsid()
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    (root/('leaf-'+str(os.getpid()))).write_text(str(os.getpid()))
end=time.monotonic()+20
while not (root/'release').exists() and time.monotonic()<end:time.sleep(.02)
os._exit(0)
''')
        out = (self.root / 'runner.stdout').open('w+')
        err = (self.root / 'runner.stderr').open('w+')
        process = subprocess.Popen(self.command(), env=self.env, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + 8
            attempts = []
            while time.monotonic() < deadline:
                attempts = self.attempts.read_text().splitlines() if self.attempts.exists() else []
                if len(attempts) > 1 or process.poll() is not None:
                    break
                time.sleep(.02)
            self.assertEqual(1, len(attempts), 'a new Grok attempt started while an escape remained')
            self.assertEqual(125, process.wait(timeout=2))
            reports = list(self.output.parent.glob('call-cleanup.*.json'))
            self.assertTrue(reports, 'known escape needs explicit cleanup evidence')
        finally:
            self.release.touch()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
            out.close();err.close()
            # Cooperative leaf shutdown works on both macOS and Linux; no
            # detached numeric PID is signalled by the fixture.
            end = time.monotonic() + 3
            while time.monotonic() < end:
                alive = []
                for marker in self.root.glob('leaf-*'):
                    pid = int(marker.read_text())
                    state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                                           env=self.env, text=True, capture_output=True, timeout=2)
                    value = state.stdout.strip()
                    if value and not value.startswith(('Z', 'X')):
                        alive.append(pid)
                if not alive:
                    break
                time.sleep(.02)
            self.assertFalse(alive, 'owned fixture failed cooperative cleanup')
