import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import quota_preflight as qp


NOW = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)


def frozen_datetime(now):
    """A stand-in for the datetime module whose datetime.now() always answers now; the rest is the real module."""
    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    module = ModuleType("datetime")
    module.__dict__.update(vars(dt))
    module.datetime = FrozenDateTime
    return module


def run_main(*argv):
    stdout = io.StringIO()
    with mock.patch.object(sys, "argv", ["quota_preflight.py", *argv]), \
            contextlib.redirect_stdout(stdout):
        code = qp.main()
    return code, stdout.getvalue()


class SnapshotFileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="quota-cov-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.quota = self.home / "quota"
        self.quota.mkdir()
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("OMNILANE_QUOTA_DIR", "OMNILANE_QUOTA_THRESHOLD_PERCENT",
                     "OMNILANE_QUOTA_MAX_AGE_SECONDS"):
            os.environ.pop(name, None)

    def write(self, **changes):
        value = {"vendor": "codex", "window": "weekly", "used_percent": 42,
                 "captured_at": "2026-10-01T11:50:00Z", "resets_at": "2026-10-05T00:00:00Z",
                 "source": "operator export"}
        value.update(changes)
        (self.quota / "codex.json").write_text(json.dumps(value))

    def test_missing_directory_and_file_are_missing_snapshot(self):
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "missing-snapshot")
        os.environ["OMNILANE_QUOTA_DIR"] = str(self.home / "absent")
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "missing-snapshot")

    def test_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.quota / "codex.json")
        result = qp.snapshot("codex", str(self.home), NOW)
        self.assertEqual(result["reason"], "invalid-snapshot")
        with self.assertRaisesRegex(ValueError, "unsafe or oversized snapshot"):
            qp.read_snapshot(self.quota)

    def test_symlinked_snapshot_or_directory_is_refused(self):
        self.write()
        real = self.home / "real"
        self.quota.rename(real)
        self.quota.symlink_to(real)
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "invalid-snapshot")
        self.quota.unlink()
        self.quota.mkdir()
        (self.quota / "codex.json").symlink_to(real / "codex.json")
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "invalid-snapshot")

    def test_byte_and_character_limits(self):
        path = self.quota / "codex.json"
        path.write_bytes(b" " * 262145)
        with self.assertRaisesRegex(ValueError, "unsafe or oversized snapshot"):
            qp.read_snapshot(self.quota)
        # Within the byte bound but beyond the 65,536-character contract.
        path.write_text(" " * 65537)
        with self.assertRaisesRegex(ValueError, "oversized snapshot"):
            qp.read_snapshot(self.quota)
        path.write_text("é" * 65536)
        self.assertEqual(len(qp.read_snapshot(self.quota)), 65536)
        path.write_bytes(b"\xff")
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "invalid-snapshot")

    def test_fresh_near_limit_and_stale_snapshots(self):
        self.write(used_percent=95)
        fresh = qp.snapshot("codex", str(self.home), NOW)
        self.assertEqual((fresh["status"], fresh["freshness"], fresh["reason"], fresh["near_limit"]),
                         ("fresh", "fresh", "weekly-snapshot", True))
        self.assertEqual(fresh["age_seconds"], 600.0)
        self.assertEqual(fresh["threshold_percent"], 90.0)
        self.write(used_percent=10)
        self.assertFalse(qp.snapshot("codex", str(self.home), NOW)["near_limit"])
        os.environ["OMNILANE_QUOTA_MAX_AGE_SECONDS"] = "60"
        stale = qp.snapshot("codex", str(self.home), NOW)
        self.assertEqual((stale["status"], stale["freshness"], stale["reason"]),
                         ("unknown", "stale", "stale-snapshot"))
        self.assertFalse(stale["near_limit"])

    def test_window_already_reset_is_stale(self):
        self.write(resets_at="2026-10-01T11:55:00+00:00")
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "stale-snapshot")

    def test_invalid_contents_are_invalid_snapshot(self):
        cases = (
            {"vendor": "claude"}, {"window": "daily"}, {"used_percent": True},
            {"used_percent": 101}, {"captured_at": "2026-10-01T11:50:00"},
            {"captured_at": 5}, {"source": ""}, {"source": "x" * 129}, {"source": "bad\nlabel"},
            {"source": 7}, {"captured_at": "2026-10-01T12:30:00Z"},
            {"resets_at": "2026-10-01T11:00:00Z"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.write(**changes)
                self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "invalid-snapshot")
        (self.quota / "codex.json").write_text('{"vendor": "codex", "vendor": "codex"}')
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "invalid-snapshot")
        (self.quota / "codex.json").write_text("[]")
        self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"], "invalid-snapshot")

    def test_configuration_errors(self):
        for name, value in (("OMNILANE_QUOTA_THRESHOLD_PERCENT", "150"),
                            ("OMNILANE_QUOTA_THRESHOLD_PERCENT", "nan"),
                            ("OMNILANE_QUOTA_MAX_AGE_SECONDS", "-1"),
                            ("OMNILANE_QUOTA_MAX_AGE_SECONDS", "inf"),
                            ("OMNILANE_QUOTA_DIR", "relative")):
            with self.subTest(name=name, value=value), mock.patch.dict(os.environ, {name: value}):
                self.assertEqual(qp.snapshot("codex", str(self.home), NOW)["reason"],
                                 "invalid-configuration")

    def test_snapshot_command_prints_flag_status_and_json(self):
        # The command reads the clock itself; pin it to the time the snapshot is written, or a clock step back
        # makes the snapshot "future" and a pause past the maximum age makes it stale.
        self.write(used_percent=99, captured_at=NOW.isoformat(), resets_at="2999-01-01T00:00:00+0000")
        with mock.patch.object(qp, "dt", frozen_datetime(NOW)):
            code, stdout = run_main("snapshot", "codex", str(self.home))
        flag, status, payload = stdout.rstrip("\n").split("\t")
        self.assertEqual((code, flag, status), (0, "1", "fresh"))
        self.assertEqual(json.loads(payload)["reason"], "weekly-snapshot")
        code, stdout = run_main("snapshot", "grok", str(self.home))
        self.assertEqual(stdout.split("\t")[:2], ["0", "unknown"])


