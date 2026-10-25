import offline_env  # Activate suite isolation for direct file execution.

import builtins
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import transport_compat


class NonpassShortCircuitTests(unittest.TestCase):
    def test_explicit_nonpass_returns_original_without_work(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in ("probe", "probe_sweep", "probe_failure"):
                raise AssertionError("nonpass must not import a probe")
            return original_import(name, *args, **kwargs)

        for verdict in ("fail", "unknown", "", None, False):
            with self.subTest(verdict=verdict):
                descriptor = dict(verdict=verdict, verdict_reason="original-reason",
                                  observed_model="original-model", evidence_tier="original-tier",
                                  metadata={"nested": ["unchanged"]})
                before = copy.deepcopy(descriptor)
                with patch("builtins.__import__", side_effect=guarded_import) as imports, \
                     patch("builtins.open", side_effect=AssertionError("evidence read")) as opened, \
                     patch.object(Path, "open", side_effect=AssertionError("evidence read")) as path_open:
                    result = transport_compat.checked_descriptor(None, None, None, descriptor)
                self.assertIs(result, descriptor)
                self.assertEqual(descriptor, before)
                imports.assert_not_called()
                opened.assert_not_called()
                path_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
