import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "c22_b182_provenance", Path(__file__).resolve().parents[1] / "scripts/lib/cli_provenance.py")
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


class SignatureStreamsTests(unittest.TestCase):
    def inspect(self, stdout, stderr):
        target = Path("/fixture/memory-binary")
        runner = Mock(side_effect=lambda argv, **kwargs: SimpleNamespace(
            returncode=0, stdout=stdout if "-dv" in argv else "",
            stderr=stderr if "-dv" in argv else ""))
        with patch("sys.platform", "darwin"), \
                patch.object(Path, "resolve", return_value=target), \
                patch.object(Path, "open", return_value=BytesIO(b"\xcf\xfa\xed\xfe")):
            result = provenance.facts(target, runner=runner)
        self.assertEqual(runner.call_args_list[0].args[0],
                         ["codesign", "--verify", "--strict", str(target)])
        self.assertEqual(runner.call_args_list[1].args[0],
                         ["codesign", "-dv", "--verbose=2", str(target)])
        return result, runner

    def test_stdout_only_cannot_supply_identity(self):
        result, runner = self.inspect(
            "Identifier=fixture\nTeamIdentifier=TEST000001\nSignature=adhoc\n", "")
        self.assertEqual(result, dict(kind="unverifiable", signer=provenance.UNSIGNED,
                                      identifier=None, valid=False))
        self.assertEqual(runner.call_count, 2)

    def test_stderr_identity_ignores_conflicting_and_duplicate_stdout(self):
        for team, kind, signer, count in (
            ("TEST000001", "developer-id", "TEST000001", 3),
            ("not set", "adhoc", provenance.ADHOC, 2),
        ):
            for stdout in (
                "Identifier=other\nTeamIdentifier=OTHER00001\nSignature=conflict\n",
                "Identifier=other\nIdentifier=other\nTeamIdentifier=OTHER00001\n"
                "TeamIdentifier=OTHER00001\nSignature=conflict\nSignature=conflict\n",
            ):
                with self.subTest(team=team, stdout=stdout):
                    result, runner = self.inspect(stdout,
                        "Identifier=fixture\nTeamIdentifier=" + team + "\nSignature=adhoc\n")
                    self.assertEqual(result, dict(kind=kind, signer=signer,
                                                  identifier="fixture", valid=True))
                    self.assertEqual(runner.call_count, count)
                    if count == 3:
                        argv = runner.call_args_list[2].args[0]
                        self.assertEqual(argv[:4], ["codesign", "--verify", "--strict", "-R"])
                        self.assertIn('subject.OU] = "TEST000001"', argv[4])
                        self.assertNotIn("OTHER00001", argv[4])


if __name__ == "__main__":
    unittest.main()
