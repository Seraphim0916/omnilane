"""In-process coverage for scripts/lib/normalize-claude-stream.py.

tests/test_normalize_claude_stream.py drives the same file as a subprocess, so
coverage never sees it. This loads the hyphenated module by path and calls
main() directly, aiming at the branches that suite does not pin.
"""
import offline_env  # Activate suite isolation for direct file execution.

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "normalize-claude-stream.py"
_spec = importlib.util.spec_from_file_location("normalize_claude_stream_cov3", SCRIPT)
normalize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(normalize)


def assistant(*blocks, **fields):
    return dict(type="assistant", message={"content": list(blocks)}, **fields)


def text(value):
    return {"type": "text", "text": value}


class NormalizeInProcessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cov3-normalize-")
        self.addCleanup(directory.cleanup)
        self.events = Path(directory.name) / "events.jsonl"
        self.output = Path(directory.name) / "out.txt"

    def write(self, raw):
        self.events.write_bytes(raw.encode("utf-8"))

    def write_events(self, *events):
        self.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))

    def run_main(self):
        with mock.patch("sys.argv", ["normalize-claude-stream.py", str(self.events), str(self.output)]):
            return normalize.main()

    def lines(self):
        return self.events.read_text(encoding="utf-8").splitlines()

    def test_empty_stream_fails_without_output_or_append(self):
        self.write("")
        self.assertEqual(self.run_main(), 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.events.read_bytes(), b"")

    def test_result_string_is_used_verbatim_and_stream_is_not_rewritten(self):
        self.write_events(assistant(text("draft")), {"type": "result", "result": "final\n\n\n"})
        before = self.events.read_bytes()
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "final\n")
        self.assertEqual(self.events.read_bytes(), before)

    def test_empty_result_string_still_counts_as_a_result(self):
        self.write_events(assistant(text("ignored")), {"type": "result", "result": ""})
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "\n")

    def test_result_wins_over_later_assistant_text(self):
        self.write_events({"type": "result", "result": "answer"}, assistant(text("afterthought")))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "answer\n")

    def test_error_result_fails_even_with_a_string_result(self):
        self.write_events(assistant(text("partial")),
                          {"type": "result", "result": "rate limited", "is_error": True})
        self.assertEqual(self.run_main(), 1)
        self.assertFalse(self.output.exists())

    def test_last_result_event_decides_error_state(self):
        self.write_events({"type": "result", "is_error": True},
                          {"type": "result", "result": "recovered", "is_error": False})
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "recovered\n")
        self.write_events({"type": "result", "result": "ok"},
                          {"type": "result", "is_error": True})
        self.assertEqual(self.run_main(), 1)

    def test_is_error_must_be_exactly_true(self):
        # Truthy non-bool values are not an error flag, and with no string result
        # the event is not a result at all.
        for flag in (1, "true", [1]):
            with self.subTest(flag=flag):
                self.write_events(assistant(text("body")), {"type": "result", "is_error": flag})
                self.assertEqual(self.run_main(), 0)
                self.assertEqual(self.output.read_text(encoding="utf-8"), "body\n")

    def test_non_string_result_without_error_is_ignored(self):
        self.write_events(assistant(text("fallback")),
                          {"type": "result", "result": {"text": "x"}},
                          {"type": "result", "result": None})
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "fallback\n")

    def test_subagent_and_malformed_assistant_events_are_skipped(self):
        self.write_events(
            assistant(text("subagent"), parent_tool_use_id="toolu_1"),
            {"type": "assistant", "message": "not a dict"},
            {"type": "assistant", "message": {"content": "not a list"}},
            {"type": "assistant"},
            assistant("bare string", {"type": "tool_use"}, {"type": "text", "text": 7}),
        )
        self.assertEqual(self.run_main(), 1)
        self.assertFalse(self.output.exists())

    def test_assistant_text_blocks_join_and_last_message_wins(self):
        self.write_events(assistant(text("first")),
                          assistant(text("a"), {"type": "thinking", "thinking": "hidden"}, text("b")),
                          assistant({"type": "tool_use", "name": "Read"}))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "a\nb\n")
        canonical = json.loads(self.lines()[-1])
        self.assertEqual(canonical, {"type": "result", "is_error": False,
                                     "result": "a\nb", "normalized_by": "omnilane"})

    def test_canonical_line_is_separated_from_an_unterminated_last_line(self):
        self.write(json.dumps(assistant(text("no newline"))))
        self.assertEqual(self.run_main(), 0)
        lines = self.lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["type"], "assistant")
        self.assertEqual(json.loads(lines[1])["result"], "no newline")

    def test_canonical_line_keeps_non_ascii_and_compact_separators(self):
        self.write_events(assistant(text("完成")))
        self.assertEqual(self.run_main(), 0)
        raw = self.events.read_bytes().splitlines()[-1]
        self.assertIn("完成".encode("utf-8"), raw)
        self.assertNotIn(b": ", raw)
        self.assertTrue(self.events.read_bytes().endswith(b"\n"))

    def test_malformed_lines_and_non_objects_do_not_stop_the_scan(self):
        self.write("{not json\n[1, 2]\n\"text\"\n\n" + json.dumps(assistant(text("kept"))) + "\n")
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "kept\n")

    def test_missing_arguments_exit_with_usage_error(self):
        with mock.patch("sys.argv", ["normalize-claude-stream.py"]), \
                mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as raised:
                normalize.main()
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
