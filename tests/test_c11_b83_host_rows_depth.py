import offline_env  # Activate suite isolation for direct file execution.

import unittest

import test_native_context_host_rows as fixtures

native_context = fixtures.native_context


class HostRowsDepthTests(unittest.TestCase):
    setUp = fixtures.HostRowsTests.setUp
    write_rows = fixtures.HostRowsTests.write_rows
    run_main = fixtures.HostRowsTests.run_main

    def test_deep_json_uses_host_rows_error(self):
        path = self.write_rows("[" * 10000 + "0" + "]" * 10000)
        with self.assertRaisesRegex(native_context.HostRowsError, "^cannot read: "):
            native_context.load_host_rows(path)

    def test_command_rejects_depth_without_partial_output(self):
        path = self.write_rows("[" * 10000 + "0" + "]" * 10000)
        code, stdout, stderr = self.run_main("--host-rows", str(path))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("invalid host rows file", stderr)
        self.assertIn("cannot read:", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertFalse(self.out.parent.exists())

    def test_normal_rows_are_preserved(self):
        path = self.write_rows({"schema_version": 1, "harnesses": {
            "claude-code": [{"model": "claude-sonnet-5", "efforts": ["high"]}]}})
        self.assertEqual(native_context.load_host_rows(path),
                         {"claude-code": [("claude-sonnet-5", ["high"])]})
        code, stdout, stderr = self.run_main("--host-rows", str(path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.strip(), str(self.out))
        self.assertTrue(self.out.is_file())

    def test_duplicate_keys_still_rejected(self):
        path = self.write_rows('{"schema_version":1,"harnesses":{},"harnesses":{}}')
        with self.assertRaisesRegex(native_context.HostRowsError, "duplicate key"):
            native_context.load_host_rows(path)


if __name__ == "__main__":
    unittest.main()
