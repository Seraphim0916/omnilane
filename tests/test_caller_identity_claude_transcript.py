"""Offline Claude current-turn identity; no real session data is read."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import caller_identity  # noqa: E402


class ClaudeTranscriptTests(unittest.TestCase):
    SID = "11111111-1111-4111-8111-111111111111"
    START = "Tue Sep 29 19:22:47 2026"
    ARGV = ("claude", "claude-opus-5-5", "xhigh")
    MARKER = "PRIVATE_MESSAGE_CONTENT_MUST_NEVER_ESCAPE_1A"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.session = self.home / "sessions/20.json"
        self.transcript = self.home / "projects/encoded-cwd" / f"{self.SID}.jsonl"
        self.session.parent.mkdir(parents=True)
        self.transcript.parent.mkdir(parents=True)
        self.tree = {40: (30, ["python3"]), 30: (20, ["bash"]),
                     20: (10, ["claude", "--model", self.ARGV[1], "--effort", "xhigh"]),
                     10: (1, ["claude", "--model", "outer-model", "--effort", "low"])}
        self.env = {"CLAUDE_CODE_SESSION_ID": self.SID,
                    "CLAUDE_CONFIG_DIR": str(self.home)}
        self.write_session()
        self.write_records(self.record())

    def write_session(self, **changes):
        record = {"pid": 20, "sessionId": self.SID, "procStart": self.START}
        record.update(changes)
        self.session.write_text(json.dumps(record), encoding="utf-8")

    def record(self, **changes):
        record = {"type": "assistant", "isSidechain": False,
                  "message": {"model": self.ARGV[1], "content": self.MARKER},
                  "effort": "medium", "perTurnEffort": "medium"}
        record.update(changes)
        return record

    def write_records(self, *records):
        self.transcript.write_text("".join(json.dumps(r) + "\n" for r in records),
                                   encoding="utf-8")

    def read(self, **kwargs):
        options = {"claude_dir": self.home, "start_time": lambda pid: self.START}
        options.update(kwargs)
        fake = self.tree.get
        if options["claude_dir"] is not None and options["start_time"] is not None:
            return caller_identity.read_caller(40, fake, current_environment=self.env,
                                               **options)
        # Default seams are only used against the real process table; stand the
        # fake in for it while each test patches the filesystem or ps it relies on.
        with patch.object(caller_identity, "_process", fake):
            return caller_identity.read_caller(40, fake, current_environment=self.env,
                                               **options)

    def assert_fallback(self, reason):
        # reason names the refusal branch; the contract is argv selector and empty source.
        self.assertEqual(self.read(), (20, self.ARGV, ""), reason)

    def run_main(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        # main() cannot inject claude_dir/start_time, so stand the fake table in
        # for the real one; the overlay only runs against the real process table.
        fake = self.tree.get
        with patch.dict(os.environ, {"OMNILANE_HOME": str(self.home / "output")}), \
                patch.object(caller_identity.os, "getpid", return_value=40), \
                patch.object(caller_identity, "_process", fake):
            with patch.object(caller_identity.subprocess, "run") as ps:
                ps.return_value.stdout = self.START + "\n"
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = caller_identity.main([], environment=self.env, lookup=fake)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_01_lowered_effort_is_current_ceiling(self):
        self.assertEqual(self.read()[1], ("claude", self.ARGV[1], "medium"))

    def test_02_raised_effort_max(self):
        self.write_records(self.record(effort="max", perTurnEffort="max"))
        self.assertEqual(self.read()[1][2], "max")

    def test_03_conflicting_efforts_take_lower(self):
        self.write_records(self.record(effort="xhigh", perTurnEffort="medium"))
        self.assertEqual(self.read()[1][2], "medium")

    def test_04_model_date_suffix(self):
        self.write_records(self.record(message={"model": "claude-haiku-4-5-20251001"}))
        self.assertEqual(self.read()[1][1], "claude-haiku-4-5")

    def test_05_model_switch(self):
        self.write_records(self.record(message={"model": "claude-sonnet-5"},
                                       effort="high", perTurnEffort="high"))
        self.assertEqual(self.read()[1], ("claude", "claude-sonnet-5", "high"))

    def test_06_missing_session(self):
        self.session.unlink()
        self.assert_fallback("sessions file missing")

    def test_07_start_mismatch(self):
        self.write_session(procStart="Tue Sep 29 19:22:48 2026")
        self.assert_fallback("start time mismatch")

    def test_08_environment_session_mismatch(self):
        self.env["CLAUDE_CODE_SESSION_ID"] = "22222222-2222-4222-8222-222222222222"
        self.assert_fallback("session id mismatch")

    def test_09_duplicate_transcripts(self):
        other = self.home / "projects/other" / self.transcript.name
        other.parent.mkdir()
        other.write_bytes(self.transcript.read_bytes())
        self.assert_fallback("not unique")

    def test_10_sidechains_and_users_are_not_current_turns(self):
        self.write_records(self.record(isSidechain=True), self.record(type="user"))
        self.assert_fallback("no qualifying assistant")

    def test_11_opt_out_does_no_file_access(self):
        self.env["OMNILANE_AA_CLAUDE_TRANSCRIPT"] = "0"
        with patch("builtins.open", side_effect=AssertionError("file access")) as opened, \
                patch.object(Path, "open", side_effect=AssertionError("file access")) as path_open, \
                patch.object(Path, "glob", side_effect=AssertionError("glob access")) as globbed:
            self.assert_fallback("disabled")
            opened.assert_not_called()
            path_open.assert_not_called()
            globbed.assert_not_called()

    def test_12_content_never_leaks(self):
        self.assertNotIn(self.MARKER, self.read()[2])
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertNotIn(self.MARKER, stdout + stderr)

    def test_13_record_before_bounded_tail_is_not_used(self):
        with self.transcript.open("ab") as stream:
            stream.write(b" " * (16 * 1024 * 1024 + 1) + b"\n")
        self.assert_fallback("no qualifying assistant")

    def test_14_main_reports_transcript(self):
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertIn("transcript", stderr)
        result = json.loads(Path(stdout.strip()).read_text())
        self.assertIsInstance(result, dict)
        self.assertIn(f"{self.ARGV[1]}-medium", stderr)

    def test_15_codex_explicit_selection_unchanged(self):
        self.tree[20] = (1, ["codex", "-m", "gpt-6-astra", "-c",
                            'model_reasoning_effort="high"'])
        with patch.object(Path, "open", side_effect=AssertionError("Claude file access")):
            self.assertEqual(self.read(), (20, ("codex", "gpt-6-astra", "high"), ""))

    def test_matches_launch_flags_source(self):
        self.write_records(self.record(effort="xhigh", perTurnEffort="xhigh"))
        self.assertEqual(self.read(), (20, self.ARGV,
                         f"transcript {self.SID[:8]}: matches launch flags"))

    def test_changed_source_exact(self):
        self.assertEqual(self.read()[2],
                         f"transcript {self.SID[:8]}: claude-opus-5-5 at medium "
                         "(launch flags said claude-opus-5-5 at xhigh)")

    def test_latest_main_record_skips_malformed_and_sidechain(self):
        self.write_records(self.record(effort="low", perTurnEffort="low"),
                           self.record(), self.record(isSidechain=True, effort="max",
                                                      perTurnEffort="max"))
        with self.transcript.open("ab") as stream:
            stream.write(b"not json\n{\"partial\":\n\xff\n[]\n")
        self.assertEqual(self.read()[1][2], "medium")

    def test_invalid_effort_does_not_fall_back_to_older_record(self):
        for invalid in ("unknown", "MAX", " "):
            with self.subTest(invalid=invalid):
                self.write_records(self.record(), self.record(effort=invalid))
                self.assert_fallback("unknown effort")

    def test_effort_only_and_per_turn_only(self):
        for changes in ({"perTurnEffort": None}, {"effort": ""},
                        {"perTurnEffort": "xhigh", "effort": "medium"}):
            with self.subTest(changes=changes):
                self.write_records(self.record(**changes))
                self.assertEqual(self.read()[1][2], "medium")

    def test_invalid_session_metadata(self):
        for changes, reason in (({"pid": 99}, "pid mismatch"),
                                ({"pid": "20"}, "pid mismatch"),
                                ({"sessionId": "../invalid"}, "invalid session id"),
                                ({"procStart": "invalid"}, "start time unavailable")):
            with self.subTest(changes=changes):
                self.write_session(**changes)
                self.assert_fallback(reason)

    def test_missing_transcript(self):
        self.transcript.unlink()
        self.assert_fallback("not unique")

    def test_unreadable_transcript(self):
        original = Path.open

        def guarded(path, *args, **kwargs):
            if path == self.transcript:
                raise PermissionError("private details must not escape")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded):
            self.assert_fallback("unreadable")

    def test_start_time_second_precision(self):
        start = datetime(2026, 9, 29, 19, 22, 47, 999999, tzinfo=timezone.utc)
        self.assertEqual(self.read(start_time=lambda pid: start)[1][2], "medium")

    def test_default_config_directory_and_nearest_pid(self):
        seen = []
        result = self.read(claude_dir=None, start_time=lambda pid: seen.append(pid) or self.START)
        self.assertEqual(result[1][2], "medium")
        self.assertEqual(seen, [20])

    def test_absent_environment_session_id_is_allowed(self):
        self.env.pop("CLAUDE_CODE_SESSION_ID")
        self.assertEqual(self.read()[1][2], "medium")

    def test_malformed_session_is_nonfatal(self):
        for raw in ("not json", "[]", "null", "{}"):
            with self.subTest(raw=raw):
                self.session.write_text(raw)
                self.assertEqual(self.read()[1], self.ARGV)

    def test_unavailable_start_time_is_nonfatal(self):
        for start in (None, "", "invalid"):
            with self.subTest(start=start):
                self.assertEqual(self.read(start_time=lambda pid: start), (20, self.ARGV, ""))

    def test_default_start_query_is_utc_and_bounded(self):
        with patch.object(caller_identity.subprocess, "run") as ps:
            ps.return_value.stdout = self.START + "\n"
            self.assertEqual(self.read(start_time=None)[1][2], "medium")
            args, kwargs = ps.call_args
            self.assertEqual(args[0], ["ps", "-o", "lstart=", "-p", "20"])
            self.assertEqual(kwargs["env"]["TZ"], "UTC")
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            self.assertLessEqual(kwargs["timeout"], 5)

    def test_default_home_directory(self):
        self.env.pop("CLAUDE_CONFIG_DIR")
        with patch.object(Path, "home", return_value=self.home.parent):
            with patch.object(Path, "glob", return_value=iter([self.transcript])):
                with patch.object(Path, "read_text", return_value=self.session.read_text()) as read:
                    self.assertEqual(self.read(claude_dir=None)[1][2], "medium")
                    read.assert_called_once_with(encoding="utf-8")

    def test_no_launch_effort_reports_none(self):
        self.tree[20] = (1, ["claude", "--model", self.ARGV[1]])
        result = self.read()
        self.assertEqual(result[1][2], "medium")
        self.assertIn("launch flags said claude-opus-5-5 at none", result[2])

    def test_subagent_directory_is_never_a_match(self):
        other = self.transcript.parent / self.SID / "subagents" / self.transcript.name
        other.parent.mkdir(parents=True)
        self.transcript.rename(other)
        self.assert_fallback("not unique")

    def test_unknown_model_still_fails_closed_in_main(self):
        self.write_records(self.record(message={"model": "claude-unscored-model"}))
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 3)
        self.assertEqual(stdout, "")
        self.assertIn("no scored configuration", stderr)

    def test_injected_lookup_without_seams_never_attempts_overlay(self):
        fake_home = self.home / "fake-home"
        claude = fake_home / ".claude"
        (claude / "sessions").mkdir(parents=True)
        (claude / "projects/encoded-cwd").mkdir(parents=True)
        (claude / "sessions/20.json").write_bytes(self.session.read_bytes())
        (claude / "projects/encoded-cwd" / self.transcript.name).write_bytes(
            self.transcript.read_bytes())
        self.env.pop("CLAUDE_CONFIG_DIR")
        # Control: with both seams injected this home does yield the transcript answer.
        self.assertEqual(self.read(claude_dir=claude)[1][2], "medium")
        for kwargs in ({}, {"claude_dir": claude}, {"start_time": lambda pid: self.START}):
            with self.subTest(passed=sorted(kwargs)):
                with patch.dict(os.environ, {"HOME": str(fake_home)}), \
                        patch.object(Path, "home", return_value=fake_home), \
                        patch.object(caller_identity, "_claude_transcript_overlay",
                                     side_effect=AssertionError("overlay")) as overlay, \
                        patch.object(caller_identity, "_claude_process_start",
                                     side_effect=AssertionError("start")) as start, \
                        patch.object(caller_identity.subprocess, "run",
                                     side_effect=AssertionError("ps")) as ps:
                    result = caller_identity.read_caller(
                        40, self.tree.get, current_environment=self.env, **kwargs)
                self.assertEqual(result, (20, self.ARGV, ""))
                overlay.assert_not_called()
                start.assert_not_called()
                ps.assert_not_called()

    def test_other_vendors_do_not_access_claude_files(self):
        for argv in (["grok", "-m", "grok-4.6", "--reasoning-effort", "high"],
                     ["agy", "--model", "gemini-3.8-flash-low"]):
            with self.subTest(argv=argv):
                self.tree[20] = (1, argv)
                with patch.object(Path, "open", side_effect=AssertionError("file access")):
                    result = self.read()
                self.assertEqual(result, (20, caller_identity.read_selector(argv), ""))


if __name__ == "__main__":
    unittest.main()
