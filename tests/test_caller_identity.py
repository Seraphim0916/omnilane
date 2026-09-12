"""Caller identity read from the launching CLI. Offline: provider calls reach a spy."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import aa_policy  # noqa: E402
import caller_identity  # noqa: E402

REGISTRY_PATH = ROOT / "config/aa-model-policy.json"
SCRATCH = ROOT / ".sandbox-tmp"
CALLER_IDENTITY = ROOT / "scripts/lib/caller_identity.py"
DESKTOP_CLAUDE = ("/Users/x/Library/Application Support/Claude/claude-code/2.1.260/"
                  "claude.app/Contents/MacOS/claude")
SPY = '#!/usr/bin/env python3\n' + '''import json,os,sys
from pathlib import Path
p=os.environ.get('OMNILANE_AA_CALLER_CONTEXT')
with open(os.environ['AA_SPY'],'a') as f:
 f.write(json.dumps({'args':sys.argv[1:],'context':json.loads(Path(p).read_text()) if p else None})+'\\n')
if '-o' in sys.argv:
 Path(sys.argv[sys.argv.index('-o')+1]).write_text('SPY_OK\\n')
'''


def registry() -> dict:
    value, _ = caller_identity.load_registry(REGISTRY_PATH)
    return value


def under_launcher(launcher: list[str], command: str, env: dict, timeout: int = 60):
    """Run a shell command as the child of a process whose argv reads as a vendor CLI.

    argv[0] is what ps reports as the command, so a bash process started with
    argv[0]="claude" stands in for the CLI without any hook in the code under
    test. The trailing `; exit` stops bash from exec-ing the command, which
    would replace the stand-in and drop it from the ancestor chain.
    """
    script = f"{command}; status=$?; exit $status"
    return subprocess.run([launcher[0], "-c", script, *launcher[1:]], executable="/bin/bash",
                          env=env, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


class ReadSelectorTests(unittest.TestCase):
    def test_desktop_claude_path_containing_spaces(self):
        argv = [DESKTOP_CLAUDE, "--output-format", "stream-json", "--effort", "high",
                "--model", "claude-opus-5"]
        self.assertEqual(caller_identity.read_selector(argv), ("claude", "claude-opus-5", "high"))

    def test_equals_forms_and_the_version_binary(self):
        self.assertEqual(
            caller_identity.read_selector(["claude", "--model=claude-opus-5", "--effort=max"]),
            ("claude", "claude-opus-5", "max"))
        argv = ["/Users/x/.local/share/claude/versions/2.1.267", "--model", "claude-opus-5"]
        self.assertEqual(caller_identity.read_selector(argv), ("claude", "claude-opus-5", None))

    def test_codex_grok_and_agy_flags(self):
        self.assertEqual(caller_identity.read_selector(
            ["codex", "exec", "-m", "gpt-5.6-sol", "-c", 'model_reasoning_effort="high"', "task"]),
            ("codex", "gpt-5.6-sol", "high"))
        self.assertEqual(caller_identity.read_selector(
            ["/Users/x/.grok/downloads/grok-1.0.25-macos-aarch64", "-m", "grok-4.6",
             "--reasoning-effort", "low"]),
            ("grok", "grok-4.6", "low"))
        self.assertEqual(caller_identity.read_selector(["agy", "--model", "gemini-3.8-flash-low"]),
                         ("gemini", "gemini-3.8-flash-low", None))

    def test_processes_that_are_not_a_vendor_cli(self):
        for argv in (["/Applications/Claude.app/Contents/MacOS/Claude", "--proxy-server=x"],
                     ["/Applications/Claude.app/Contents/Helpers/disclaimer", "--", DESKTOP_CLAUDE],
                     ["/bin/zsh", "-c", "claude --model claude-opus-5"],
                     ["python3", "claude.py"], []):
            with self.subTest(argv=argv[:1]):
                self.assertIsNone(caller_identity.read_selector(argv))


class CodexConfigTests(unittest.TestCase):
    def test_exec_config_model_and_effort(self):
        self.assertEqual(caller_identity.read_selector([
            "codex", "exec", "-c", 'model="x"', "-c", 'model_reasoning_effort="high"']),
            ("codex", "x", "high"))

    def test_config_argument_forms(self):
        for args in (["--config", 'model="x"'], ['--config=model="x"'],
                     ['-cmodel="x"'], ['-c=model="x"'], ["-c", "model='x'"], ["-c", "model=x"]):
            with self.subTest(args=args):
                self.assertEqual(caller_identity.read_selector(["codex", "exec", *args])[1], "x")

    def test_model_flag_still_wins_over_config(self):
        self.assertEqual(caller_identity.read_selector([
            "codex", "exec", "-m", "flag-model", "-c", 'model="config-model"',
            "-c", 'model_reasoning_effort="high"']), ("codex", "flag-model", "high"))

    def test_config_uses_toml_string_decoding(self):
        self.assertEqual(caller_identity.read_selector([
            "codex", "exec", "--config=model=\"gpt-\\u0036-astra\""])[1], "gpt-6-astra")

    def test_non_string_model_config_refuses(self):
        with self.assertRaisesRegex(ValueError, "model.*string"):
            caller_identity.read_selector(["codex", "exec", "-c", "model=42"])


class RolloutCallerTests(unittest.TestCase):
    thread = "11111111-1111-4111-8111-111111111111"
    other = "22222222-2222-4222-8222-222222222222"

    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = self.home / "sessions/2026/09/11" / f"rollout-synthetic-{self.thread}.jsonl"
        self.path.parent.mkdir(parents=True)
        self.tree = {40: (30, ["python3"]), 30: (20, ["bash"]),
                     20: (1, ["codex", "app-server"])}
        self.environ = {"CODEX_THREAD_ID": self.thread}
        self.env_lookup = lambda pid: self.environ if pid == 30 else {}
        self.env_patch = patch.dict(os.environ, {"CODEX_THREAD_ID": self.thread,
            "CODEX_HOME": str(self.home), "OMNILANE_AA_CALLER_FROM_PROCESS": "1"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.records = [self.record("session_meta", id=self.thread), self.context("turn-1", "low"),
                        self.record("event_msg", type="task_complete"), self.context("turn-2", "high")]
        self.write_records()

    def record(self, kind, **payload):
        return {"type": kind, "payload": payload}

    def context(self, turn, effort):
        return self.record("turn_context", turn_id=turn, model="gpt-5.6-sol", effort=effort)

    def write_records(self):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in self.records))

    def read(self):
        return caller_identity.read_caller(40, self.tree.get, self.env_lookup)

    def test_appserver_uses_the_latest_turn(self):
        pid, selector, source = self.read()
        self.assertEqual((pid, selector), (20, ("codex", "gpt-5.6-sol", "high")))
        self.assertIn(self.thread, source)
        self.assertIn("turn-2", source)

    def test_null_effort_refuses(self):
        self.records[-1]["payload"]["effort"] = None
        self.write_records()
        with self.assertRaisesRegex(ValueError, "effort"):
            self.read()

    def test_missing_effort_refuses(self):
        del self.records[-1]["payload"]["effort"]
        self.write_records()
        with self.assertRaisesRegex(ValueError, "effort"):
            self.read()

    def test_thread_mismatch_refuses(self):
        self.environ["CODEX_THREAD_ID"] = self.other
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.read()

    def test_missing_current_thread_refuses(self):
        del os.environ["CODEX_THREAD_ID"]
        with self.assertRaisesRegex(ValueError, "current process.*CODEX_THREAD_ID"):
            self.read()

    def test_missing_child_thread_refuses(self):
        self.environ.clear()
        with self.assertRaisesRegex(ValueError, "direct child.*CODEX_THREAD_ID"):
            self.read()

    def test_malformed_thread_refuses(self):
        for where in (os.environ, self.environ):
            with self.subTest(where=where is os.environ):
                where["CODEX_THREAD_ID"] = "../not-a-uuid"
                with self.assertRaisesRegex(ValueError, "UUID"):
                    self.read()
                where["CODEX_THREAD_ID"] = self.thread

    def test_missing_rollout_refuses(self):
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, "found 0"):
            self.read()

    def test_archived_rollout_is_not_an_active_turn(self):
        archive = self.home / "archived_sessions"
        archive.mkdir()
        self.path.rename(archive / self.path.name)
        with self.assertRaisesRegex(ValueError, "found 0"):
            self.read()

    def resumed_rollout(self, turn, effort):
        """Resuming a thread opens rollout-<ts>-<thread>_<session>.jsonl beside the original."""
        resumed = self.path.parent / f"rollout-resumed-{self.thread}_{self.other}.jsonl"
        records = [self.record("session_meta", id=self.thread), self.context(turn, effort)]
        resumed.write_text("".join(json.dumps(r) + "\n" for r in records))
        return resumed

    def age(self, path, stamp):
        os.utime(path, (stamp, stamp))

    def test_resumed_rollout_is_read_instead_of_the_stale_original(self):
        resumed = self.resumed_rollout("turn-9", "xhigh")
        self.age(self.path, 1_000_000)
        self.age(resumed, 2_000_000)
        pid, selector, source = self.read()
        self.assertEqual(selector, ("codex", "gpt-5.6-sol", "xhigh"))
        self.assertIn("turn-9", source)

    def test_the_original_is_read_while_it_is_the_newest(self):
        resumed = self.resumed_rollout("turn-9", "xhigh")
        self.age(resumed, 1_000_000)
        self.age(self.path, 2_000_000)
        pid, selector, source = self.read()
        self.assertEqual(selector, ("codex", "gpt-5.6-sol", "high"))
        self.assertIn("turn-2", source)

    def test_a_resumed_rollout_alone_is_matched(self):
        resumed = self.resumed_rollout("turn-9", "xhigh")
        self.path.unlink()
        self.assertTrue(resumed.exists())
        pid, selector, source = self.read()
        self.assertEqual(selector, ("codex", "gpt-5.6-sol", "xhigh"))
        self.assertIn("turn-9", source)

    def test_session_meta_mismatch_refuses(self):
        self.records[0]["payload"]["id"] = self.other
        self.write_records()
        with self.assertRaisesRegex(ValueError, "session_meta"):
            self.read()

    def test_completed_turn_refuses(self):
        self.records.append(self.record("event_msg", type="task_complete"))
        self.write_records()
        with self.assertRaisesRegex(ValueError, "task_complete"):
            self.read()

    def test_turn_complete_alias_refuses(self):
        self.records.append(self.record("event_msg", type="turn_complete"))
        self.write_records()
        with self.assertRaisesRegex(ValueError, "turn_complete"):
            self.read()

    def test_aborted_turn_refuses(self):
        self.records.append(self.record("event_msg", type="turn_aborted"))
        self.write_records()
        with self.assertRaisesRegex(ValueError, "turn_aborted"):
            self.read()

    def test_another_turn_aborted_does_not_end_latest_context(self):
        self.records.append(self.record("event_msg", type="turn_aborted", turn_id="turn-1"))
        self.write_records()
        pid, selector, source = self.read()
        self.assertEqual((pid, selector), (20, ("codex", "gpt-5.6-sol", "high")))
        self.assertIn("turn-2", source)

    def test_same_turn_aborted_reports_both_turn_ids(self):
        self.records.append(self.record("event_msg", type="turn_aborted", turn_id="turn-2"))
        self.write_records()
        with self.assertRaises(ValueError) as caught:
            self.read()
        self.assertEqual(str(caught.exception), "latest turn_context turn turn-2 has already ended "
                         "(turn_aborted, turn turn-2)")

    def test_stale_rollout_reports_last_record_age_and_earlier_conversation(self):
        now = datetime(2026, 9, 12, 2, 40, 42, 833000, tzinfo=timezone.utc)
        self.records.append({**self.record("event_msg", type="turn_aborted", turn_id="turn-2"),
                             "timestamp": "2026-09-11T02:40:42.833Z"})
        # The last record, not the end event or filesystem mtime, supplies the age.
        self.records.append({"type": "response_item", "timestamp": "2026-09-11T03:40:42.833Z"})
        self.write_records()
        with self.assertRaises(ValueError) as caught:
            caller_identity.read_caller(40, self.tree.get, self.env_lookup, now=lambda: now)
        self.assertEqual(str(caught.exception),
                         "latest turn_context turn turn-2 has already ended "
                         "(turn_aborted, turn turn-2); that rollout's last record is "
                         "2026-09-11T03:40:42.833Z, 23h before now, so CODEX_THREAD_ID may name "
                         "an earlier conversation than the one running this command")

    def test_recent_rollout_reports_age_without_earlier_conversation_hint(self):
        now = datetime(2026, 9, 12, 3, 42, 42, 833000, tzinfo=timezone.utc)
        self.records.append({**self.record("event_msg", type="turn_aborted", turn_id="turn-2"),
                             "timestamp": "2026-09-12T03:40:42.833Z"})
        self.write_records()
        with self.assertRaises(ValueError) as caught:
            caller_identity.read_caller(40, self.tree.get, self.env_lookup, now=lambda: now)
        self.assertEqual(str(caught.exception),
                         "latest turn_context turn turn-2 has already ended "
                         "(turn_aborted, turn turn-2); that rollout's last record is "
                         "2026-09-12T03:40:42.833Z, 2m before now")
        self.assertNotIn("may name an earlier conversation", str(caught.exception))

    def test_last_record_without_parseable_timestamp_preserves_exact_refusal(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        self.records.append({**self.record("event_msg", type="turn_aborted", turn_id="turn-2"),
                             "timestamp": "2026-09-11T01:00:00Z"})
        records = self.records[:]
        for fields in ({}, {"timestamp": "invalid"}, {"timestamp": None}, {"timestamp": 42},
                       {"timestamp": "2026-09-11T01:00:00"}):
            with self.subTest(fields=fields):
                self.records = records + [{"type": "response_item", **fields}]
                self.write_records()
                with self.assertRaises(ValueError) as caught:
                    caller_identity.read_caller(40, self.tree.get, self.env_lookup, now=lambda: now)
                self.assertEqual(str(caught.exception),
                                 "latest turn_context turn turn-2 has already ended "
                                 "(turn_aborted, turn turn-2)")

    def test_stale_other_turn_end_still_returns_latest_identity(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        records = self.records[:]
        for event in ("turn_aborted", "task_complete", "turn_complete"):
            with self.subTest(event=event):
                self.records = records + [{**self.record("event_msg", type=event, turn_id="turn-1"),
                                          "timestamp": "2026-09-11T01:00:00Z"}]
                self.write_records()
                pid, selector, source = caller_identity.read_caller(
                    40, self.tree.get, self.env_lookup, now=lambda: now)
                self.assertEqual((pid, selector), (20, ("codex", "gpt-5.6-sol", "high")))
                self.assertEqual(source, f"thread {self.thread}, turn turn-2")

    def test_rollout_age_floors_units_and_uses_strict_ten_minute_threshold(self):
        timestamp = "2026-09-11T03:40:42.833+08:00"
        recorded = datetime.fromisoformat(timestamp)
        self.records.append({**self.record("event_msg", type="turn_aborted", turn_id="turn-2"),
                             "timestamp": timestamp})
        self.write_records()
        for seconds, age, hint in ((0, "0m", False), (299, "4m", False), (600, "10m", False),
                                   (600.001, "10m", True), (14399, "3h", True), (172800, "2d", True)):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError) as caught:
                    caller_identity.read_caller(40, self.tree.get, self.env_lookup,
                                                now=lambda: recorded + timedelta(seconds=seconds))
                self.assertIn(f"{timestamp}, {age} before now", str(caught.exception))
                self.assertEqual("may name an earlier conversation" in str(caught.exception), hint)

    def test_unusable_clock_or_future_record_preserves_exact_refusal(self):
        self.records.append({**self.record("event_msg", type="turn_aborted", turn_id="turn-2"),
                             "timestamp": "2026-09-12T03:40:42.833Z"})
        self.write_records()
        for now in (None, datetime(2026, 9, 12), datetime(2026, 9, 11, tzinfo=timezone.utc)):
            with self.subTest(now=now):
                with self.assertRaises(ValueError) as caught:
                    caller_identity.read_caller(40, self.tree.get, self.env_lookup, now=lambda: now)
                self.assertEqual(str(caught.exception),
                                 "latest turn_context turn turn-2 has already ended "
                                 "(turn_aborted, turn turn-2)")

    def test_end_event_without_readable_turn_id_refuses(self):
        records = self.records[:]
        for event in ("turn_aborted", "task_complete", "turn_complete"):
            for fields in ({}, {"turn_id": None}, {"turn_id": ""}, {"turn_id": " "},
                           {"turn_id": 0}, {"turn_id": []}, {"turn_id": {}}):
                with self.subTest(event=event, fields=fields):
                    self.records = [*records, self.record("event_msg", type=event, **fields)]
                    self.write_records()
                    with self.assertRaises(ValueError) as caught:
                        self.read()
                    self.assertEqual(str(caught.exception),
                                     f"latest turn_context turn turn-2 has already ended ({event}, turn unknown)")

    def test_completion_events_match_latest_turn_id(self):
        records = self.records[:]
        for event in ("task_complete", "turn_complete"):
            for turn in ("turn-1", "turn-2"):
                with self.subTest(event=event, turn=turn):
                    self.records = [*records, self.record("event_msg", type=event, turn_id=turn)]
                    self.write_records()
                    if turn == "turn-1":
                        self.assertEqual(self.read()[1], ("codex", "gpt-5.6-sol", "high"))
                        self.assertIn("turn-2", self.read()[2])
                    else:
                        with self.assertRaises(ValueError) as caught:
                            self.read()
                        self.assertEqual(str(caught.exception),
                                         f"latest turn_context turn turn-2 has already ended ({event}, turn turn-2)")

    def test_context_without_readable_turn_id_reports_unknown(self):
        records = self.records[:-1]
        for event in (None, "turn_aborted"):
            for fields in ({}, {"turn_id": None}, {"turn_id": ""}, {"turn_id": " "},
                           {"turn_id": 0}, {"turn_id": []}, {"turn_id": {}}):
                with self.subTest(event=event, fields=fields):
                    self.records = [*records, self.record("turn_context", model="gpt-5.6-sol",
                                                         effort="high", **fields)]
                    if event:
                        self.records.append(self.record("event_msg", type=event, turn_id="turn-1"))
                    self.write_records()
                    with self.assertRaises(ValueError) as caught:
                        self.read()
                    self.assertIn("turn unknown", str(caught.exception))
                    if event:
                        self.assertEqual(str(caught.exception),
                                         "latest turn_context turn unknown has already ended (turn_aborted, turn turn-1)")

    def test_unrelated_end_does_not_clear_a_matching_end(self):
        self.records.extend([self.record("event_msg", type="turn_aborted", turn_id="turn-2"),
                             self.record("event_msg", type="task_complete", turn_id="turn-1")])
        self.write_records()
        with self.assertRaisesRegex(ValueError, r"turn_aborted, turn turn-2"):
            self.read()

    def test_unrelated_end_does_not_clear_an_unknown_end(self):
        self.records.extend([self.record("event_msg", type="turn_aborted"),
                             self.record("event_msg", type="task_complete", turn_id="turn-1")])
        self.write_records()
        with self.assertRaisesRegex(ValueError, r"turn_aborted, turn unknown"):
            self.read()

    def test_new_task_started_without_context_still_refuses(self):
        self.records.extend([self.record("event_msg", type="task_complete"),
                             self.record("event_msg", type="task_started")])
        self.write_records()
        with self.assertRaisesRegex(ValueError, "task_complete"):
            self.read()

    def test_appserver_ignores_config_and_model_flags(self):
        for args in (["-c", 'model="wrong"'], ["-m", "wrong"], ["--config=model=42"]):
            with self.subTest(args=args):
                self.tree[20] = (1, ["codex", "app-server", *args])
                self.assertEqual(self.read()[1], ("codex", "gpt-5.6-sol", "high"))

    def test_appserver_after_global_options_uses_rollout(self):
        self.tree[20] = (1, ["codex", "-m", "wrong", "-p", "profile", "app-server"])
        self.assertEqual(self.read()[1], ("codex", "gpt-5.6-sol", "high"))

    def test_exec_with_profile_only_uses_rollout(self):
        self.tree[20] = (1, ["codex", "exec", "--profile", "profile"])
        self.assertEqual(self.read()[1], ("codex", "gpt-5.6-sol", "high"))

    def test_no_model_ignores_argv_effort_default(self):
        self.tree[20] = (1, ["codex", "exec", "-c", "model_reasoning_effort=42"])
        self.assertEqual(self.read()[1], ("codex", "gpt-5.6-sol", "high"))

    def test_explicit_exec_flags_do_not_read_rollout(self):
        self.tree[20] = (1, ["codex", "exec", "-m", "x", "-c", 'model_reasoning_effort="high"'])
        self.path.unlink()
        self.assertEqual(self.read()[1], ("codex", "x", "high"))

    def test_exec_prompt_appserver_is_not_a_subcommand(self):
        self.tree[20] = (1, ["codex", "exec", "-m", "x", "-c", 'model_reasoning_effort="high"', "app-server"])
        self.assertEqual(self.read()[1], ("codex", "x", "high"))

    def test_process_reading_switch_disables_both_routes(self):
        os.environ["OMNILANE_AA_CALLER_FROM_PROCESS"] = "0"
        for argv in (["codex", "app-server"], ["codex", "exec", "-m", "x"]):
            self.tree[20] = (1, argv)
            with self.assertRaisesRegex(ValueError, "disabled"):
                self.read()

    def test_missing_model_or_turn_id_refuses(self):
        for key in ("model", "turn_id"):
            with self.subTest(key=key):
                self.records[-1] = self.context("turn-2", "high")
                self.records[-1]["payload"][key] = " "
                self.write_records()
                with self.assertRaisesRegex(ValueError, key):
                    self.read()

    def test_missing_session_meta_or_turn_context_refuses(self):
        for kind in ("session_meta", "turn_context"):
            with self.subTest(kind=kind):
                self.records = [self.context("turn-2", "high")] if kind == "session_meta" else [self.record("session_meta", id=self.thread)]
                self.write_records()
                with self.assertRaisesRegex(ValueError, kind):
                    self.read()

    def test_partial_json_refuses_without_echoing_content(self):
        with self.path.open("a") as stream:
            stream.write('{"private-synthetic-canary":')
        with self.assertRaises(ValueError) as caught:
            self.read()
        self.assertNotIn("private-synthetic-canary", str(caught.exception))

    def test_rollout_is_streamed_not_read_whole(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("whole file")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("whole file")):
            self.assertEqual(self.read()[1][2], "high")

    def test_invalid_event_type_refuses_without_traceback(self):
        self.records.append(self.record("event_msg", type={"synthetic": "invalid"}))
        self.write_records()
        with self.assertRaisesRegex(ValueError, "invalid rollout event type"):
            self.read()

    def test_environment_read_failure_refuses(self):
        def denied(pid):
            raise PermissionError(1, "synthetic private detail")
        with self.assertRaisesRegex(ValueError, "errno 1") as caught:
            caller_identity.read_caller(40, self.tree.get, denied)
        self.assertNotIn("synthetic private detail", str(caught.exception))

    def test_default_codex_home(self):
        del os.environ["CODEX_HOME"]
        default = self.home / ".codex/sessions/2026/09/11" / self.path.name
        default.parent.mkdir(parents=True)
        self.path.rename(default)
        with patch.object(Path, "home", return_value=self.home):
            self.assertEqual(self.read()[1][2], "high")

    def test_whoami_reports_thread_and_turn(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        out, err = StringIO(), StringIO()
        with patch.object(caller_identity, "read_caller", return_value=self.read()), \
             patch.dict(os.environ, {"OMNILANE_HOME": str(self.home)}), \
             redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(caller_identity.main(["--registry", str(REGISTRY_PATH)]), 0)
        self.assertIn(self.thread, err.getvalue())
        self.assertIn("turn-2", err.getvalue())
        self.assertEqual(json.loads(Path(out.getvalue().strip()).read_text())["caller"]["effort"], "high")


class InitialEnvironmentTests(unittest.TestCase):
    def test_argv_decoy_is_not_an_environment_variable(self):
        argv = [b"zsh", b"-c", b"CODEX_THREAD_ID=decoy", b""]
        raw = (struct.pack("=i", len(argv)) + b"/bin/zsh\0\0" + b"\0".join(argv) + b"\0" +
               b"CODEX_THREAD_ID=11111111-1111-4111-8111-111111111111\0OTHER=ignored\0\0")
        parsed, env = caller_identity._parse_procargs2(raw)
        self.assertEqual(parsed, [arg.decode() for arg in argv])
        self.assertEqual(env, {"CODEX_THREAD_ID": RolloutCallerTests.thread})

    def test_truncated_procargs_refuses(self):
        with self.assertRaises(ValueError):
            caller_identity._parse_procargs2(struct.pack("=i", 2) + b"/bin/zsh\0zsh\0")

    def test_duplicate_environment_thread_ids_refuse(self):
        raw = struct.pack("=i", 1) + b"/bin/zsh\0zsh\0CODEX_THREAD_ID=a\0CODEX_THREAD_ID=b\0\0"
        with self.assertRaisesRegex(ValueError, "duplicate CODEX_THREAD_ID"):
            caller_identity._parse_procargs2(raw)


class ResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = registry()

    def resolve(self, *selector):
        return caller_identity.resolve(self.registry, *selector)

    def test_each_claude_effort_is_one_scored_row(self):
        for effort, config in (("max", "claude/claude-opus-5"), ("xhigh", "claude/claude-opus-5-xhigh"),
                               ("high", "claude/claude-opus-5-high"), ("low", "claude/claude-opus-5-low")):
            with self.subTest(effort=effort):
                row, reason = self.resolve("claude", "claude-opus-5", effort)
                self.assertEqual(row["id"], config, reason)

    def test_claude_is_never_mapped_onto_a_non_reasoning_row(self):
        # ADR-0046: Claude's --effort has no reasoning-off value, so the only
        # sonnet-5 row at high, the non-reasoning one, cannot be what ran.
        row, reason = self.resolve("claude", "claude-sonnet-5", "high")
        self.assertIsNone(row)
        self.assertIn("claude-sonnet-5", reason)

    def test_a_missing_effort_resolves_only_where_a_default_is_scored(self):
        row, reason = self.resolve("claude", "claude-opus-5", None)
        self.assertIsNone(row)
        self.assertIn("--effort", reason)
        row, _ = self.resolve("claude", "claude-haiku-4-5", None)
        self.assertEqual(row["id"], "claude/claude-4-5-haiku-reasoning")

    def test_codex_none_is_non_reasoning_and_an_absent_effort_refuses(self):
        row, _ = self.resolve("codex", "gpt-5.6-sol", "none")
        self.assertEqual(row["id"], "codex/gpt-5-6-sol-non-reasoning")
        row, reason = self.resolve("codex", "gpt-5.6-sol", None)
        self.assertIsNone(row)
        self.assertIn("effort", reason)

    def test_gemini_effort_is_decoded_from_the_model_id(self):
        row, _ = self.resolve("gemini", "gemini-3.8-flash-low", None)
        self.assertEqual(row["id"], "gemini/gemini-3-8-flash-low")

    def test_aliases_and_unscored_models_refuse(self):
        for selector in (("claude", "opus", "high"), ("claude", "claude-opus-9", "max")):
            with self.subTest(selector=selector):
                row, reason = self.resolve(*selector)
                self.assertIsNone(row)
                self.assertIn(selector[1], reason)


class FindLauncherTests(unittest.TestCase):
    def test_the_nearest_vendor_cli_is_the_caller(self):
        table = {
            40: (30, ["python3", "caller_identity.py"]),
            30: (20, ["bash", "dispatch.sh"]),
            20: (10, ["codex", "exec", "-m", "gpt-5.6-sol", "-c", 'model_reasoning_effort="high"']),
            10: (1, [DESKTOP_CLAUDE, "--model", "claude-opus-5", "--effort", "max"]),
        }
        self.assertEqual(caller_identity.find_launcher(40, table.get),
                         (20, ("codex", "gpt-5.6-sol", "high")))

    def test_chains_without_a_vendor_cli_end_without_a_caller(self):
        self.assertIsNone(caller_identity.find_launcher(3, {3: (2, ["bash"]), 2: (1, ["zsh"])}.get))
        self.assertIsNone(caller_identity.find_launcher(5, {5: (6, ["bash"]), 6: (5, ["bash"])}.get))
        self.assertIsNone(caller_identity.find_launcher(7, {}.get))

    def test_process_query_unavailable_returns_none(self):
        def unavailable(_pid):
            raise caller_identity._ProcessQueryUnavailable

        self.assertIsNone(caller_identity.find_launcher(40, unavailable))


class SandboxRefusalTests(unittest.TestCase):
    sandbox = {"CODEX_SANDBOX": "seatbelt", "OMNILANE_AA_CALLER_FROM_PROCESS": "1"}
    outside = {"OMNILANE_AA_CALLER_FROM_PROCESS": "1"}

    def assert_sandbox_refusal(self, error):
        message = str(error.exception)
        self.assertIn("Codex sandbox", message)
        self.assertIn("CODEX_SANDBOX=seatbelt", message)
        self.assertIn("outside the sandbox", message)
        self.assertNotIn("--caller-context FILE", message)

    def test_ps_permission_error_in_seatbelt_names_the_real_constraint(self):
        with patch.object(subprocess, "run", side_effect=PermissionError(1, "synthetic")):
            with self.assertRaises(ValueError) as error:
                caller_identity.read_caller(
                    40, caller_identity._process, current_environment=self.sandbox)
        self.assert_sandbox_refusal(error)

    def test_ps_nonzero_in_seatbelt_names_the_real_constraint(self):
        failed = subprocess.CompletedProcess(["ps"], 1, stdout="", stderr="synthetic")
        with patch.object(subprocess, "run", return_value=failed):
            with self.assertRaises(ValueError) as error:
                caller_identity.read_caller(
                    40, caller_identity._process, current_environment=self.sandbox)
        self.assert_sandbox_refusal(error)

    def test_ps_nonzero_with_parseable_output_still_reads_identity_outside_sandbox(self):
        head = subprocess.CompletedProcess(
            ["ps"], 1, stdout="20 claude\n", stderr="synthetic")
        args = subprocess.CompletedProcess(
            ["ps"], 1,
            stdout="claude --model claude-opus-5 --effort high\n",
            stderr="synthetic")
        with patch.object(subprocess, "run", side_effect=[head, args]):
            self.assertEqual(
                caller_identity.read_caller(
                    40, caller_identity._process, current_environment=self.outside),
                (40, ("claude", "claude-opus-5", "high"), ""))

    def test_no_vendor_ancestor_in_seatbelt_names_the_real_constraint(self):
        with self.assertRaises(ValueError) as error:
            caller_identity.read_caller(40, lambda _pid: None,
                                        current_environment=self.sandbox)
        self.assert_sandbox_refusal(error)

    def test_non_sandbox_failures_keep_the_previous_messages_exactly(self):
        def denied(_pid):
            raise PermissionError(1, "synthetic")

        with self.assertRaises(ValueError) as error:
            caller_identity.read_caller(40, denied, current_environment=self.outside)
        self.assertEqual(str(error.exception), "cannot query caller process (errno 1)")

        with self.assertRaises(ValueError) as error:
            caller_identity.read_caller(40, lambda _pid: None,
                                        current_environment=self.outside)
        self.assertEqual(
            str(error.exception),
            "no vendor CLI among this process's ancestors; a model caller passes "
            "--caller-context FILE and a human operator --operator-asserted-human")

    def test_seatbelt_marker_does_not_override_a_successful_query(self):
        tree = {40: (20, ["python3"]),
                20: (1, ["claude", "--model", "claude-opus-5", "--effort", "high"])}
        self.assertEqual(
            caller_identity.read_caller(40, tree.get, current_environment=self.sandbox),
            (20, ("claude", "claude-opus-5", "high"), ""))

    def test_main_returns_three_with_the_sandbox_refusal(self):
        from contextlib import redirect_stderr
        from io import StringIO

        error = StringIO()
        with redirect_stderr(error):
            result = caller_identity.main(
                ["--registry", str(REGISTRY_PATH)], environment=self.sandbox,
                lookup=lambda _pid: None)
        self.assertEqual(result, 3)
        self.assertEqual(
            error.getvalue(),
            "omnilane: cannot read the caller identity: "
            f"{caller_identity.CODEX_SANDBOX_REFUSAL}\n")
        self.assertNotIn("--caller-context FILE", error.getvalue())


class WriteContextTests(unittest.TestCase):
    def test_the_written_context_is_what_the_gate_accepts(self):
        reg = registry()
        row, _ = caller_identity.resolve(reg, "claude", "claude-opus-5", "high")
        SCRATCH.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path = caller_identity.write_context(row, reg, Path(tmp))
            value, _ = aa_policy.load_caller(path, reg)
            self.assertEqual(value["caller"], {key: row[key] for key in aa_policy.IDENTITY_FIELDS})
            self.assertEqual(value["inherited_ceiling"], 52)
            first = path.stat().st_mtime_ns
            self.assertEqual(caller_identity.write_context(row, reg, Path(tmp)), path)
            self.assertEqual(path.stat().st_mtime_ns, first)


class WhoamiCommandTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("OMNILANE_")}
        self.env["OMNILANE_HOME"] = self.tmp.name

    def run_whoami(self, *launcher, via_cli=False):
        if via_cli:
            command = f"bash {shlex.quote(str(ROOT / 'bin/omnilane'))} whoami"
        else:
            command = (f"python3 {shlex.quote(str(CALLER_IDENTITY))} "
                       f"--registry {shlex.quote(str(REGISTRY_PATH))}")
        return under_launcher(list(launcher), command, self.env)

    def test_prints_a_context_file_for_the_launching_cli(self):
        result = self.run_whoami("claude", "--model", "claude-opus-5", "--effort", "high")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(result.stdout.strip())
        self.assertEqual(path.parent, Path(self.tmp.name) / "caller-context")
        value = json.loads(path.read_text())
        self.assertEqual(value["caller"]["effort"], "high")
        self.assertEqual(value["inherited_ceiling"], 52)
        self.assertIn("claude/claude-opus-5-high", result.stderr)

    def test_the_omnilane_subcommand_reaches_the_same_answer(self):
        result = self.run_whoami("claude", "--model", "claude-opus-5", "--effort", "max", via_cli=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(Path(result.stdout.strip()).read_text())["inherited_ceiling"], 54)

    def test_refuses_rather_than_guessing(self):
        result = self.run_whoami("claude", "--model", "claude-opus-5")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn("--effort", result.stderr)


class DispatchReadsCallerTests(unittest.TestCase):
    """The lane's top candidate scores 54, the next 48: the ceiling decides which runs."""

    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.policy = base / "registry.json"
        self.policy.write_bytes(REGISTRY_PATH.read_bytes())
        self.registry = json.loads(REGISTRY_PATH.read_text())
        self.marker = base / "spy.jsonl"
        bins = base / "bin"
        bins.mkdir()
        spy = bins / "codex"
        spy.write_text(SPY)
        spy.chmod(0o755)
        mappings = [{"config_id": row["id"],
                     "identity": {key: row[key] for key in aa_policy.IDENTITY_FIELDS},
                     "runtime_model": row["model"], "runtime_effort": row["effort"],
                     "verification": "request-selector-contract"}
                    for row in self.registry["scored_configs"] if row["vendor"] == "codex"]
        overlay = base / "overlay.json"
        overlay.write_text(json.dumps({
            "schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"],
            "host": socket.gethostname(),
            "evidence": [{"path": str(spy), "sha256": hashlib.sha256(spy.read_bytes()).hexdigest()}],
            "mappings": mappings}))
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("OMNILANE_") and k not in ("CODEX_BIN", "CLAUDE_BIN")}
        self.env.update(OMNILANE_HOME=str(self.home), CODEX_BIN=str(spy), AA_SPY=str(self.marker),
                        PATH=str(bins) + os.pathsep + self.env["PATH"], OMNILANE_INBOX="0",
                        OMNILANE_AA_TRANSPORT_OVERLAY=str(overlay))
        (self.home / "routing.local.yaml").write_text(
            "aa-unit: codex gpt-6-astra xhigh | codex gpt-5.6-sol high\n")

    def dispatch(self, *extra, launcher):
        command = " ".join(shlex.quote(part) for part in (
            "bash", str(ROOT / "scripts/dispatch.sh"), "--aa-policy", str(self.policy),
            "--timeout", "10", *extra, "aa-unit", "Bounded offline test"))
        return under_launcher(launcher, command, self.env)

    def models_sent(self) -> list[str]:
        if not self.marker.exists():
            return []
        sent = []
        for line in self.marker.read_text().splitlines():
            args = json.loads(line)["args"]
            sent.append(args[args.index("-m") + 1] if "-m" in args else "")
        return sent

    def test_a_max_launcher_reaches_the_top_candidate(self):
        result = self.dispatch(launcher=["claude", "--model", "claude-opus-5", "--effort", "max"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.models_sent(), ["gpt-6-astra"])
        self.assertIn("claude/claude-opus-5", result.stderr)

    def test_a_high_launcher_is_held_to_its_own_ceiling(self):
        result = self.dispatch(launcher=["claude", "--model", "claude-opus-5", "--effort", "high"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.models_sent(), ["gpt-5.6-sol"])

    def test_an_explicit_context_outranks_the_launcher(self):
        row = next(r for r in self.registry["scored_configs"] if r["id"] == "codex/gpt-6-astra-medium")
        context = Path(self.tmp.name) / "caller.json"
        context.write_text(json.dumps({
            "schema_version": 1, "snapshot_id": self.registry["snapshot"]["id"], "kind": "model",
            "caller": {key: row[key] for key in aa_policy.IDENTITY_FIELDS}, "inherited_ceiling": 52}))
        self.env["OMNILANE_AA_CALLER_CONTEXT"] = str(context)
        result = self.dispatch(launcher=["claude", "--model", "claude-opus-5", "--effort", "max"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.models_sent(), ["gpt-5.6-sol"])

    def test_the_human_assertion_outranks_the_launcher(self):
        result = self.dispatch("--operator-asserted-human",
                               launcher=["claude", "--model", "claude-opus-5", "--effort", "high"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.models_sent(), ["gpt-6-astra"])

    def test_an_undecidable_launcher_is_refused_before_any_provider(self):
        result = self.dispatch(launcher=["claude", "--model", "claude-opus-5"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing-caller-context", result.stdout + result.stderr)
        self.assertIn("--effort", result.stderr)
        self.assertEqual(self.models_sent(), [])

    def test_with_reading_off_the_refusal_names_the_way_out(self):
        self.env["OMNILANE_AA_CALLER_FROM_PROCESS"] = "0"
        result = self.dispatch(launcher=["claude", "--model", "claude-opus-5", "--effort", "max"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing-caller-context", result.stdout + result.stderr)
        self.assertIn("omnilane whoami", result.stdout + result.stderr)
        self.assertEqual(self.models_sent(), [])


if __name__ == "__main__":
    unittest.main()
