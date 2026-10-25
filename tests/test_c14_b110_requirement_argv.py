import offline_env  # Activate suite isolation for direct file execution.
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
from cli_provenance import verify_origin_requirement


class RequirementArgvTests(unittest.TestCase):
    def test_special_paths_are_single_literal_arguments(self):
        requirement = (
            '=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] '
            'exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
            'and certificate leaf[subject.OU] = "TEST000001"'
        )
        with tempfile.TemporaryDirectory() as directory:
            for name in ("space name", "single'quote", 'double"quote',
                         "semi;colon", "$(printf injected)", "`printf injected`",
                         'all spaces\'";$(printf injected)`printf injected`'):
                for use_path_object in (False, True):
                    with self.subTest(name=name, path_object=use_path_object):
                        path = Path(directory) / name
                        argument = path if use_path_object else str(path)
                        result = object()
                        runner = Mock(return_value=result)
                        self.assertIs(verify_origin_requirement(argument, "TEST000001", runner), result)
                        runner.assert_called_once_with(
                            ["codesign", "--verify", "--strict", "-R", requirement, str(path)],
                            capture_output=True, text=True, timeout=15,
                        )
                        self.assertNotIn("shell", runner.call_args.kwargs)
        print("path retained True requirement literal True shell absent True")


if __name__ == "__main__":
    unittest.main()
