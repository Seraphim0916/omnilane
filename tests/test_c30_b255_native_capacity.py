import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import native


def context_with_rows(count, workdir):
    return {
        "schema_version": 1, "harness": "fixture", "vendor": "codex",
        "requirements": {"tools": [], "isolation": "shared-inherited",
                         "lifecycle": "single-shot"},
        "capabilities": [
            {"model": "fixture-{}".format(index), "efforts": ["high"],
             "modes": ["work"], "workdirs": [workdir], "tools": [],
             "isolations": ["shared-inherited"], "lifecycles": ["single-shot"]}
            for index in range(count)],
    }


class NativeCapacityTests(unittest.TestCase):
    def test_exact_capacity_boundaries_and_path_validation_count(self):
        with tempfile.TemporaryDirectory() as workdir:
            for count in (0, 1, 128, 129):
                with self.subTest(count=count):
                    value = context_with_rows(count, workdir)
                    with mock.patch.object(native, "canonical_dir", wraps=native.canonical_dir) as resolve:
                        if count in (0, 129):
                            with self.assertRaisesRegex(ValueError, "^invalid capabilities$"):
                                native.capability_context(None, value=value)
                            resolve.assert_not_called()
                        else:
                            result = native.capability_context(None, value=value)
                            self.assertEqual(len(result["capabilities"]), count)
                            self.assertEqual(resolve.call_args_list, [mock.call(workdir)] * count)
                            self.assertEqual(
                                [row["workdirs"] for row in result["capabilities"]],
                                [[str(Path(workdir).resolve())]] * count)

    def test_last_row_at_limit_is_still_validated(self):
        with tempfile.TemporaryDirectory() as workdir:
            value = context_with_rows(128, workdir)
            missing = str(Path(workdir) / "missing")
            value["capabilities"][-1]["workdirs"] = [missing]
            with mock.patch.object(native, "canonical_dir", wraps=native.canonical_dir) as resolve:
                with self.assertRaisesRegex(ValueError, "^workdir must be an existing absolute directory$"):
                    native.capability_context(None, value=value)
                self.assertEqual(resolve.call_count, 128)
                self.assertEqual(resolve.call_args, mock.call(missing))


if __name__ == "__main__":
    unittest.main()
