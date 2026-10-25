import offline_env  # Activate suite isolation for direct file execution.

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy
import verification_status as status


class StatusSchemaTests(unittest.TestCase):
    def test_only_integer_version_one_is_accepted_without_writes(self):
        for version in (1, True, False, 1.0, "1", 0, 2, None):
            with self.subTest(version=repr(version)):
                value = {"schema_version": version, "pending": {}}
                path = Mock()
                path.exists.return_value = True
                with patch.object(status, "status_path", return_value=path), \
                        patch.object(aa_policy, "_read_json", return_value=(value, None)) as reader, \
                        patch.object(aa_policy, "atomic_json") as writer:
                    if type(version) is int and version == 1:
                        self.assertIs(status.load(), value)
                    else:
                        with self.assertRaisesRegex(aa_policy.PolicyError,
                                                    "^invalid local verification status$"):
                            status.load()
                    reader.assert_called_once_with(path)
                    writer.assert_not_called()
                    self.assertEqual(value, {"schema_version": version, "pending": {}})


if __name__ == "__main__":
    unittest.main()
