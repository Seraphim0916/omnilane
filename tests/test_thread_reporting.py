"""Thread inspection preserves validated Unicode metadata without rewriting it."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]


class ThreadReportingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-thread-report-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "tools", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        home = self.root / "home"
        (home / "threads").mkdir(parents=True)
        self.env["OMNILANE_HOME"] = str(home)
        self.path = home / "threads/sample.json"

    def run_cli(self, *args):
        result = subprocess.run(["bash", str(ROOT / "scripts/jobs.sh"), *args],
                                env=self.env, capture_output=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"", result.stderr)
        return result.stdout.decode("utf-8")

    def assert_unicode_state(self, label):
        # Exercise each field independently: another wide character must not
        # accidentally cause Perl to encode an otherwise Latin-1 byte string.
        for model, workdir in ((label, "/tmp/fixture"), ("fake-model", "/tmp/" + label)):
            for escaped in (False, True):
                with self.subTest(model=model, workdir=workdir, escaped=escaped):
                    state = {"name": "sample", "vendor": "claude", "model": model,
                             "effort": "low", "workdir": workdir,
                             "session_id": "fixture-session", "turns": 1,
                             "last_job_id": "20261002-000000-123-1",
                             "created": "2026-10-01T16:00:00Z",
                             "updated": "2026-10-01T16:00:00Z"}
                    self.path.write_text(json.dumps(state, ensure_ascii=escaped) + "\n",
                                         encoding="utf-8")
                    before = self.path.read_bytes()
                    listing = self.run_cli("threads", "list")
                    self.assertEqual(model, listing.splitlines()[1].split("\t")[2])
                    self.assertEqual([state], json.loads(self.run_cli("--json", "threads"))["threads"])
                    self.assertEqual(state, json.loads(self.run_cli("threads", "show", "sample")))
                    self.assertEqual(state, json.loads(self.run_cli("--json", "threads", "show", "sample"))["thread"])
                    result = subprocess.run(
                        ["bash", "-c", 'source "$1"; read_thread_state "$2" sample || exit 1; '
                         'printf "%s\\034%s" "$THREAD_STATE_MODEL" "$THREAD_STATE_WORKDIR"',
                         "fixture", str(ROOT / "scripts/lib/common.sh"), str(self.path)],
                        env=self.env, capture_output=True, timeout=5)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(b"", result.stderr)
                    self.assertEqual(model + "\034" + workdir, result.stdout.decode("utf-8"))
                    self.assertEqual(before, self.path.read_bytes())

    def test_ascii_metadata_is_preserved(self):
        self.assert_unicode_state("fake-model")

    def test_latin1_metadata_is_utf8(self):
        self.assert_unicode_state("modèle-café")

    def test_cjk_metadata_is_utf8(self):
        self.assert_unicode_state("模型資料")


if __name__ == "__main__":
    unittest.main()
