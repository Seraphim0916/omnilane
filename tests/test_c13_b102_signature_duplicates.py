import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "c13_provenance", Path(__file__).resolve().parents[1] / "scripts/lib/cli_provenance.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


class SignatureDuplicateTests(unittest.TestCase):
    def facts(self, lines):
        path = Path("/fixture/memory-binary")
        runner = Mock(side_effect=lambda argv, **kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr="\n".join(lines) if "-dv" in argv else ""))
        with patch("sys.platform", "darwin"), \
                patch.object(Path, "resolve", return_value=path) as resolve, \
                patch.object(Path, "open", return_value=BytesIO(b"\xcf\xfa\xed\xfe")) as opened:
            result = cp.facts(path, runner=runner)
        resolve.assert_called_once_with(strict=True)
        opened.assert_called_once_with("rb")
        return result, runner

    def test_duplicate_signature_and_team_never_verify_in_either_order(self):
        for key, first, second, other in (
            ("Signature", "adhoc", "adhoc", "TeamIdentifier=not set"),
            ("Signature", "adhoc", "unknown", "TeamIdentifier=not set"),
            ("TeamIdentifier", "TEST000001", "TEST000001", "Signature=adhoc"),
            ("TeamIdentifier", "TEST000001", "TEST000002", "Signature=adhoc"),
            ("TeamIdentifier", "TEST000001", "not set", "Signature=adhoc"),
        ):
            for left, right in ((first, second), (second, first)):
                for separated in (False, True):
                    with self.subTest(key=key, left=left, right=right, separated=separated):
                        fields = [f"{key}={left}", f"{key}={right}"]
                        if separated:
                            fields.insert(1, other)
                        else:
                            fields.append(other)
                        result, runner = self.facts(["Identifier=fixture", *fields])
                        self.assertEqual(result, dict(kind="unverifiable", signer=cp.UNSIGNED,
                                                      identifier=None, valid=False))
                        self.assertEqual(runner.call_count, 2)
                        self.assertFalse(any("-R" in call.args[0] for call in runner.call_args_list))

    def test_unique_fields_keep_adhoc_and_developer_id_results(self):
        for team, kind, signer, count in (("not set", "adhoc", cp.ADHOC, 2),
                                         ("TEST000001", "developer-id", "TEST000001", 3)):
            lines = ["Identifier=fixture", "Signature=adhoc", f"TeamIdentifier={team}"]
            for ordered in (lines, list(reversed(lines))):
                with self.subTest(team=team, ordered=ordered):
                    result, runner = self.facts(ordered)
                    self.assertEqual(result, dict(kind=kind, signer=signer,
                                                  identifier="fixture", valid=True))
                    self.assertEqual(runner.call_count, count)


if __name__ == "__main__":
    unittest.main()
