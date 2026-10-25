"""Exercise the Claude stream contract through its offline command-line entry."""
import offline_env  # Activate suite isolation for direct file execution.
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lib" / "normalize-claude-stream.py"


def assistant(*blocks, **fields):
    return dict(type="assistant", message={"content": list(blocks)}, **fields)


def text(value):
    return {"type": "text", "text": value}


class NormalizeClaudeStreamTests(unittest.TestCase):
    def setUp(self):
        sandbox = ROOT / ".sandbox-tmp"
        sandbox.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="normalize-stream-", dir=sandbox)
        self.addCleanup(directory.cleanup)
        self.events = Path(directory.name) / "events.jsonl"
        self.output = Path(directory.name) / "out.txt"

    def run_stream(self, events, raw_prefix=""):
        self.events.write_text(raw_prefix + "".join(
            json.dumps(event, ensure_ascii=False) + "\n" for event in events
        ), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.events), str(self.output)],
            capture_output=True, text=True, timeout=10,
        )

    def test_malformed_json_and_non_objects_are_skipped(self):
        result = self.run_stream(
            [None, [], 17, "ignored", {"type": "result", "result": "usable"}],
            raw_prefix="{broken\n\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"usable\n")

    def test_error_result_fails_without_output_or_canonical_append(self):
        events = [assistant(text("fallback")),
                  {"type": "result", "result": "failed", "is_error": True}]
        result = self.run_stream(events)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual([json.loads(line) for line in self.events.read_text().splitlines()], events)

    def test_subagent_text_does_not_replace_top_level_text(self):
        result = self.run_stream([
            assistant(text("top level")),
            assistant(text("child"), parent_tool_use_id="tool-child"),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"top level\n")

    def test_subagent_text_alone_is_not_a_success(self):
        result = self.run_stream([assistant(text("child"), parent_tool_use_id="tool-child")])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.output.exists())

    def test_no_result_or_top_level_text_fails(self):
        for events in ([], [{"type": "system"}], [assistant()]):
            with self.subTest(events=events):
                result = self.run_stream(events)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(self.output.exists())

    def test_top_level_text_appends_exactly_one_canonical_result(self):
        event = assistant(text("完成\n\n"))
        result = self.run_stream([event])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), "完成\n".encode("utf-8"))
        lines = self.events.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), event)
        self.assertEqual(json.loads(lines[1]), {
            "type": "result", "is_error": False, "result": "完成\n\n",
            "normalized_by": "omnilane",
        })
        before = self.events.read_bytes()
        repeated = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.events), str(self.output)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(self.events.read_bytes(), before)

    def test_result_wins_over_assistant_text_without_appending(self):
        events = [assistant(text("before")), {"type": "result", "result": "authoritative"},
                  assistant(text("after"))]
        original = "".join(json.dumps(event) + "\n" for event in events).encode("utf-8")
        result = self.run_stream(events)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"authoritative\n")
        self.assertEqual(self.events.read_bytes(), original)

    def test_text_blocks_join_with_newlines_and_skip_non_text(self):
        result = self.run_stream([assistant(
            text("first"), {"type": "tool_use", "text": "ignored"}, None,
            text(42), text("second"), text("third"),
        )])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"first\nsecond\nthird\n")

    def test_result_has_exactly_one_trailing_newline(self):
        for value in ("answer", "answer\n", "answer\n\n", "", "\n\n"):
            with self.subTest(value=value):
                result = self.run_stream([{"type": "result", "result": value}])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.output.read_bytes(), (value.rstrip("\n") + "\n").encode())

    def test_last_result_controls_text_and_error_status(self):
        for is_error in (False, True):
            with self.subTest(is_error=is_error):
                result = self.run_stream([
                    {"type": "result", "result": "old", "is_error": not is_error},
                    {"type": "result", "result": "new", "is_error": is_error},
                ])
                self.assertEqual(result.returncode, int(is_error), result.stderr)
                if not is_error:
                    self.assertEqual(self.output.read_bytes(), b"new\n")
                    self.output.unlink()
                else:
                    self.assertFalse(self.output.exists())

    def test_last_top_level_message_wins(self):
        result = self.run_stream([assistant(text("old")), assistant(text("new"))])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"new\n")

    def test_invalid_assistant_shapes_are_ignored(self):
        result = self.run_stream([
            assistant(text("valid")), {"type": "assistant", "message": None},
            {"type": "assistant", "message": {"content": "not a list"}},
            assistant({"type": "text"}),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"valid\n")

    def test_error_result_without_text_fails(self):
        result = self.run_stream([
            assistant(text("valid")), {"type": "result", "result": None, "is_error": True},
        ])
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