class TimestampTests(unittest.TestCase):
    def test_normalized_iso_forms(self):
        expected = dt.datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=dt.timezone.utc)
        for text in ("2026-01-02T03:04:05.123Z", "2026-01-02T03:04:05,123z",
                     "2026-01-02T03:04:05.1230000001+0000", "2026-01-02T03:04:05.123+00:00"):
            with self.subTest(text=text):
                self.assertEqual(qp.timestamp(text), expected)

    def test_rejections(self):
        for value, message in ((None, "must be text"), ("2026-01-02T03:04:05", "requires a timezone")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, message):
                    qp.timestamp(value)
        with self.assertRaises(ValueError):
            qp.timestamp("not a time")


class DecisionTests(unittest.TestCase):
    def args(self, snapshot="", original=("codex", "gpt", "high"), final=("", "", "")):
        return SimpleNamespace(snapshot=snapshot, lane="review", action="exclude",
                               original=list(original), final=list(final))

    def test_candidate_needs_a_vendor(self):
        self.assertIsNone(qp.candidate([]))
        self.assertIsNone(qp.candidate(["", "m", "e"]))
        self.assertEqual(qp.candidate(["codex", "m", "e"]),
                         {"vendor": "codex", "model": "m", "effort": "e"})

    def test_recorded_snapshot_drives_exclusion(self):
        snap = dict(qp.unknown_snapshot("codex", "weekly-snapshot"), status="fresh", near_limit=True)
        value = qp.decision(self.args(json.dumps(snap), final=("claude", "opus", "max")))
        self.assertEqual(value["status"], "fresh")
        self.assertEqual(value["exclusion_reason"], "quota_near_limit")
        self.assertEqual(value["final"], {"vendor": "claude", "model": "opus", "effort": "max"})
        self.assertEqual(value["lane"], "review")
        self.assertIsNone(value["job_id"])
        dt.datetime.fromisoformat(value["decided_at"])

    def test_missing_snapshot_is_unknown_and_vote_maps_to_codex(self):
        value = qp.decision(self.args(original=("vote", "claude,codex", "-")))
        self.assertEqual(value["snapshot"]["vendor"], "codex")
        self.assertEqual(value["snapshot"]["reason"], "snapshot-read-failed")
        self.assertIsNone(value["exclusion_reason"])
        self.assertIsNone(value["final"])
        value = qp.decision(self.args(original=("vote", "claude,grok", "-")))
        self.assertEqual(value["snapshot"]["vendor"], "vote")

    def test_decision_command(self):
        code, stdout = run_main("decision", "--snapshot", "", "--lane", "l", "--action", "keep",
                                "--original-vendor", "codex", "--original-model", "m",
                                "--original-effort", "e", "--final-vendor", "codex",
                                "--final-model", "m", "--final-effort", "e")
        value = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(value["original"], value["final"])
        self.assertEqual(value["action"], "keep")

    def test_persist_command_writes_the_decision(self):
        with tempfile.TemporaryDirectory(prefix="quota-persist-") as directory:
            path = Path(directory) / "decision.json"
            code, stdout = run_main("persist", "--path", str(path), "--decision", '{"a": 1}')
            self.assertEqual((code, stdout), (0, ""))
            self.assertEqual(json.loads(path.read_text()), {"a": 1})


class CapabilityTests(unittest.TestCase):
    """native.choose is the executor's real matcher; a stub isolates this wrapper's mapping."""

    def run_capability(self, chosen, executor="native", context="", error=None):
        calls = []
        native = ModuleType("native")
        native.canonical_dir = lambda path: "canon:" + path
        native.capability_context = lambda path: calls.append(("context", path)) or {"ctx": path}

        def choose(request, context_value):
            calls.append(("choose", request, context_value))
            if error is not None:
                raise error
            return chosen, None, None

        native.choose = choose
        argv = ["capability", "--executor", executor, "--vendor", "claude", "--workdir", "/w",
                "--background", "1", "--context", context]
        with mock.patch.dict(sys.modules, {"native": native}):
            code, _ = run_main(*argv)
        return code, calls

    def test_native_match_is_zero(self):
        code, calls = self.run_capability("native", context="/ctx.json")
        self.assertEqual(code, 0)
        self.assertEqual(calls[0], ("context", "/ctx.json"))
        request = calls[1][1]
        self.assertEqual((request.workdir, request.background, request.vendor), ("canon:/w", True, "claude"))
        self.assertEqual(calls[1][2], {"ctx": "/ctx.json"})

    def test_required_native_mismatch_is_one_and_optional_is_ten(self):
        self.assertEqual(self.run_capability("cli", executor="native")[0], 1)
        code, calls = self.run_capability("cli", executor="auto")
        self.assertEqual(code, 10)
        self.assertIsNone(calls[0][2])

    def test_matcher_errors_are_one(self):
        for error in (ValueError("x"), OSError("x"), KeyError("x"), TypeError("x"), RecursionError()):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.run_capability("native", executor="auto", error=error)[0], 1)


if __name__ == "__main__":
    unittest.main()
