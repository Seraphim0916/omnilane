"""Offline end-to-end diagnostics reached from a refused dispatch."""

import offline_env  # noqa: F401
import json
import os
import shlex
import subprocess
import unittest

from test_probe_retry import RetryFixture, ROOT


class DoctorDiagnosticsTests(RetryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Make every pinned vendor executable visible without running a provider.
        for path in self.bins.values():
            path.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then '
                            'printf "offline fixture 1.0.0\\n"; exit 0; fi\nexit 97\n')
            path.chmod(0o755)
        self.doctor_env = dict(os.environ)
        self.doctor_env.update({f"{vendor.upper()}_BIN" if vendor != "gemini" else "AGY_BIN": str(path)
                                for vendor, path in self.bins.items()})
        self.doctor_env["PATH"] = os.pathsep.join(
            [str(path.parent) for path in self.bins.values()] + [os.environ["PATH"]])

    def doctor(self, command="omnilane doctor", *options):
        argv = shlex.split(command)
        self.assertEqual(argv, ["omnilane", "doctor"])
        return subprocess.run(["/bin/bash", str(ROOT / "bin/omnilane"), *argv[1:], *options],
                              env=self.doctor_env, text=True, capture_output=True, timeout=45)

    def test_refusal_next_command_shows_conclusive_row_in_text_and_json(self):
        entry = self.fail_row("wrong-model")
        decision = self.decision(entry)
        self.assertFalse(decision["allowed"])
        before = self.live.read_bytes()
        for options in ((), ("--json",)):
            with self.subTest(options=options):
                result = self.doctor(decision["next_command"], *options)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for value in (entry["config_id"], entry["model"], entry["effort"],
                              "some-other-model", "model-mismatch", "conclusive",
                              "will not retry automatically", "requested", "observed",
                              "last_probe_at", str(self.sweep_root / "evidence")):
                    self.assertIn(value, result.stdout)
                if options:
                    report = json.loads(result.stdout)
                    row = next(c for c in report["checks"] if c["check"] == "transport-unproven")
                    self.assertEqual(row["level"], "WARN")
                    self.assertEqual(row["details"]["config_id"], entry["config_id"])
                    self.assertEqual(row["details"]["requested_model"], entry["model"])
                    self.assertEqual(row["details"]["observed_model"], "some-other-model")
                    self.assertIsNone(row["details"]["observed_effort"])
                    self.assertTrue(row["details"]["last_probe_at"])
                    self.assertEqual(report["summary"]["failed"], 0)
                    self.assertTrue(report["ok"])
        self.assertEqual(self.live.read_bytes(), before)
        self.assertFalse(self.decision(entry)["allowed"])

    def test_environment_failure_names_retry_command_and_does_not_dump_evidence(self):
        entry = self.fail_row("api")
        result = self.doctor("omnilane doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = next(c["details"] for c in json.loads(result.stdout)["checks"]
                   if c["check"] == "transport-unproven")
        self.assertEqual(row["failure_class"], "environment")
        self.assertIn("omnilane resign --retry-failed --vendor claude", row["next_step"])
        self.assertIn("api-error", row["reason"])
        self.assertIn(str(self.sweep_root / "evidence" / f"{entry['name']}.stdout"),
                      row["evidence_paths"])
        self.assertNotIn('"modelUsage"', result.stdout)
        self.assertEqual(self.calls, [])

    def test_tampered_evidence_names_path_and_both_recovery_routes(self):
        entry = self.fail_row("api")
        path = self.sweep_root / "evidence" / f"{entry['name']}.json"
        path.write_text(path.read_text() + " ")
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(retry_failed=True), 40, self.lines)
        self.assertEqual(self.calls, [])
        doctor = self.doctor("omnilane doctor", "--json")
        self.assertNotEqual(doctor.returncode, 0)
        for message in ("\n".join(self.lines), doctor.stdout):
            for value in (str(path), "known-good backup", "probe_sweep.py",
                          "build_overlay.py", "--root", "REPROBE_ROOT"):
                self.assertIn(value, message)
        self.assertEqual(self.live.read_bytes(), before)

    def test_mixed_failures_keep_separate_classes_and_recorded_observed_effort(self):
        environment = self.fail_row("api")
        conclusive = self.fail_row("wrong-effort", index=-2)
        path = self.sweep_root / "evidence" / f"{conclusive['name']}.json"
        record = json.loads(path.read_text())
        record["observed_effort"] = "low"
        path.write_text(json.dumps(record))
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / "transport-contracts.local.json").read_bytes())
        result = self.doctor("omnilane doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = {c["details"]["config_id"]: c for c in json.loads(result.stdout)["checks"]
                if c["check"] == "transport-unproven"}
        self.assertEqual(set(rows), {environment["config_id"], conclusive["config_id"]})
        self.assertEqual(rows[environment["config_id"]]["details"]["failure_class"], "environment")
        self.assertEqual(rows[conclusive["config_id"]]["details"]["failure_class"], "conclusive")
        self.assertEqual(rows[conclusive["config_id"]]["details"]["observed_effort"], "low")
        self.assertTrue(all(row["level"] == "WARN" for row in rows.values()))
        strict = self.doctor("omnilane doctor", "--json", "--strict")
        self.assertNotEqual(strict.returncode, 0)
        self.assertEqual(json.loads(strict.stdout)["summary"]["failed"], 0)

    def test_recovery_display_does_not_mask_a_malformed_overlay_exit(self):
        self.change_overlay(lambda overlay: overlay.update(evidence=None))
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(retry_failed=True), 40, self.lines)
        self.assertIn("known-good backup", "\n".join(self.lines))
        self.assertIn(str(self.live), "\n".join(self.lines))
        self.assertEqual(self.live.read_bytes(), before)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
