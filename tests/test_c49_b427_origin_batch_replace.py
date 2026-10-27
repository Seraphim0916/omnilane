import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginBatchReplaceTests(unittest.TestCase):
    def test_last_update_replaces_only_matching_key_and_saves_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "policy.json"

            def channel(accepts, label, location=None):
                return dict(kind="location", accepts=accepts,
                            install_family=str(location or root / "tool"),
                            approved_at=label, approved_via="operator")

            for vendors in (("claude", "codex"), ("gemini", "grok")):
                with self.subTest(vendors=vendors):
                    target, other = vendors
                    old = channel("script", "old")
                    adhoc = channel("adhoc", "old")
                    elsewhere = channel("script", "elsewhere", root / "other-tool")
                    developer = dict(kind="developer-id", install_family=str(root / "tool"),
                                     team_id="TEST000001", identifier_family="vendor",
                                     approved_at="old", approved_via="operator")
                    data = dict(schema_version=1, host="fixture", vendors={
                        target: {"channels": [old, adhoc, elsewhere, developer]},
                        other: {"channels": [copy.deepcopy(old)]},
                    })
                    other_before = copy.deepcopy(data["vendors"][other])
                    first, last = channel("script", "first"), channel("script", "last")
                    changes = [(target, first, None, None), (target, last, None, None)]
                    changes_before = copy.deepcopy(changes)
                    with patch.object(origin, "load", return_value=data) as loaded, \
                            patch.object(origin, "_save") as saved, \
                            patch.object(origin, "implicit_channels") as migrated:
                        origin.record_many(changes, path=path)
                    loaded.assert_called_once_with(path)
                    saved.assert_called_once_with(path, data)
                    migrated.assert_not_called()
                    self.assertEqual(data["vendors"][target]["channels"], [adhoc, elsewhere, developer, last])
                    self.assertEqual(data["vendors"][other], other_before)
                    self.assertEqual(changes, changes_before)
                    at_location = [c for c in data["vendors"][target]["channels"]
                                   if c["kind"] == "location" and c["install_family"] == str(root / "tool")]
                    self.assertEqual([(c["accepts"], c["approved_at"]) for c in at_location],
                                     [("adhoc", "old"), ("script", "last")])


if __name__ == "__main__":
    unittest.main()
