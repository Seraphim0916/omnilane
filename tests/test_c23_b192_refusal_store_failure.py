import offline_env  # Activate suite isolation for direct file execution.
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import refusal_evidence


class RefusalStoreFailureTests(unittest.TestCase):
    def check_failure(self, error_type):
        refusal = {"allowed": False, "failed_gate": "caller-context", "code": "missing-context",
                   "reason": "verified caller required", "message": "Dispatch refused"}
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(os.environ, {"OMNILANE_REFUSAL_EVIDENCE": "1"}), \
                    patch.object(sys, "stdin", io.StringIO(json.dumps(refusal) + "\n")), \
                    patch.object(refusal_evidence, "store", side_effect=error_type("store failed")) as store, \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                rc = refusal_evidence.main(["--home", directory, "--lane", "test-lane",
                                            "--headline", "Refusal headline"])
            store.assert_called_once_with(home, refusal, "test-lane", "Refusal headline")
            self.assertEqual(rc, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue().splitlines(), [
                "omnilane: refusal evidence not stored (" + error_type.__name__ + ")",
                'omnilane refusal: failed_gate=caller-context code=missing-context '
                'reason="verified caller required" quote="Dispatch refused"',
            ])
            self.assertNotIn("evidence=", stderr.getvalue())
            self.assertNotIn(directory, stdout.getvalue() + stderr.getvalue())
            self.assertEqual(list(home.iterdir()), [])

    def test_permission_error_preserves_summary(self):
        self.check_failure(PermissionError)

    def test_file_exists_error_preserves_summary(self):
        self.check_failure(FileExistsError)

    def test_os_error_preserves_summary(self):
        self.check_failure(OSError)


if __name__ == "__main__":
    unittest.main()
