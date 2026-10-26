import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import caller_identity as ci  # noqa: E402

THREAD = "0199a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"
NO_SUCH_PID = 999999999


def row(model, effort, score=10, reasoning="reasoning", vendor="codex", suffix=""):
    return {"id": f"{vendor}/{model}/{effort}{suffix}", "vendor": vendor, "model": model,
            "effort": effort, "score": score, "reasoning": reasoning}


class ResolveTests(unittest.TestCase):
    def test_refusal_reasons_name_what_is_missing_or_ambiguous(self):
        registry = {"scored_configs": [row("m", "high"), row("m", "high", suffix="-dup"),
                                       row("c", None, vendor="claude", reasoning="non-reasoning"),
                                       row("g", "low", vendor="gemini")]}
        cases = [
            (("codex", None, "high"), "codex was launched without a model flag"),
            (("codex", "", "high"), "codex was launched without a model flag"),
            (("codex", "m", "high"), "codex m at effort high matches 2 scored configurations"),
            (("claude", "c", None), "the only scored c row at effort None is non-reasoning, which a "
                                    "Claude launch cannot select"),
            (("claude", "x", None), "x was launched without --effort and no default is scored for it"),
            (("codex", "m", "none"), "no scored configuration for codex m at effort None"),
        ]
        for selector, reason in cases:
            with self.subTest(selector=selector):
                self.assertEqual(ci.resolve(registry, *selector), (None, reason))
        self.assertEqual(ci.resolve(registry, "gemini", "g-low", None)[0]["id"], "gemini/g/low")

    def test_floor_row_is_lowest_score_then_id(self):
        registry = {"scored_configs": [row("m", "high", 30), row("m", "low", 20),
                                       row("m", "medium", 20)]}
        self.assertEqual(ci.floor_row(registry, "codex", "m")["id"], "codex/m/low")
        self.assertIsNone(ci.floor_row(registry, "codex", "absent"))


class ProcArgsTests(unittest.TestCase):
    def block(self, argc, *parts, padding=b"\0\0"):
        return struct.pack("=i", argc) + b"/exec/path" + padding + b"\0".join(parts)

    def test_valid_block_splits_argv_from_environment_by_argc(self):
        data = self.block(2, b"codex", b"CODEX_THREAD_ID=looks-like-argv", b"HOME=/h",
                          b"CODEX_THREAD_ID=" + THREAD.encode(), b"", b"apple=1")
        argv, environment = ci._parse_procargs2(data)
        self.assertEqual(argv, ["codex", "CODEX_THREAD_ID=looks-like-argv"])
        self.assertEqual(environment, {"CODEX_THREAD_ID": THREAD})

    def test_malformed_blocks_are_refused(self):
        cases = [
            (b"\1\0", "truncated KERN_PROCARGS2 header"),
            (struct.pack("=i", 0) + b"x\0a\0", "invalid KERN_PROCARGS2 header"),
            (struct.pack("=i", 1) + b"no-terminator", "invalid KERN_PROCARGS2 header"),
            (self.block(3, b"a", b"b"), "truncated KERN_PROCARGS2 argv"),
            (self.block(1, b"a", b"HOME=/h", b"PATH"), "truncated KERN_PROCARGS2 environment"),
            (self.block(1, b"a", b"CODEX_THREAD_ID=1", b"CODEX_THREAD_ID=2", b""),
             "duplicate CODEX_THREAD_ID in initial environment"),
        ]
        for data, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                ci._parse_procargs2(data)


