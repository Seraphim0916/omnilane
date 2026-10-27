import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "native_b317", Path(__file__).resolve().parents[1] / "scripts/lib/native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeQuotaPublicationTests(unittest.TestCase):
    def test_quota_write_failure_prevents_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = {"task": "fixture task", "quota_preflight": {"allowed": True}}
            real_write = native.write_new
            error = OSError("quota-write-denied")
            events = []

            def write(path, content):
                events.append(path.name)
                if path.name == "quota-preflight.json":
                    raise error
                return real_write(path, content)

            with patch.object(native, "write_new", side_effect=write) as writes, \
                    patch.object(native.os, "open", wraps=native.os.open) as opens, \
                    patch.object(native.os, "rename", wraps=native.os.rename) as rename, \
                    patch.object(native, "cleanup_staging_dir", wraps=native.cleanup_staging_dir) as cleanup:
                with self.assertRaises(OSError) as caught:
                    native.publish_job(root, "fixture-job", plan, {})
                self.assertIs(caught.exception, error)
                self.assertEqual(events, ["native.lock", "task.txt", "meta.json",
                                          "native.json", "quota-preflight.json"])
                self.assertEqual(writes.call_count, 5)
                publication_opens = [c for c in opens.call_args_list
                                     if Path(c.args[0]).name == ".native-publish.lock"]
                self.assertEqual(publication_opens, [])
                rename.assert_not_called()
                cleanup.assert_called_once()
                self.assertFalse(cleanup.call_args.args[0].exists())
            self.assertEqual(list(root.iterdir()), [])
            print("quota-write-denied writes 5 open 0 rename 0 cleanup 1")

    def test_success_with_and_without_quota_attachment(self):
        for quota in (False, True):
            with self.subTest(quota=quota), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plan = {"task": "fixture task"}
                if quota:
                    plan["quota_preflight"] = {"allowed": True}
                with patch.object(native, "write_new", wraps=native.write_new) as writes, \
                        patch.object(native.os, "rename", wraps=native.os.rename) as rename, \
                        patch.object(native, "cleanup_staging_dir", wraps=native.cleanup_staging_dir) as cleanup:
                    native.publish_job(root, "fixture-job", plan, {"fixture": True})
                    self.assertEqual(writes.call_count, 5 if quota else 4)
                    rename.assert_called_once()
                    cleanup.assert_called_once()
                job = root / "fixture-job"
                expected = {"native.lock", "task.txt", "meta.json", "native.json"}
                if quota:
                    expected.add("quota-preflight.json")
                    self.assertEqual(json.loads((job / "quota-preflight.json").read_text()),
                                     plan["quota_preflight"])
                self.assertEqual({p.name for p in job.iterdir()}, expected)
                self.assertEqual(json.loads((job / "native.json").read_text()), plan)
                self.assertEqual({p.name for p in root.iterdir()},
                                 {"fixture-job", ".native-publish.lock"})


if __name__ == "__main__":
    unittest.main()
