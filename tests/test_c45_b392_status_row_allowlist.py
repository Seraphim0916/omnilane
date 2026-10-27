import offline_env  # Activate suite isolation for direct file execution.

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import aa_policy
import build_overlay
import verification_status as status


class RowAllowlistTests(unittest.TestCase):
    def test_mixed_sources_are_registry_filtered_deduplicated_and_sorted(self):
        registry = {"row-a": {}, "row-b": {}, "unused-row": {}}
        summary = {"vendors": {"codex": {
            "unprobed": ["row-b", "unknown", "row-a", "row-b"],
            "failed_environment": {"row-b": {}, "row-a": {}, "unknown-failure": {}}}}}
        original = copy.deepcopy(summary)
        store = {"schema_version": 1, "pending": {}}
        writes = []

        def save(path, value):
            writes.append((path, copy.deepcopy(value)))
            store.clear()
            store.update(copy.deepcopy(value))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            with patch.object(status, "load", side_effect=lambda: copy.deepcopy(store)), \
                    patch.object(status, "status_path", return_value=path), \
                    patch.object(build_overlay, "ROWS", registry), \
                    patch.object(aa_policy, "atomic_json", side_effect=save):
                status.record(30, vendors=["codex"], summary=summary)
                # Each source must contribute even without overlap with the other.
                status.record(30, vendors=["codex"], summary={"vendors": {"codex": {
                    "unprobed": ["row-b", "unknown", "row-b"],
                    "failed_environment": {"row-a": {}, "unknown-failure": {}}}}})
            self.assertEqual(len(writes), 2)
            self.assertEqual(writes[0][0], path)
            self.assertEqual(writes[1][0], path)
            for _, saved in writes:
                self.assertEqual(saved["pending"]["codex"]["config_ids"], ["row-a", "row-b"])
            self.assertFalse(path.exists())
        self.assertEqual(store["pending"]["codex"]["config_ids"], ["row-a", "row-b"])
        self.assertEqual(summary, original)
        self.assertEqual(registry, {"row-a": {}, "row-b": {}, "unused-row": {}})


if __name__ == "__main__":
    unittest.main()
