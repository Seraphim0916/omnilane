import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import origin_policy as origin


class OriginLockCleanupTests(unittest.TestCase):
    def test_cleanup_and_body_entry_at_each_stage(self):
        cases = (
            ("open", ["open"]),
            ("fstat", ["open", "fstat", "close:71"]),
            ("invalid-type", ["open", "fstat", "close:71"]),
            ("flock", ["open", "fstat", "flock", "close:71"]),
            ("body", ["open", "fstat", "flock", "body", "close:71"]),
            ("success", ["open", "fstat", "flock", "body", "close:71"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "policy.json"
            for stage, expected in cases:
                with self.subTest(stage=stage):
                    events = []
                    failure = OSError(stage + " failed")

                    def visit(name, result=None):
                        events.append(name)
                        if stage == name:
                            raise failure
                        return result

                    mode = stat.S_IFDIR if stage == "invalid-type" else stat.S_IFREG
                    with patch.object(origin.os, "open", side_effect=lambda *a: visit("open", 71)) as opened, \
                            patch.object(origin.os, "fstat", side_effect=lambda fd: visit("fstat", SimpleNamespace(st_mode=mode))) as inspected, \
                            patch.object(origin.fcntl, "flock", side_effect=lambda *a: visit("flock")) as locked, \
                            patch.object(origin.os, "close", side_effect=lambda fd: events.append("close:" + str(fd))) as closed:
                        if stage == "success":
                            with origin._lock(path):
                                visit("body")
                        else:
                            error_type = origin.OriginError if stage == "invalid-type" else OSError
                            with self.assertRaises(error_type) as caught:
                                with origin._lock(path):
                                    visit("body")
                            if stage == "invalid-type":
                                self.assertEqual(str(caught.exception), "invalid origin lock")
                            else:
                                self.assertIs(caught.exception, failure)
                    self.assertEqual(events, expected)
                    opened.assert_called_once_with(str(path) + ".lock", origin.os.O_CREAT | origin.os.O_RDWR | origin.os.O_NOFOLLOW, 0o600)
                    if stage == "open":
                        inspected.assert_not_called()
                        closed.assert_not_called()
                    else:
                        inspected.assert_called_once_with(71)
                        closed.assert_called_once_with(71)
                    if stage in ("open", "fstat", "invalid-type"):
                        locked.assert_not_called()
                    else:
                        locked.assert_called_once_with(71, origin.fcntl.LOCK_EX)


if __name__ == "__main__":
    unittest.main()
