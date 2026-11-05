import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "c45_claim_wakeup", Path(__file__).resolve().parents[1] / "scripts/completion-wakeup.py")
wakeup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wakeup)


class ClaimBoundaryTests(unittest.TestCase):
    def check_boundary(self, now, expired):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            marker = home / "jobs" / "job-1" / "exit"
            marker.parent.mkdir(parents=True)
            marker.write_text("0")
            args = SimpleNamespace(home=str(home), run_id="run-1", lease_seconds=30)
            frozen = {"fixture": "identity"}
            key = wakeup.digest([args.run_id, "job-1", frozen, 0])
            event = dict(job_id="job-1", exit_code=0, state="claimed",
                         observed_at=50, claim_token="old", lease_until=100)
            state = dict(status="registered", expires_at=1000, workdir=str(home),
                         jobs={"job-1": frozen}, events={key: event})
            before = dict(event)
            with patch.object(wakeup, "identity", return_value=frozen), \
                    patch.object(wakeup.secrets, "token_hex", return_value="new-claim") as token:
                result = wakeup.poll(args, state, home / "registry.json", now)
            self.assertEqual(result["errors"], [])
            self.assertEqual(list(state["events"]), [key])
            self.assertFalse(result["needs_disable"])
            if expired:
                token.assert_called_once_with(16)
                self.assertEqual(result["events"], [dict(
                    event_key=key, job_id="job-1", exit_code=0,
                    claim_token="new-claim", lease_until=now + 30)])
                self.assertEqual(event["claim_token"], "new-claim")
                self.assertEqual(event["lease_until"], now + 30)
                self.assertEqual(event["observed_at"], 50)
            else:
                token.assert_not_called()
                self.assertEqual(result["events"], [])
                self.assertEqual(event, before)

    def test_before_expiry_preserves_claim(self):
        self.check_boundary(99.999999, False)

    def test_exact_expiry_reclaims_same_event(self):
        self.check_boundary(100, True)

    def test_after_expiry_reclaims_same_event(self):
        self.check_boundary(100.000001, True)


if __name__ == "__main__":
    unittest.main()
