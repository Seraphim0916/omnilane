import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginFreezeExistingTests(unittest.TestCase):
    def test_existing_empty_and_nonempty_channels_are_not_migrated_or_saved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "policy.json"
            recorded = {"kind": "adhoc", "operator_trust": origin.provenance.TRUST_ADHOC}
            recorded_path = str(root / "old-tool")
            grant = origin.implicit_channels(recorded, recorded_path)
            self.assertEqual(len(grant), 1)
            for channels in ([], grant):
                with self.subTest(channels=channels):
                    data = dict(schema_version=1, host="fixture", vendors={
                        "claude": {"channels": copy.deepcopy(channels)},
                        "codex": {"channels": copy.deepcopy(grant)},
                    })
                    before = copy.deepcopy(data)
                    with patch.object(origin, "load", return_value=data) as loaded, \
                            patch.object(origin, "_save") as saved, \
                            patch.object(origin, "implicit_channels", wraps=origin.implicit_channels) as migrated:
                        origin.preserve_before_override("claude", recorded, recorded_path, path)
                    loaded.assert_called_once_with(path)
                    migrated.assert_not_called()
                    saved.assert_not_called()
                    self.assertEqual(data, before)

    def test_absent_vendor_freezes_supplied_old_grant_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "policy.json"
            recorded = {"kind": "adhoc", "operator_trust": origin.provenance.TRUST_ADHOC}
            recorded_path = str(root / "old-tool")
            recorded_before = copy.deepcopy(recorded)
            data = dict(schema_version=1, host="fixture", vendors={"codex": {"channels": []}})
            other_before = copy.deepcopy(data["vendors"]["codex"])
            with patch.object(origin, "load", return_value=data) as loaded, \
                    patch.object(origin, "_save") as saved, \
                    patch.object(origin, "implicit_channels", wraps=origin.implicit_channels) as migrated:
                origin.preserve_before_override("claude", recorded, recorded_path, path)
            loaded.assert_called_once_with(path)
            migrated.assert_called_once_with(recorded, recorded_path)
            saved.assert_called_once_with(path, data)
            channels = data["vendors"]["claude"]["channels"]
            self.assertEqual(len(channels), 1)
            self.assertEqual(channels[0]["kind"], "location")
            self.assertEqual(channels[0]["accepts"], "adhoc")
            self.assertEqual(channels[0]["install_family"], origin.install_family(recorded_path))
            self.assertEqual(channels[0]["approved_via"], "migrated-from-overlay")
            self.assertEqual(data["vendors"]["codex"], other_before)
            self.assertEqual(recorded, recorded_before)


if __name__ == "__main__":
    unittest.main()
