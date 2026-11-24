"""Read-only CLI cancellation fixtures; no providers or signal delivery."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
START = 1_760_000_000
FIELDS = dict(lane="fixture", vendor="exec", model="fixture-model",
              mode="advise", workdir="/fixture", foreman_session="")


class CancelOutputProvenanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="omnilane-cancel-output-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.env, violations = isolated_environment(root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = root / "home"
        self.env["OMNILANE_HOME"] = str(self.home)
        self.id = "20251009-085320-123-1"
        self.job = self.home / "jobs" / self.id
        self.job.mkdir(parents=True)
        self.inbox = self.home / "inbox"
        self.inbox.mkdir()
        self.put(self.job / "meta.json", json.dumps(dict(FIELDS, started="2025-10-09T08:53:20Z")), START)

    def put(self, path, payload, stamp):
        path.write_bytes(payload if isinstance(payload, bytes) else payload.encode())
        os.utime(path, ns=(stamp * 10**9, stamp * 10**9))

    def exit(self, code):
        self.put(self.job / "exit", f"{code}\n", START + 10)

    def receipt(self, tail, *, stamp=START + 13, **overrides):
        record = dict(FIELDS, job_id=self.id, exit=0, finished="2025-10-09T08:53:33Z", tail=tail)
        record.update(overrides)
        self.put(self.inbox / (self.id + ".json"), json.dumps(record), stamp)

    def cli(self, json_mode=False):
        args = ["--json"] if json_mode else []
        return subprocess.run([str(ROOT / "bin/omnilane"), "jobs", *args, "result", self.id],
                              env=self.env, capture_output=True, timeout=8)

    def check(self, code, status, source, output, *, formal=False, truncated=False):
        # subTest keeps both public surfaces exercising the original baseline,
        # even when its first assertion fails.
        with self.subTest(surface="text"):
            got = self.cli()
            self.assertEqual(got.returncode, code)
            self.assertEqual(got.stdout, output)
            self.assertIn(f"cancelled/interrupted (exit {code})".encode(), got.stderr)
            self.assertIn(f"output provenance: {status}".encode(), got.stderr)
        with self.subTest(surface="json"):
            got = self.cli(json_mode=True)
            self.assertEqual(got.returncode, code)
            self.assertEqual(got.stderr, b"")
            record = json.loads(got.stdout)
            self.assertEqual((record["schema_version"], record["command"], record["ok"]),
                             (1, "result", True))
            data = record["job"]
            for k, v in dict(id=self.id, state="done", exit_code=code, output_available=formal,
                             stderr_available=False).items():
                self.assertEqual(data[k], v, k)
            self.assertEqual(data.get("cancelled"), True)
            self.assertEqual(data.get("output_provenance"),
                             dict(status=status, source=source, preview_truncated=truncated))

    def test_stale_formal_result_is_not_promoted(self):
        self.exit(143)
        self.put(self.job / "out.txt", "old result\n", START - 2)
        self.receipt("old result\n")
        self.check(143, "stale", "out.txt", b"old result\n", formal=True)

    def test_completion_must_match_current_whole_output(self):
        self.exit(137)
        self.put(self.job / "out.txt", "confirmed answer\n", START + 12)
        self.receipt("confirmed answer\n")
        # Codex transport recovery rewrites metadata without changing started.
        os.utime(self.job / "meta.json", ns=((START + 15) * 10**9,) * 2)
        self.check(137, "completed", "out.txt", b"confirmed answer\n", formal=True)
        self.put(self.job / "out.txt", "changed answer\n", START + 14)
        self.check(137, "unknown", "out.txt", b"changed answer\n", formal=True)

    def test_temporary_result_is_partial_without_promoting_or_moving_it(self):
        self.exit(143)
        tmp = self.job / "out.txt.tmp"
        self.put(tmp, "writing stopped here\n", START + 9)
        self.check(143, "partial", "out.txt.tmp", b"writing stopped here\n")
        self.assertEqual(tmp.read_bytes(), b"writing stopped here\n")
        self.assertFalse((self.job / "out.txt").exists())

    def test_unverified_formal_result_stays_unknown(self):
        self.exit(137)
        self.put(self.job / "out.txt", "published without proof\n", START + 3)
        self.check(137, "unknown", "out.txt", b"published without proof\n", formal=True)

    def test_matching_success_from_before_recorded_cancellation_is_stale(self):
        self.exit(137)
        self.put(self.job / "out.txt", "older success\n", START + 3)
        self.receipt("older success\n", stamp=START + 5, finished="2025-10-09T08:53:25Z")
        self.check(137, "stale", "out.txt", b"older success\n", formal=True)

    def test_cancel_request_time_cannot_be_inferred_from_exit_file(self):
        self.exit(143)
        self.put(self.job / "out.txt", "answer from uncertain interval\n", START + 9)
        self.receipt("answer from uncertain interval\n")
        self.check(143, "unknown", "out.txt", b"answer from uncertain interval\n", formal=True)

    def test_mismatched_and_failed_receipts_never_confirm(self):
        self.exit(143)
        answer = "published answer\n"
        self.put(self.job / "out.txt", answer, START + 12)
        for fields in ({"job_id": "other-job"}, {"model": "different-model"},
                       {"exit": 143}, {"exit": False}, {"vendor": "other"},
                       {"finished": "not-a-time"}, {"finished": "2025-10-09T08:54:23Z"},
                       {"thread": "different-thread"}):
            with self.subTest(fields=fields):
                self.receipt(answer, **fields)
                self.check(143, "unknown", "out.txt", answer.encode(), formal=True)

    def test_truncated_tail_or_lossy_text_cannot_confirm(self):
        self.exit(137)
        for answer in ("[truncated: leading output omitted]\nmatching tail\n",
                       "already sanitized �\n", "x" * 2001, ""):
            with self.subTest(answer_length=len(answer)):
                self.put(self.job / "out.txt", answer, START + 12)
                self.receipt(answer)
                self.check(137, "unknown", "out.txt", answer.encode(), formal=True)

    def test_receipt_must_be_local_and_regular(self):
        self.exit(137)
        answer = "published answer\n"
        self.put(self.job / "out.txt", answer, START + 12)
        self.receipt(answer)
        receipt = self.inbox / (self.id + ".json")
        outside = receipt.rename(self.home / "outside-receipt")
        receipt.symlink_to(outside)
        self.check(137, "unknown", "out.txt", answer.encode(), formal=True)
        receipt.unlink()
        real_inbox = self.inbox.rename(self.home / "elsewhere")
        outside.rename(real_inbox / receipt.name)
        self.inbox.symlink_to(real_inbox, target_is_directory=True)
        self.check(137, "unknown", "out.txt", answer.encode(), formal=True)

    def test_temporary_preview_does_not_read_links_or_special_files(self):
        self.exit(137)
        external = self.home / "outside"
        self.put(external, "outside output\n", START + 3)
        path = self.job / "out.txt.tmp"
        path.symlink_to(external)
        self.check(137, "unknown", None, b"")
        path.unlink()
        os.link(external, path)
        self.check(137, "unknown", None, b"")
        path.unlink()
        os.mkfifo(path)
        self.check(137, "unknown", None, b"")
        path.unlink()
        path.mkdir()
        self.check(137, "unknown", None, b"")

    def test_temporary_preview_is_bounded_and_no_terminal_controls(self):
        self.exit(143)
        self.put(self.job / "out.txt.tmp", b"\x1b[2J\x00" + b"x" * 9000, START + 9)
        self.check(143, "partial", "out.txt.tmp", b"?[2J?" + b"x" * (8192 - 5), truncated=True)
        self.assertIn(b"temporary preview, truncated", self.cli().stderr)

    def test_hardlinked_formal_output_keeps_legacy_body_and_availability(self):
        self.exit(137)
        answer = b"published answer with a second name\n"
        output = self.job / "out.txt"
        self.put(output, answer, START + 3)
        os.link(output, self.job / "output-copy")
        self.assertEqual(output.stat().st_nlink, 2)
        self.check(137, "unknown", "out.txt", answer, formal=True)

    def test_formal_bytes_and_stderr_are_preserved_and_take_precedence(self):
        self.exit(137)
        data = bytes(range(256)) * 100
        self.put(self.job / "out.txt", data, START + 3)
        self.put(self.job / "out.txt.tmp", "newer temporary answer", START + 4)
        self.put(self.job / "out.txt.stderr.log", "diagnostic\n", START + 5)
        text_result = self.cli()
        self.assertEqual((text_result.returncode, text_result.stdout), (137, data))
        self.assertIn(b"--- stderr ---\ndiagnostic\n", text_result.stderr)
        self.assertTrue(json.loads(self.cli(json_mode=True).stdout)["job"]["stderr_available"])

    def test_non_cancelled_results_keep_their_exact_contract(self):
        for code in (0, 7, 124, 125, 142):
            self.exit(code)
            self.put(self.job / "out.txt", "original body\n", START + 3)
            self.put(self.job / "out.txt.stderr.log", "original diagnostic\n", START + 4)
            got = self.cli()
            self.assertEqual((got.returncode, got.stdout, got.stderr),
                             (code, b"original body\n", b"--- stderr ---\noriginal diagnostic\n"))
            got = self.cli(json_mode=True)
            self.assertEqual((got.returncode, got.stderr), (code, b""))
            self.assertEqual(json.loads(got.stdout), dict(
                schema_version=1, command="result", ok=True, job=dict(
                    id=self.id, state="done", exit_code=code, output_available=True,
                    stderr_available=True)))


if __name__ == "__main__":
    unittest.main()
