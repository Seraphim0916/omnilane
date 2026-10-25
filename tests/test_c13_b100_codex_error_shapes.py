import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "c13_probe", Path(__file__).resolve().parents[1] / "scripts/lib/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class CodexErrorShapesTests(unittest.TestCase):
    def test_malformed_error_is_ignored_and_nonzero_exit_still_fails(self):
        for error in ("fixture", ["fixture"], 7, 1.5, True, None, [], "", 0):
            with self.subTest(error=error):
                stream = json.dumps({"type": "turn.failed", "error": error})
                self.assertEqual(probe.codex_failure(stream), "")
                self.assertEqual(
                    probe.verdict({"exit_code": 1}, stream + "\nEXPECTED_OK",
                                  "", "codex", "EXPECTED_OK"),
                    ("fail", "exit-code: 1", None, probe.TIER_SELECTOR))

    def test_last_legal_message_wins_across_invalid_events(self):
        events = [{"type": "error", "message": "retry"},
                  {"type": "turn.failed", "error": {"message": "final"}}]
        for error in ("fixture", [1], 42, {}, {"message": []}, {"message": ""}):
            events.append({"type": "turn.failed", "error": error})
        stream = "\n".join(map(json.dumps, events))
        self.assertEqual(probe.codex_failure(stream), "; codex-event: final")
        self.assertEqual(probe.verdict({"exit_code": 2}, stream, "", "codex", "OK"),
                         ("fail", "exit-code: 2; codex-event: final", None,
                          probe.TIER_SELECTOR))
        stream += "\n" + json.dumps({"type": "error", "message": "last"})
        self.assertEqual(probe.codex_failure(stream), "; codex-event: last")


if __name__ == "__main__":
    unittest.main()
