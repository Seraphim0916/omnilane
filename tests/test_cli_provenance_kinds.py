"""Injected signature checks; never inspect a real provider executable."""
import offline_env  # noqa: F401
import sys
import tempfile
import unittest
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import cli_provenance as cp
import origin_policy as op

TEAM = "TEST000001"


class KindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".sandbox-tmp")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "binary"
        self.path.write_bytes(b"\xcf\xfa\xed\xfe" + b"test")
        platform = patch.object(sys, "platform", "darwin")
        platform.start()
        self.addCleanup(platform.stop)
        self.calls = []

    def runner(self, *, integrity=0, origin=0, display=None, display_rc=0):
        def run(argv, **kwargs):
            self.calls.append(argv)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["timeout"], 15)
            if "-R" in argv:
                self.assertEqual(len(argv), 6)
                if not argv[4].startswith("="):
                    return SimpleNamespace(returncode=1, stdout="",
                                           stderr="invalid requirement specification")
                self.assertTrue(argv[4].startswith("=anchor apple generic"))
                self.assertIn('anchor apple generic', argv[4])
                self.assertIn('certificate leaf[subject.OU] = "TEST000001"', argv[4])
                messages = {0: "", 1: "invalid requirement specification",
                            2: "invalid arguments",
                            3: "code failed to satisfy specified code requirement(s)"}
                return SimpleNamespace(returncode=origin, stdout="", stderr=messages.get(origin, "tool failed"))
            if "--verify" in argv:
                return SimpleNamespace(returncode=integrity, stdout="", stderr="")
            return SimpleNamespace(returncode=display_rc, stdout="", stderr=(
                "Identifier=vendor.1.2.3\nTeamIdentifier=" + TEAM + "\n" if display is None else display))
        return run

    def test_valid_developer_id_requires_external_requirement(self):
        result = cp.facts(self.path, self.runner())
        self.assertEqual(result, dict(kind="developer-id", signer=TEAM, identifier="vendor.1.2.3", valid=True))
        self.assertEqual(len(self.calls), 3)

    def test_case01_forged_team_with_non_apple_chain_stops(self):
        result = cp.facts(self.path, self.runner(origin=3))
        self.assertEqual(result["kind"], "invalid")
        self.assertFalse(op.gate("claude", None, None, result, self.path)[0])
        self.assertFalse(cp.verdict({"signer": TEAM}, str(self.path), result, self.path)[0])

    def test_requirement_exit_codes_never_make_failed_checks_approvable(self):
        import resign
        from verification_status import origin_guidance
        for code, kind in ((3, "invalid"), (1, "unverifiable"), (2, "unverifiable"), (4, "unverifiable")):
            with self.subTest(code=code):
                result = cp.facts(self.path, self.runner(origin=code))
                self.assertEqual(result["kind"], kind)
                self.assertFalse(result["valid"])
                self.assertEqual(result["signer"], TEAM)
                self.assertFalse(op.gate("claude", None, None, result, self.path)[0])
                report = dict(vendor="claude", cli=str(self.path), cli_changed=True,
                              recorded_codesign=None, recorded_cli_path=None)
                with patch.object(cp, "facts", return_value=result):
                    for approved in (False, True):
                        self.assertFalse(resign.gate(report, approved)[0])
                if code == 3:
                    self.assertEqual(result["invalid_reason"], "requirement")
                    self.assertIn("憑證鏈要求", origin_guidance("claude", kind, result)["message_zh_tw"])

    def test_requirement_tool_failure_is_unverifiable(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("codesign", 15)):
            with self.subTest(error=type(error).__name__):
                delegate = self.runner()
                def run(argv, **kwargs):
                    if "-R" in argv:
                        raise error
                    return delegate(argv, **kwargs)
                result = cp.facts(self.path, run)
                self.assertEqual(result["kind"], "unverifiable")
                self.assertFalse(op.gate("claude", None, None, result, self.path)[0])

    def test_integrity_diagnostic_is_distinct_from_requirement_failure(self):
        from verification_status import origin_guidance
        result = cp.facts(self.path, self.runner(integrity=1))
        self.assertEqual(result["invalid_reason"], "integrity")
        self.assertIn("完整性", origin_guidance("claude", "invalid", result)["message_zh_tw"])
        self.assertFalse(any("-R" in call for call in self.calls))

    def test_diagnostic_field_cannot_grant_trust(self):
        for diagnostic in (None, "integrity", "requirement", [], {}, True, "developer-id"):
            with self.subTest(diagnostic=diagnostic):
                current = dict(kind="invalid", signer=TEAM, identifier="sample", valid=False)
                if diagnostic is not None:
                    current["invalid_reason"] = diagnostic
                self.assertFalse(op.gate("claude", None, None, current, self.path)[0])
                self.assertEqual(op.implicit_channels(current, str(self.path)), [])

    def test_unknown_macos_headers_never_invoke_signature_tools(self):
        for header in (b"\x7fELF", b"plain text", b"", b"\xcf\xfa\xed"):
            with self.subTest(header=header):
                self.path.write_bytes(header)
                self.calls.clear()
                result = cp.facts(self.path, self.runner())
                self.assertEqual(result["kind"], "unverifiable")
                self.assertEqual(self.calls, [])
                self.assertFalse(op.gate("claude", None, None, result, self.path)[0])

    def test_missing_signature_field_does_not_infer_adhoc(self):
        for signature in ("", "Signature=unknown\n"):
            with self.subTest(signature=signature):
                result = cp.facts(self.path, self.runner(display="Identifier=sample\nTeamIdentifier=not set\n" + signature))
                self.assertEqual(result["kind"], "unverifiable")
                self.assertFalse(op.gate("claude", None, None, result, self.path)[0])
        self.assertEqual(cp.facts(self.path, self.runner())["kind"], "developer-id")

    def test_case02_integrity_failure_cannot_be_waived(self):
        result = cp.facts(self.path, self.runner(integrity=1))
        self.assertEqual(result["kind"], "invalid")
        self.assertFalse(op.gate("claude", {"operator_trust": cp.TRUST_ADHOC}, str(self.path), result, self.path)[0])
        from verification_status import origin_guidance
        self.assertIn(TEAM, origin_guidance("claude", "invalid", result)["message"])

    def test_case06_missing_timeout_unparseable_are_unverifiable(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError()
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("codesign", 15)
        for runner in (missing, timeout, self.runner(display=""), self.runner(display_rc=1),
                       self.runner(display="Identifier=a\nIdentifier=b\n")):
            with self.subTest(runner=runner):
                result = cp.facts(self.path, runner)
                self.assertEqual(result["kind"], "unverifiable")
                self.assertFalse(op.gate("claude", None, None, result, self.path)[0])

    def test_case07_team_input_is_validated_before_requirement(self):
        for team in ('TEST00000"', "TEST 00001", "$(touch x)", "short", "TEST000001\n", None):
            with self.subTest(team=team), self.assertRaises(ValueError):
                cp.verify_origin_requirement(self.path, team, self.runner())
        self.assertEqual(self.calls, [])

    def test_case25_script_does_not_borrow_interpreter_signature(self):
        self.path.write_bytes(b"#!/signed/interpreter\n")
        self.assertEqual(cp.facts(self.path, self.runner())["kind"], "script")
        self.assertEqual(self.calls, [])

    def test_adhoc_and_unsigned_are_distinct(self):
        result = cp.facts(self.path, self.runner(display="Identifier=sample\nSignature=adhoc\nTeamIdentifier=not set\n"))
        self.assertEqual(result["kind"], "adhoc")
        self.assertTrue(result["valid"])
        result = cp.facts(self.path, self.runner(integrity=1, display_rc=1,
                                                display="sample: code object is not signed at all\n"))
        self.assertEqual(result["kind"], "unsigned")
        self.assertFalse(result["valid"])

    def test_non_macos_native_does_not_invoke_codesign(self):
        self.path.write_bytes(b"\x7fELFtest")
        with patch.object(sys, "platform", "linux"):
            self.assertEqual(cp.facts(self.path, self.runner())["kind"], "no-platform-signature")
        self.assertEqual(self.calls, [])

    def test_missing_identifier_or_signature_is_not_trusted(self):
        for display in ("TeamIdentifier=TEST000001\n", "Identifier=x\n", "Identifier=x\nTeamIdentifier=short\n"):
            result = cp.facts(self.path, self.runner(display=display))
            self.assertIn(result["kind"], ("invalid", "unverifiable"))


if __name__ == "__main__":
    unittest.main()
