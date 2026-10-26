import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


class NativeFlagsTests(unittest.TestCase):
    FLAGS = ("inherits_caller_runtime", "caller_identity_verified",
             "preserve_existing_context")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workdir = directory.name

    def context(self, flag, value):
        return {
            "schema_version": 1, "harness": "codex", "vendor": "codex",
            flag: value,
            "requirements": {"tools": [], "isolation": "shared-inherited",
                             "lifecycle": "single-shot"},
            "capabilities": [{"model": "fixture-model", "efforts": ["high"],
                              "modes": ["work"], "workdirs": [self.workdir],
                              "tools": [], "isolations": ["shared-inherited"],
                              "lifecycles": ["single-shot"]}],
        }

    def test_actual_booleans_reach_workdir_validation(self):
        for flag in self.FLAGS:
            for value in (True, False):
                with self.subTest(flag=flag, value=value):
                    with mock.patch.object(native, "canonical_dir",
                                           wraps=native.canonical_dir) as validate_dir:
                        result = native.capability_context(None, self.context(flag, value))
                    self.assertIs(result[flag], value)
                    validate_dir.assert_called_once_with(self.workdir)
                    self.assertEqual(result["capabilities"][0]["workdirs"],
                                     [str(Path(self.workdir).resolve())])

    def test_non_booleans_rejected_before_workdir_validation(self):
        for flag in self.FLAGS:
            for value in (0, 1, "true", None):
                with self.subTest(flag=flag, value=value):
                    with mock.patch.object(native, "canonical_dir") as validate_dir:
                        with self.assertRaisesRegex(ValueError, "^invalid " + flag + "$"):
                            native.capability_context(None, self.context(flag, value))
                        validate_dir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
