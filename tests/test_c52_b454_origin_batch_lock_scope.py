import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as op


class BatchLockScopeTests(unittest.TestCase):
    def test_lock_covers_load_and_save_and_releases_on_save_failure(self):
        for fail_save in (False, True):
            with self.subTest(fail_save=fail_save):
                events = []
                held = False
                path = Path("/fixture/policy.json")
                first = dict(kind="location", accepts="script", install_family="/fixture/a")
                second = dict(kind="location", accepts="adhoc", install_family="/fixture/b")
                data = {"vendors": {"claude": {"channels": []}}}
                failure = OSError("injected save failure")

                @contextlib.contextmanager
                def lock(actual_path):
                    nonlocal held
                    self.assertEqual(actual_path, path)
                    self.assertFalse(held)
                    held = True
                    events.append("lock")
                    try:
                        yield
                    finally:
                        events.append("unlock")
                        held = False

                def load(actual_path):
                    self.assertTrue(held, "load must occur while locked")
                    self.assertEqual(actual_path, path)
                    events.append("load")
                    return data

                def save(actual_path, actual_data):
                    self.assertTrue(held, "save must occur while locked")
                    self.assertEqual(actual_path, path)
                    self.assertIs(actual_data, data)
                    self.assertEqual(actual_data["vendors"]["claude"]["channels"],
                                     [first, second])
                    events.append("save")
                    if fail_save:
                        raise failure

                changes = [("claude", first, None, None), ("claude", second, None, None)]
                with patch.object(op, "_lock", side_effect=lock) as lock_mock, \
                        patch.object(op, "load", side_effect=load) as load_mock, \
                        patch.object(op, "_save", side_effect=save) as save_mock:
                    if fail_save:
                        with self.assertRaises(OSError) as caught:
                            op.record_many(changes, path=path)
                        self.assertIs(caught.exception, failure)
                    else:
                        op.record_many(changes, path=path)
                    lock_mock.assert_called_once_with(path)
                    load_mock.assert_called_once_with(path)
                    save_mock.assert_called_once_with(path, data)
                self.assertEqual(events, ["lock", "load", "save", "unlock"])
                self.assertFalse(held)


if __name__ == "__main__":
    unittest.main()
