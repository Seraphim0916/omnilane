"""Offline end-to-end doctor diagnostics."""

import offline_env
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from test_probe_retry import RetryFixture, ROOT


class DoctorRoutingTests(unittest.TestCase):
    def test_routing_distinguishes_unusable_lanes_from_an_empty_list(self):
        cases = (
            ("unusable", "disabled: off\nmissing: exec {home}/missing.sh\n",
             [("disabled", "off"), ("missing", "unavailable")],
             "WARN", "2 lanes parsed, but none is currently usable", 0),
            ("empty", "# No lanes\n", [], "FAIL", "effective routing is empty", 1),
        )
        for name, routing, listed_lanes, level, message, exit_code in cases:
            with self.subTest(case=name):
                temporary = tempfile.TemporaryDirectory(prefix="omnilane-doctor-routing-")
                self.addCleanup(temporary.cleanup)
                env, network_log = offline_env.isolated_environment(Path(temporary.name), "")
                self.addCleanup(lambda log=network_log: self.assertFalse(
                    log.exists(), "doctor or list attempted an unmocked network command"))
                home = Path(env["HOME"])
                # Copy the entrypoint and its support files unchanged. Only the
                # fixture's routing table varies; dispatch --list is not stubbed.
                repo = home / "repo"
                for directory in ("bin", "scripts", "config", "hooks"):
                    shutil.copytree(ROOT / directory, repo / directory)
                (repo / "routing.yaml").write_text(routing.format(home=home), encoding="utf-8")
                command = ["/bin/bash", str(repo / "bin" / "omnilane")]
                listed = subprocess.run([*command, "list"], cwd=repo, env=env,
                                        text=True, capture_output=True, timeout=45)
                self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
                self.assertEqual(
                    re.findall(r"^([a-z][a-z0-9-]*):[ \t]+(\S+)", listed.stdout, re.MULTILINE),
                    listed_lanes,
                )

                for options in ((), ("--json",)):
                    with self.subTest(options=options):
                        result = subprocess.run([*command, "doctor", *options], cwd=repo, env=env,
                                                text=True, capture_output=True, timeout=45)
                        if options:
                            report = json.loads(result.stdout)
                            self.assertEqual(
                                [row for row in report["checks"] if row["check"] == "routing"],
                                [{"level": level, "check": "routing", "message": message}],
                            )
                            self.assertIs(report["ok"], exit_code == 0)
                        else:
                            self.assertEqual(
                                re.findall(r"^(\S+)[ \t]+routing[ \t]+(.+)$",
                                           result.stdout, re.MULTILINE),
                                [(level, message)],
                            )
                        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)


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

    def test_tampered_failed_evidence_warns_without_disabling_intact_rows(self):
        entry = self.fail_row("api")
        path = self.sweep_root / "evidence" / f"{entry['name']}.json"
        path.write_text(path.read_text() + " ")
        before = self.live.read_bytes()
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [])
        doctor = self.doctor("omnilane doctor", "--json")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        report = json.loads(doctor.stdout)
        overlay = next(c for c in report["checks"] if c["check"] == "transport-overlay")
        self.assertEqual(overlay["level"], "WARN")
        self.assertIn("1 row(s) invalidated", overlay["message"])
        self.assertIn("omnilane resign --vendor claude", overlay["message"])
        row = next(c["details"] for c in report["checks"] if c["check"] == "transport-unproven")
        self.assertIn(str(path), row["evidence_paths"])
        self.assertIsNone(row["last_probe_at"])
        self.assertIn("cannot authorize automatic retry", row["next_step"])
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