class ProcessTableTests(unittest.TestCase):
    def ps(self, head, args):
        outputs = iter([head, args])
        return mock.patch.object(ci.subprocess, "run", side_effect=lambda *a, **k:
                                 subprocess.CompletedProcess(a[0], 0, next(outputs), ""))

    def test_unreadable_proc_entry_is_not_a_process(self):
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(13, "denied")):
            self.assertIsNone(ci._process(os.getpid()))

    def test_ps_fallback_keeps_comm_whole_and_splits_the_rest(self):
        comm = "/Applications/App Support/codex"
        with self.ps(f" 41 {comm}\n", f"{comm} -m gpt --config x=1\n"):
            self.assertEqual(ci._process(NO_SUCH_PID), (41, [comm, "-m", "gpt", "--config", "x=1"]))
        with self.ps("7 bash\n", "-bash -lc true\n"):
            self.assertEqual(ci._process(NO_SUCH_PID), (7, ["bash", "-lc", "true"]))

    def test_ps_fallback_without_a_parseable_row_is_not_a_process(self):
        for head in ("", "ppid-only\n", "x codex\n"):
            with self.subTest(head=head), self.ps(head, ""):
                self.assertIsNone(ci._process(NO_SUCH_PID))

    def test_ps_that_cannot_run_is_reported_as_query_unavailable(self):
        for error in (FileNotFoundError(2, "ps"), subprocess.TimeoutExpired("ps", 5)):
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(ci.subprocess, "run", side_effect=error), \
                    self.assertRaises(ci._ProcessQueryUnavailable):
                ci._process(NO_SUCH_PID)
            with mock.patch.object(ci.subprocess, "run", side_effect=error):
                self.assertIsNone(ci.find_launcher(NO_SUCH_PID))

    def test_launcher_walk_stops_on_cycles_dead_ends_and_depth(self):
        cycle = {10: (11, ["sh"]), 11: (10, ["bash"])}
        self.assertIsNone(ci.find_launcher(10, cycle.get))
        self.assertIsNone(ci.find_launcher(10, {10: (0, ["sh"])}.get))
        self.assertIsNone(ci.find_launcher(10, {}.get))
        deep = lambda pid: (pid + 1, ["sh"])  # noqa: E731
        self.assertIsNone(ci.find_launcher(1, deep))
        found = {pid: (pid + 1, ["sh"]) for pid in range(1, ci.MAX_DEPTH)}
        found[ci.MAX_DEPTH] = (1, ["codex", "-m", "m", "-c", "model_reasoning_effort=high"])
        self.assertEqual(ci.find_launcher(1, found.get), (ci.MAX_DEPTH, ("codex", "m", "high")))
        found[ci.MAX_DEPTH] = (ci.MAX_DEPTH + 1, ["sh"])
        found[ci.MAX_DEPTH + 1] = (1, ["codex", "-m", "m"])
        self.assertIsNone(ci.find_launcher(1, found.get))


class ReadCallerFailureTests(unittest.TestCase):
    def raising(self, error):
        def lookup(pid):
            raise error
        return lookup

    def test_unavailable_process_query_outside_and_inside_the_codex_sandbox(self):
        lookup = self.raising(ci._ProcessQueryUnavailable())
        with self.assertRaisesRegex(ValueError, "no vendor CLI among this process's ancestors"):
            ci.read_caller(5, lookup, current_environment={})
        with self.assertRaises(ValueError) as caught:
            ci.read_caller(5, lookup, current_environment={"CODEX_SANDBOX": "seatbelt"})
        self.assertEqual(str(caught.exception), ci.CODEX_SANDBOX_REFUSAL)

    def test_os_error_names_errno_or_the_sandbox(self):
        lookup = self.raising(PermissionError(1, "not permitted"))
        with self.assertRaisesRegex(ValueError, r"cannot query caller process \(errno 1\)"):
            ci.read_caller(5, lookup, current_environment={})
        with self.assertRaises(ValueError) as caught:
            ci.read_caller(5, lookup, current_environment={"CODEX_SANDBOX": "seatbelt"})
        self.assertEqual(str(caught.exception), ci.CODEX_SANDBOX_REFUSAL)

    def test_disabled_process_identity_refuses_before_any_lookup(self):
        with self.assertRaisesRegex(ValueError, "caller identity from process is disabled"):
            ci.read_caller(5, self.raising(AssertionError("looked up")),
                           current_environment={"OMNILANE_AA_CALLER_FROM_PROCESS": "0"})


class RolloutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="caller-cov-")
        self.addCleanup(temp.cleanup)
        self.codex_home = Path(temp.name) / "codex"
        self.day = self.codex_home / "sessions" / "2026" / "10" / "01"
        self.day.mkdir(parents=True)
        self.environment = {"CODEX_HOME": str(self.codex_home)}

    def write(self, *records, name=None):
        path = self.day / (name or f"rollout-2026-10-01T00-00-00-{THREAD}.jsonl")
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        return path

    def select(self):
        return ci._rollout_selector(THREAD, self.environment)

    def meta(self):
        return {"type": "session_meta", "payload": {"id": THREAD}}

    def turn(self, **payload):
        value = {"turn_id": "t1", "model": "gpt-x", "effort": "high"}
        value.update(payload)
        return {"type": "turn_context", "payload": value}

    def test_records_and_payloads_must_be_objects(self):
        for records, message in (([self.meta(), [1]], "invalid rollout record"),
                                 ([self.meta(), {"type": "turn_context", "payload": "x"}],
                                  "invalid rollout payload"),
                                 ([self.meta(), self.turn(), {"type": "event_msg", "payload": {"type": 3}}],
                                  "invalid rollout event type")):
            self.write(*records)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.select()

    def test_unreadable_candidates_and_files_report_errno_only(self):
        with mock.patch.object(ci, "_rollout_candidates", side_effect=PermissionError(13, "x")), \
                self.assertRaisesRegex(ValueError, r"\Acannot read rollout \(errno 13\)\Z"):
            self.select()
        (self.day / f"rollout-2026-10-01T00-00-00-{THREAD}.jsonl").mkdir()
        with self.assertRaisesRegex(ValueError, r"\Acannot read rollout \(errno 21\)\Z"):
            self.select()

    def test_the_newest_resumed_rollout_wins(self):
        old = self.write(self.meta(), self.turn(model="old-model"))
        os.utime(old, (1, 1))
        self.write(self.meta(), self.turn(model="new-model"),
                   name=f"rollout-2026-10-01T01-00-00-{THREAD}_0199ffff-0000-4000-8000-000000000000.jsonl")
        self.assertEqual(self.select(), (("codex", "new-model", "high"), f"thread {THREAD}, turn t1"))

    def test_missing_or_blank_turn_fields_are_refused(self):
        cases = [([self.meta(), self.turn(turn_id="")],
                  "latest turn_context turn_id must be a non-empty string (turn unknown)"),
                 ([self.meta(), self.turn(model=" ")], "latest turn_context model must be a non-empty string"),
                 ([self.meta(), self.turn(effort="")], "latest turn_context effort must be a non-empty string"),
                 ([self.meta(), self.meta(), self.turn()], "expected exactly one matching session_meta"),
                 ([{"type": "session_meta", "payload": {"id": "other"}}],
                  "session_meta id does not match CODEX_THREAD_ID")]
        for records, message in cases:
            self.write(*records)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, re.escape(message)):
                self.select()


class ClaudeTailTests(unittest.TestCase):
    def test_missing_effort_skips_records_without_identity_until_a_proven_one(self):
        temp = tempfile.TemporaryDirectory(prefix="claude-tail-cov-")
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "t.jsonl"
        records = [
            {"type": "assistant", "message": {"model": "claude-x-20261001"}, "effort": "high"},
            {"type": "assistant", "message": {}},
            {"type": "assistant", "message": {"model": "claude-x"}, "effort": "turbo"},
            {"type": "assistant", "message": {"model": "claude-x-20261001"}},
        ]
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        self.assertEqual(ci._claude_tail_selector(path),
                         (("claude", "claude-x", None), "latest assistant effort missing"))


class MainTests(unittest.TestCase):
    def test_unreadable_registry_exits_3_before_reading_the_caller(self):
        temp = tempfile.TemporaryDirectory(prefix="caller-main-cov-")
        self.addCleanup(temp.cleanup)
        stderr = io.StringIO()

        def lookup(pid):
            raise AssertionError("caller was read")

        with contextlib.redirect_stderr(stderr):
            code = ci.main(["--registry", str(Path(temp.name) / "absent.json")], {}, lookup)
        self.assertEqual(code, 3)
        self.assertTrue(stderr.getvalue().startswith("omnilane: cannot read the AA registry: "))
        self.assertEqual(list(Path(temp.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
