import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import transport_compat as compat


class CompatShortCircuitTests(unittest.TestCase):
    def setUp(self):
        self.old = {"id": "old"}
        self.new = {"id": "new"}
        self.source = {"engine": {"fixture": "current"},
                       "selectors": {"old": {"model": "fixture"}}}
        self.calls = Mock()
        for name, options in (
            ("engine_contract", {"return_value": {"fixture": "current"}}),
            ("row_contract", {"side_effect": ["same", "same"]}),
            ("selector_contract", {"return_value": {"model": "fixture"}}),
        ):
            patcher = patch.object(compat, name, **options)
            self.calls.attach_mock(patcher.start(), name)
            self.addCleanup(patcher.stop)

    def test_missing_source_does_not_check_engine(self):
        self.assertIs(compat.equivalent(self.old, self.new, None), False)
        self.assertEqual(self.calls.mock_calls, [])

    def test_stale_engine_does_not_parse_rows_or_selectors(self):
        self.source["engine"] = {"fixture": "stale"}
        self.assertIs(compat.equivalent(self.old, self.new, self.source), False)
        self.assertEqual(self.calls.mock_calls, [call.engine_contract()])

    def test_row_mismatch_does_not_check_selector(self):
        self.calls.row_contract.side_effect = ["old-contract", "new-contract"]
        self.assertIs(compat.equivalent(self.old, self.new, self.source), False)
        self.assertEqual(self.calls.mock_calls, [call.engine_contract(),
                         call.row_contract(self.old), call.row_contract(self.new)])

    def test_matching_contracts_check_every_stage_once(self):
        self.assertIs(compat.equivalent(self.old, self.new, self.source), True)
        self.assertEqual(self.calls.mock_calls, [call.engine_contract(),
                         call.row_contract(self.old), call.row_contract(self.new),
                         call.selector_contract(self.new)])


if __name__ == "__main__":
    unittest.main()
