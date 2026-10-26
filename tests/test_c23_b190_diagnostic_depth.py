import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import overlay_diagnostics as diagnostics


class DiagnosticDepthTests(unittest.TestCase):
    def deep_path(self):
        path = Mock(spec=Path)
        path.read_text.return_value = '{"a":' * 10000 + '{}' + '}' * 10000
        with self.assertRaises(RecursionError):
            json.loads(path.read_text.return_value)
        return path

    def test_deep_json_returns_empty_object(self):
        path = self.deep_path()
        self.assertEqual(diagnostics.read_object(path), {})
        path.read_text.assert_called_once_with()

    def test_deep_json_preserves_recovery_guidance(self):
        result = diagnostics.recovery_guidance(ROOT, self.deep_path())
        self.assertIn("manifest paths: not recorded", result)
        self.assertIn("omnilane doctor", result)
        self.assertIn(str(ROOT / "scripts/lib/probe_sweep.py"), result)
        self.assertIn(str(ROOT / "scripts/lib/build_overlay.py"), result)
        self.assertIn("the current overlay is not replaced by these commands", result)

    def test_existing_json_contracts(self):
        value = {"evidence": [{"path": "/example/probe-manifest.json"}]}
        for text, expected in ((json.dumps(value), value), ("{broken", {}), ("[]", {})):
            with self.subTest(text=text):
                path = Mock(spec=Path)
                path.read_text.return_value = text
                self.assertEqual(diagnostics.read_object(path), expected)
                result = diagnostics.recovery_guidance(ROOT, path)
                manifest = "/example/probe-manifest.json" if expected else "not recorded"
                self.assertIn("manifest paths: " + manifest, result)

    def test_read_errors_preserve_empty_fallback_and_guidance(self):
        for error in (PermissionError("denied"), FileNotFoundError("missing"), OSError("read")):
            with self.subTest(error=type(error).__name__):
                path = Mock(spec=Path)
                path.read_text.side_effect = error
                self.assertEqual(diagnostics.read_object(path), {})
                self.assertIn("omnilane doctor", diagnostics.recovery_guidance(ROOT, path))

    def test_unexpected_read_errors_propagate(self):
        for error_type in (RecursionError, RuntimeError, TypeError, AttributeError, MemoryError):
            with self.subTest(error=error_type.__name__):
                path = Mock(spec=Path)
                error = error_type("unexpected read failure")
                path.read_text.side_effect = error
                with self.assertRaises(error_type) as caught:
                    diagnostics.read_object(path)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
