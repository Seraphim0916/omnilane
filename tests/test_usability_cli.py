"""Exercise the public CLI with private homes, fake executables and no providers."""
from offline_env import isolated_environment
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UsabilityCLITests(unittest.TestCase):
    def test_dispatch_renderer_redacts_private_fields_but_preserves_json_bytes(self):
        from test_usability import PRIVATE_FIXTURE, assert_private_line
        text = (ROOT / "scripts/dispatch.sh").read_text()
        function = text[text.index("aa_print_refusal() {"):text.index("\ninherit_fallback()")]
        decision = json.dumps(dict(allowed=False, code="deny", failed_gate=PRIVATE_FIXTURE,
                                  reason=PRIVATE_FIXTURE, message=PRIVATE_FIXTURE,
                                  next_command="omnilane native-context --workdir " + PRIVATE_FIXTURE))
        script = function + '\nOMNILANE_REPO="$1"\nOMNILANE_HOME="$2"\nAA_LAST_DECISION="$3"\naa_print_refusal\n'
        # No aa_lanes enrichment: exercise exactly the bytes supplied to the renderer.
        repo = self.base / "renderer"
        lib = repo / "scripts/lib"
        lib.mkdir(parents=True)
        for name in ("refusal_evidence.py", "json_depth.py"):
            (lib / name).symlink_to(ROOT / "scripts/lib" / name)
        result = subprocess.run(["/bin/bash", "-c", script, "fixture", str(repo),
                                 self.env["OMNILANE_HOME"], decision], env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        human, raw, summary = result.stderr.splitlines()
        assert_private_line(self, human)
        self.assertEqual(raw, decision)
        command = summary.split('next_command="', 1)[1].split('"', 1)[0]
        self.assertNotIn("alice@example.invalid", command)
        self.assertNotIn("sk-syntheticToken", command)
        legacy = subprocess.run(["/bin/bash", "-c", script, "fixture", str(repo),
                                 self.env["OMNILANE_HOME"], decision],
                                env=dict(self.env, OMNILANE_REFUSAL_JSON_ONLY="1"),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(legacy.returncode, 0)
        self.assertEqual(legacy.stderr, decision + "\nomnilane refusal: failed_gate=" + PRIVATE_FIXTURE
                         + ' code=deny reason="' + PRIVATE_FIXTURE + '" quote="' + PRIVATE_FIXTURE + '"\n')

    def test_disabled_lane_human_line_never_interpolates_private_lane_name(self):
        for lane in ("alice@example.invalid", "sk-synthetictoken012345678901234567"):
            home = Path(self.env["OMNILANE_HOME"])
            home.mkdir(exist_ok=True)
            (home / "routing.local.yaml").write_text(lane + ": off\n")
            result = self.run_cli("route", "--dry-run", "--workdir", "/tmp/alice@example.invalid", lane, "fixture")
            self.assertEqual(result.returncode, 2 if "@" in lane else 3, result.stderr)
            self.assertNotIn(lane, result.stderr.splitlines()[0])
            if "@" not in lane:
                self.assertEqual(json.loads(result.stderr.splitlines()[1])["lane"], lane)
        self.assertFalse(self.called.exists())

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="usability-cli-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.env, self.violations = isolated_environment(self.base / "isolation", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.env.pop("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN")
        self.env.update(OMNILANE_HOME=str(self.base / "omnilane"), OMNILANE_AA_CALLER_FROM_PROCESS="0",
                        OMNILANE_REFUSAL_EVIDENCE="0")
        self.bins = self.base / "bin"
        self.bins.mkdir()
        self.called = self.base / "vendor-called"
        for name in ("claude", "codex", "agy", "grok"):
            self.executable(name, f"#!/bin/sh\necho called >> {shlex.quote(str(self.called))}\nexit 99\n")
        self.env["PATH"] = str(self.bins) + os.pathsep + self.env["PATH"]

    def executable(self, name, content):
        path = self.bins / name
        path.write_text(content)
        path.chmod(0o755)

    def run_cli(self, *args):
        return subprocess.run(["/bin/bash", str(ROOT / "bin/omnilane"), *args],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_refusal_starts_with_readable_line_then_json(self):
        result = self.run_cli("route", "--dry-run", "consult", "fixture task")
        self.assertNotEqual(result.returncode, 0)
        lines = result.stderr.splitlines()
        self.assertTrue(lines[0].startswith("omnilane: refused (caller-identity):"), result.stderr)
        decision = json.loads(lines[1])
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["code"], "missing-caller-context")
        self.assertIn("Next: ", lines[0])
        self.assertFalse(self.called.exists())

    def test_process_diagnostics_drive_the_refusal_without_granting_authority(self):
        diagnostics = [
            ("no vendor CLI among this process's ancestors", "--operator-asserted-human"),
            ("codex direct child CODEX_THREAD_ID is missing; run the omnilane command as the only command", "only command"),
            ("no scored configuration for codex future-model at effort high", "--inherit"),
        ]
        self.env["OMNILANE_AA_CALLER_FROM_PROCESS"] = "1"
        for diagnostic, expected in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.executable("python3", "#!/bin/sh\ncase \"$1\" in\n"
                                f"*/caller_identity.py) printf '%s\\n' {shlex.quote(diagnostic)} >&2; exit 3;;\nesac\n"
                                f"exec {shlex.quote(sys.executable)} \"$@\"\n")
                result = self.run_cli("route", "--dry-run", "consult", "fixture task")
                lines = result.stderr.splitlines()
                self.assertTrue(lines[0].startswith("omnilane: refused"), result.stderr)
                self.assertIn(expected, lines[0])
                decision = json.loads(lines[1])
                self.assertFalse(decision["allowed"])
                self.assertIn(expected, decision["next_command"])
                if expected == "--inherit":
                    self.assertIn("by design", lines[0])
                    self.assertNotIn("whoami", decision["next_command"])
        self.assertFalse(self.called.exists())

    def test_json_only_retains_the_original_refusal_bytes(self):
        # Isolate the shell renderer with no lane enrichment or generated evidence path.
        text = (ROOT / "scripts/dispatch.sh").read_text()
        function = text[text.index("aa_print_refusal() {"):text.index("\ninherit_fallback()")]
        repo = self.base / "renderer"
        lib = repo / "scripts/lib"
        lib.mkdir(parents=True)
        for name in ("refusal_evidence.py", "json_depth.py"):
            (lib / name).symlink_to(ROOT / "scripts/lib" / name)
        decision = '{"allowed":false,"code":"deny","failed_gate":"gate","reason":"reason","message":"message","next_command":"omnilane doctor"}'
        script = function + '\nOMNILANE_REPO="$1"\nOMNILANE_HOME="$2"\nAA_LAST_DECISION="$3"\naa_print_refusal\n'
        env = dict(self.env, OMNILANE_REFUSAL_JSON_ONLY="1")
        result = subprocess.run(["/bin/bash", "-c", script, "fixture", str(repo),
                                 self.env["OMNILANE_HOME"], decision], env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, decision + '\nomnilane refusal: failed_gate=gate code=deny reason="reason" quote="message"\n')

    def test_identity_failures_keep_original_detail_and_actionable_next_step(self):
        diagnostics = [
            ("no vendor CLI among this process's ancestors", "--operator-asserted-human"),
            ("codex direct child CODEX_THREAD_ID is missing; run the omnilane command as the only command", "only command"),
            ("no scored configuration for codex future-model at effort high", "--inherit"),
            ("Codex sandbox (CODEX_SANDBOX=seatbelt) blocked process inspection", "authorized for process inspection"),
            ("claude-opus-5 was launched without --effort and no default is scored for it", "--effort"),
            ("claude was launched without a model flag", "--model"),
            ("codex was launched without model_reasoning_effort; its configured default is not read", "model_reasoning_effort"),
        ]
        self.env["OMNILANE_AA_CALLER_FROM_PROCESS"] = "1"
        for diagnostic, expected in diagnostics:
            detail = "omnilane: cannot read the caller identity: " + diagnostic
            self.executable("python3", "#!/bin/sh\ncase \"$1\" in\n"
                            f"*/caller_identity.py) printf '%s\\n' {shlex.quote(detail)} >&2; exit 3;;\nesac\n"
                            f"exec {shlex.quote(sys.executable)} \"$@\"\n")
            for json_only in (False, True):
                with self.subTest(diagnostic=diagnostic, json_only=json_only):
                    self.env["OMNILANE_REFUSAL_JSON_ONLY"] = "1" if json_only else "0"
                    result = self.run_cli("route", "--dry-run", "consult", "fixture task")
                    self.assertEqual(result.returncode, 4, result.stderr)
                    self.assertIn(detail, result.stderr.splitlines())
                    decision = next(json.loads(line) for line in result.stderr.splitlines() if line.startswith("{"))
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(decision["code"], "missing-caller-context")
                    self.assertIsNone(decision["effective_ceiling"])
                    if not json_only:
                        human = result.stderr.splitlines()[0]
                        self.assertTrue(human.startswith("omnilane: refused"), human)
                        self.assertIn(expected, human)
                        self.assertNotEqual(decision["next_command"], "omnilane whoami")
                    else:
                        self.assertNotIn("omnilane: refused", result.stderr)
        self.assertFalse(self.called.exists())

    def test_unavailable_dry_run_preserves_exit_and_original_explanation(self):
        home = Path(self.env["OMNILANE_HOME"])
        home.mkdir()
        (home / "routing.local.yaml").write_text("offline: codex unavailable-model low\n")
        self.env.update(OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1", CODEX_BIN=str(home / "missing-codex"))
        for json_only in (False, True):
            with self.subTest(json_only=json_only):
                self.env["OMNILANE_REFUSAL_JSON_ONLY"] = "1" if json_only else "0"
                result = self.run_cli("route", "--dry-run", "offline", "fixture")
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertIn("no eligible available target", result.stderr)
                if not json_only:
                    self.assertTrue(result.stderr.startswith("omnilane: refused"), result.stderr)
        self.assertFalse(self.called.exists())
        self.assertFalse((home / "jobs").exists())

    def test_init_refuses_non_tty_and_daily_before_executing_vendors(self):
        for args in (("--init",), ("--init", "--daily")):
            result = self.run_cli("resign", *args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--init", result.stderr)
        self.assertFalse(self.called.exists())
        self.assertFalse((Path(self.env["OMNILANE_HOME"]) / "local.sh").exists())

    def test_doctor_points_first_install_at_one_command(self):
        result = self.run_cli("doctor", "--json")
        report = json.loads(result.stdout)
        row = next(check for check in report["checks"] if check["check"] == "transport-overlay")
        self.assertIn("omnilane resign --init", str(row))


if __name__ == "__main__":
    unittest.main()
