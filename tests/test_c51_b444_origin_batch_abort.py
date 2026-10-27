import offline_env  # Activate suite isolation for direct file execution.

import copy
from contextlib import nullcontext
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginBatchAbortTests(unittest.TestCase):
    def test_later_invalid_vendor_aborts_without_saving(self):
        self.run_batch("unknown-vendor", rejected=True)

    def test_valid_batch_saves_both_updates_once(self):
        self.run_batch("codex", rejected=False)

    def run_batch(self, second_vendor, rejected):
        persisted = dict(schema_version=1, host=socket.gethostname(), vendors={})
        initial = copy.deepcopy(persisted)
        loaded = copy.deepcopy(persisted)

        def save(path, data):
            origin.validate(data)
            persisted.clear()
            persisted.update(copy.deepcopy(data))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            channels = [dict(kind="location", accepts="adhoc",
                             install_family=str(Path(directory) / vendor),
                             approved_at="2026-01-01T00:00:00+00:00",
                             approved_via="operator") for vendor in ("claude", "codex")]
            changes = [("claude", channels[0], None, None),
                       (second_vendor, channels[1], None, None)]
            with patch.object(origin, "_lock", return_value=nullcontext()), \
                    patch.object(origin, "load", return_value=loaded), \
                    patch.object(origin, "_save", side_effect=save) as saving:
                if rejected:
                    with self.assertRaisesRegex(origin.OriginError, "^unknown vendor$"):
                        origin.record_many(changes, path)
                    saving.assert_not_called()
                    self.assertEqual(persisted, initial)
                    self.assertEqual(loaded["vendors"], {"claude": {"channels": [channels[0]]}})
                    print("OriginError unknown vendor saves 0")
                else:
                    origin.record_many(changes, path)
                    saving.assert_called_once_with(path, loaded)
                    self.assertEqual(persisted["vendors"], {
                        "claude": {"channels": [channels[0]]},
                        "codex": {"channels": [channels[1]]},
                    })
                    print("valid batch saves 1 vendors 2")


if __name__ == "__main__":
    unittest.main()
