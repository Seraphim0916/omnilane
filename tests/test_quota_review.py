"""FIX1 review reproductions. All dispatches use isolated, fake providers."""

import offline_env

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_quota_preflight as fixtures

ROOT = fixtures.ROOT


class QuotaReviewTests(unittest.TestCase):
    setUp = fixtures.QuotaPreflightTests.setUp
    tearDown = fixtures.QuotaPreflightTests.tearDown
    routes = fixtures.QuotaPreflightTests.routes
    snapshot = fixtures.QuotaPreflightTests.snapshot
    dispatch = fixtures.QuotaPreflightTests.dispatch
    plan = fixtures.QuotaPreflightTests.plan
    native_context = fixtures.QuotaPreflightTests.native_context
    exact_policy = fixtures.QuotaPreflightTests.exact_policy

    def reader(self):
        spec = importlib.util.spec_from_file_location(
            "quota_review_reader", ROOT / "scripts/lib/quota_preflight.py")
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        return reader

    def read_snapshot(self, now=None):
        with patch.dict(os.environ, self.env, clear=True):
            return self.reader().snapshot("codex", str(self.home), now)

    def test_missing_snapshot_paths_keep_original_with_missing_reason(self):
        for directory_exists in (False, True):
            with self.subTest(directory_exists=directory_exists):
                directory = self.base / ("empty-quota" if directory_exists else "absent-quota")
                if directory_exists:
                    directory.mkdir()
                self.env["OMNILANE_QUOTA_DIR"] = str(directory)
                result = self.dispatch()
                plan = self.plan(result)
                self.assertEqual(plan["status"], "unknown")
                self.assertEqual(plan["snapshot"]["reason"], "missing-snapshot")
                self.assertEqual(plan["action"], "unchanged")
                self.assertIn("vendor=codex\n", result.stdout)

    def test_invalid_snapshot_paths_keep_invalid_reason(self):
        for kind in ("leaf-symlink", "directory-symlink", "fifo", "malformed-json"):
            with self.subTest(kind=kind):
                directory = self.base / kind
                if kind == "directory-symlink":
                    directory.symlink_to(self.quota, target_is_directory=True)
                else:
                    directory.mkdir()
                    leaf = directory / "codex.json"
                    if kind == "leaf-symlink":
                        leaf.symlink_to(self.quota / "codex.json")
                    elif kind == "fifo":
                        os.mkfifo(leaf)
                    else:
                        leaf.write_text("{broken")
                self.env["OMNILANE_QUOTA_DIR"] = str(directory)
                result = self.dispatch()
                plan = self.plan(result)
                self.assertEqual(plan["status"], "unknown")
                self.assertEqual(plan["snapshot"]["reason"], "invalid-snapshot")
                self.assertEqual(plan["action"], "unchanged")
                self.assertIn("vendor=codex\n", result.stdout)

    def shell(self, code):
        return subprocess.run(["/bin/bash", "-c", code], env=self.env,
                              cwd=ROOT, text=True, capture_output=True, timeout=30)

    def function(self, name):
        source = (ROOT / "scripts/dispatch.sh").read_text()
        start = source.index(name + "() {")
        return source[start:source.index("\n}\n", start) + 3]

    def preflight_shell(self, extra):
        # Real resolver and policy state machine; only policy verdicts and
        # executable/capability availability are controlled fixtures.
        code = "set -euo pipefail\n"
        code += "source " + shlex.quote(str(ROOT / "scripts/lib/quota-preflight.sh")) + "\n"
        for name in ("parse_lane_segment", "aa_vote_child_spec",
                     "aa_policy_candidate_allowed", "resolve_chain"):
            code += self.function(name) + "\n"
        code += """
LANE=quota-unit; MODE=advise; WORKDIR="$PWD"; EXECUTOR=cli
SESSION_REQUEST=auto; THREAD_NAME=''; NATIVE_CONTEXT=''; BACKGROUND=0
OVERRIDE_MODEL=''; OVERRIDE_EFFORT=''; OVERRIDE_JOB_TIMEOUT=''; OVERRIDE_IDLE_TIMEOUT=''
OMNILANE_GROK_MACOS_WORK_UNCONFINED=0
AA_POLICY_ACTIVE=1; AA_EXPLICIT_TARGET=0; AA_TARGET_CONFIG=''
AA_SELECTED_DECISION=''; AA_LAST_DECISION=''; AA_VOTE_PREFLIGHT=0
RESOLVE_WITH_CONTEXT=0
aa_policy_decide() { AA_LAST_DECISION="$1/$2/$3"; return 0; }
routing_candidate_available() { return 0; }
"""
        self.env["OMNILANE_REPO"] = str(ROOT)
        return self.shell(code + extra)

    def test_a1_auto_keeps_chain_order_and_exact_cli_fallback(self):
        self.snapshot(99)
        self.routes(self.chain + " | gemini fixture-gemini high")
        context = self.native_context("gemini")
        result = self.dispatch("--executor", "auto", "--native-context", str(context))
        decision = self.plan(result)
        self.assertEqual(decision["final"]["vendor"], "claude")
        self.assertIn("vendor=claude\n", result.stdout)
        self.assertIn("executor=cli\n", result.stdout)
        self.assertNotIn("vendor=codex\n", result.stdout)

    def test_a1_native_below_threshold_preserves_original_refusal(self):
        context = self.native_context("claude")
        for enabled in ("0", "1"):
            self.env["OMNILANE_QUOTA_ENABLED"] = enabled
            result = self.dispatch("--executor", "native", "--native-context", str(context))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("vendor-mismatch", result.stderr)
            self.assertFalse(self.marker.exists())

    def test_a1_no_alternative_rechecks_exact_cli_availability(self):
        self.snapshot(99)
        self.routes("codex fixture-codex high")
        self.env["CODEX_BIN"] = str(self.bins / "missing")
        result = self.dispatch("--executor", "auto", "--native-context",
                               str(self.native_context("gemini")))
        self.assertEqual(result.returncode, 4)
        self.assertIn("exact CLI target 'codex' unavailable", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_a2_vote_state_is_reset_and_failed_selection_restored(self):
        self.snapshot(99)
        for chain in ("vote codex,claude 1 | claude fixture-claude high",
                      "codex fixture-codex high | vote codex,claude 1 | claude fixture-claude high"):
            result = self.preflight_shell(
                "CHAIN=" + shlex.quote(chain) + "\nresolve_chain \"$CHAIN\"\n"
                "quota_preflight\n"
                "printf '%s|%s|%s|%s\\n' \"${RESOLVED_FIELDS[0]}\" \"$AA_VOTE_PREFLIGHT\" "
                "\"$RESOLVED_IDX\" \"$AA_SELECTED_DECISION\"\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(),
                             "claude|0|{}|claude/fixture-claude/high".format(chain.count("|") + 1))
        result = self.preflight_shell("""
CHAIN='codex fixture-codex high | vote codex,claude 1 | off'
resolve_chain "$CHAIN"
quota_preflight
printf '%s|%s|%s|%s\n' "${RESOLVED_FIELDS[0]}" "$AA_VOTE_PREFLIGHT" "$RESOLVED_IDX" "$AA_SELECTED_DECISION"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "codex|0|1|codex/fixture-codex/high")

    def test_a3_explicit_unknown_notice_is_required_by_policy(self):
        self.routes("grok fixture-grok high")
        result = self.dispatch("--vendor", "grok")
        decision = self.plan(result)
        self.assertEqual(decision["action"], "explicit")
        self.assertIn("quota status=unknown explicit target preserved", result.stderr)
        self.assertNotIn("quota_near_limit", result.stderr)

    def test_a3_existing_thread_skips_quota_before_pin_validation(self):
        self.snapshot(99)
        threads = self.home / "threads"
        threads.mkdir()
        # Invalid state is deliberately rejected by the existing thread gate.
        # A quota refusal or reroute must not precede that gate.
        (threads / "existing.json").write_text("{}")
        self.routes("codex fixture-codex high")
        for flags in ((), ("--vendor", "codex")):
            result = self.dispatch("--thread", "existing", *flags, lane="hardest-coding")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("state is not safely readable", result.stderr)
            self.assertNotIn("quota", result.stdout + result.stderr)

    def test_b1_empty_directory_uses_home_not_workdir(self):
        self.snapshot(12)
        self.env["OMNILANE_QUOTA_DIR"] = ""
        # Deliberately put a valid contrary snapshot in the caller's cwd.
        path = self.base / "codex.json"
        value = json.loads((self.quota / "codex.json").read_text())
        value["used_percent"] = 99
        path.write_text(json.dumps(value))
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/lib/quota_preflight.py"),
             "snapshot", "codex", str(self.home)], cwd=self.base, env=self.env,
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.split("\t", 2)[2])["used_percent"], 12)

    def test_b1_empty_numeric_settings_use_defaults(self):
        self.env.update(OMNILANE_QUOTA_THRESHOLD_PERCENT="", OMNILANE_QUOTA_MAX_AGE_SECONDS="")
        value = self.read_snapshot()
        self.assertEqual(value["status"], "fresh")
        self.assertEqual(value["threshold_percent"], 90)
        self.assertEqual(value["max_age_seconds"], 1800)

    def test_b2_snapshot_and_directory_symlinks_are_unknown(self):
        real = self.quota / "codex.json"
        real.rename(self.quota / "real.json")
        real.symlink_to(self.quota / "real.json")
        self.assertEqual(self.read_snapshot()["status"], "unknown")
        real.rename(self.quota / "link.json")
        (self.quota / "real.json").rename(real)
        alias = self.base / "quota-link"
        alias.symlink_to(self.quota, target_is_directory=True)
        self.env["OMNILANE_QUOTA_DIR"] = str(alias)
        self.assertEqual(self.read_snapshot()["status"], "unknown")

    def test_b2_open_uses_nofollow_nonblock_and_fstat(self):
        reader = self.reader()
        opened = []
        actual_open = os.open
        actual_fstat = os.fstat
        def checked_open(path, flags, *args, **kwargs):
            opened.append(flags)
            return actual_open(path, flags, *args, **kwargs)
        def nonregular(fd):
            value = actual_fstat(fd)
            if stat.S_ISDIR(value.st_mode):
                return value
            values = list(value)
            values[0] = stat.S_IFIFO | 0o600
            return os.stat_result(values)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(reader.os, "open", side_effect=checked_open), \
             patch.object(reader.os, "fstat", side_effect=nonregular):
            value = reader.snapshot("codex", str(self.home))
        self.assertEqual(value["status"], "unknown")
        self.assertTrue(opened)
        self.assertTrue(all(flags & os.O_NOFOLLOW for flags in opened))
        self.assertTrue(any(flags & os.O_NONBLOCK for flags in opened))

    def test_b2_leaf_swap_to_symlink_is_rejected(self):
        reader = self.reader()
        actual_open = os.open
        swapped = []
        def swap_leaf(path, flags, *args, **kwargs):
            if path == "codex.json":
                leaf = self.quota / path
                leaf.rename(self.quota / "saved.json")
                leaf.symlink_to(self.quota / "saved.json")
                swapped.append(True)
            return actual_open(path, flags, *args, **kwargs)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(reader.os, "open", side_effect=swap_leaf):
            self.assertEqual(reader.snapshot("codex", str(self.home))["status"], "unknown")
        self.assertEqual(swapped, [True])

    def test_b2_directory_swap_cannot_redirect_pinned_read(self):
        self.snapshot(12)
        reader = self.reader()
        actual_open = os.open
        other = self.base / "replacement"
        other.mkdir()
        value = json.loads((self.quota / "codex.json").read_text())
        value["used_percent"] = 99
        (other / "codex.json").write_text(json.dumps(value))
        swapped = []
        def swap_parent(path, flags, *args, **kwargs):
            fd = actual_open(path, flags, *args, **kwargs)
            if Path(path) == self.quota:
                self.quota.rename(self.home / "original-quota")
                self.quota.symlink_to(other, target_is_directory=True)
                swapped.append(True)
            return fd
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(reader.os, "open", side_effect=swap_parent):
            self.assertEqual(reader.snapshot("codex", str(self.home))["used_percent"], 12)
        self.assertEqual(swapped, [True])

    def test_b2_fifo_does_not_block_and_relative_directory_is_unknown(self):
        (self.quota / "codex.json").rename(self.quota / "saved.json")
        os.mkfifo(self.quota / "codex.json")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/lib/quota_preflight.py"), "snapshot", "codex", str(self.home)],
            env=self.env, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.split("\t", 2)[2])["status"], "unknown")
        self.env["OMNILANE_QUOTA_DIR"] = "."
        self.assertEqual(self.read_snapshot()["reason"], "invalid-configuration")

    def test_b3_timestamp_formats_and_invalid_interior_z(self):
        reader = self.reader()
        for value in ("2026-10-18T01:02:03Z", "2026-10-18T01:02:03z", "2026-10-18T01:02:03+0800",
                      "2026-10-18T01:02:03.5+08:00"):
            with self.subTest(value=value):
                self.assertIsNotNone(reader.timestamp(value).utcoffset())
        for value in ("2026-10-18T01:02:03", "2026-Z10-18T01:02:03Z", "bad"):
            with self.assertRaises(ValueError):
                reader.timestamp(value)

    def test_c1_bash32_preserves_source_spaces_backslashes(self):
        for source in ("operator weekly snapshot", r"operator \\ weekly snapshot", 'operator "weekly" snapshot'):
            self.snapshot(99, source=source)
            self.assertEqual(self.plan(self.dispatch())["snapshot"]["source"], source)

    def test_c1_snapshot_subprocess_failure_falls_back_unknown(self):
        real_python = self.env["PATH"].split(os.pathsep)[-1] + "/python3"
        wrapper = self.bins / "python3"
        self.snapshot(99)
        for rc in (23, 0):
            wrapper.write_text("#!/bin/sh\n"
                               'case "$1:$2" in *quota_preflight.py:snapshot) exit ' + str(rc) + ';; esac\n'
                               'exec ' + shlex.quote(real_python) + ' "$@"\n')
            wrapper.chmod(0o755)
            result = self.dispatch()
            decision = self.plan(result)
            self.assertEqual(decision["status"], "unknown")
            self.assertEqual(decision["action"], "unchanged")
            self.assertNotIn("Traceback", result.stderr)

    def test_c2_lane_guard_and_bash32_unset_indirection(self):
        result = self.dispatch(lane="invalid.lane")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid lane name", result.stderr)
        self.routes("codex fixture-codex high")
        self.env["CODEX_BIN"] = str(self.bins / "missing")
        result = self.dispatch()
        self.assertEqual(result.returncode, 4)
        self.assertNotIn("unbound variable", result.stderr)
        self.assertNotIn("quota_preflight", result.stdout)
        result = self.preflight_shell("""
RESOLVE_WITH_CONTEXT=1
EXECUTOR=auto
NATIVE_CONTEXT=''
quota_candidate_capable claude fixture-claude high
printf 'capability-ok\n'
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "capability-ok")

    def test_c3_decision_handles_leading_dash_fields(self):
        # The policy/parser may reject such a model upstream; the quota helper
        # must still treat already resolved candidate data as literal data.
        result = self.preflight_shell("""
CHAIN='codex -model -effort'
AA_EXPLICIT_TARGET=1
resolve_chain "$CHAIN"
quota_preflight
printf '%s\n' "$QUOTA_DECISION"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["final"]["model"], "-model")

    def test_a3_valid_existing_thread_keeps_target_and_no_quota_output(self):
        self.snapshot(99)
        threads = self.home / "threads"
        threads.mkdir()
        state = dict(name="existing", vendor="codex", model="fixture-codex", effort="high",
                     workdir=str(ROOT), session_id="fixture-session", turns=1,
                     last_job_id="20261018-000000-123-1", created="2026-10-18T00:00:00Z",
                     updated="2026-10-18T00:00:00Z")
        (threads / "existing.json").write_text(json.dumps(state))
        for flags in ((), ("--vendor", "codex")):
            result = self.dispatch("--thread", "existing", *flags)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("vendor=codex\n", result.stdout)
            self.assertNotIn("quota_preflight=", result.stdout)
            self.assertNotIn("quota status", result.stderr)
            self.assertNotIn("quota_near_limit", result.stderr)

    def test_d2_reset_boundaries(self):
        now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
        captured = now - dt.timedelta(seconds=30)
        for reset, reason, freshness in (
            (now, "stale-snapshot", "stale"),
            (now - dt.timedelta(seconds=1), "stale-snapshot", "stale"),
            (captured, "invalid-snapshot", "unknown"),
            (captured - dt.timedelta(seconds=1), "invalid-snapshot", "unknown")):
            self.snapshot(99, captured_at=captured.isoformat(), resets_at=reset.isoformat())
            value = self.read_snapshot(now)
            self.assertEqual((value["status"], value["reason"], value["freshness"]),
                             ("unknown", reason, freshness))

    def test_d3_source_boundaries(self):
        for source in ("", "a" * 129, "tab\there", "new\nline", "control\x00"):
            self.snapshot(99, source=source)
            self.assertEqual(self.read_snapshot()["reason"], "invalid-snapshot")
        self.snapshot(99, source="a" * 128)
        self.assertEqual(self.read_snapshot()["status"], "fresh")

    def test_d4_invalid_configuration(self):
        for key, values in (("OMNILANE_QUOTA_THRESHOLD_PERCENT", ("-1", "101", "nan", "oops")),
                            ("OMNILANE_QUOTA_MAX_AGE_SECONDS", ("-1", "nan", "inf", "oops"))):
            for value in values:
                self.env[key] = value
                self.assertEqual(self.read_snapshot()["reason"], "invalid-configuration")
            del self.env[key]

    def test_d5_new_thread_and_live_capability_filter(self):
        self.snapshot(99)
        self.routes("codex fixture-codex high | exec /bin/true - | claude fixture-claude high")
        result = self.dispatch("--thread", "new-thread")
        self.assertEqual(self.plan(result)["final"]["vendor"], "claude")
        result = self.dispatch("--background", "--live")
        self.assertEqual(self.plan(result)["final"]["vendor"], "claude")
        self.routes("codex fixture-codex high | grok fixture-grok high | claude fixture-claude high")
        result = self.dispatch("--background", "--live")
        self.assertEqual(self.plan(result)["final"]["vendor"], "claude")
        result = self.dispatch("--background", "--live", "--mode", "sysops")
        self.assertEqual(self.plan(result)["final"]["vendor"], "grok")

    def test_d6_target_config_preserved(self):
        _, _, codex, _ = self.exact_policy()
        self.snapshot(99)
        result = self.dispatch("--target-config", codex["id"])
        self.assertEqual(self.plan(result)["action"], "explicit")
        self.assertIn("vendor=codex\n", result.stdout)

    def test_d6_inherit_never_calls_quota_reader(self):
        # A valid inherited native plan must bypass the quota helper entirely.
        _, _, codex, _ = self.exact_policy()
        context_path = self.native_context("codex")
        context = json.loads(context_path.read_text())
        context.update(inherits_caller_runtime=True, current_model=codex["model"])
        context["capabilities"][0]["model"] = codex["model"]
        context_path.write_text(json.dumps(context))
        self.snapshot(99)
        real_python = self.env["PATH"].split(os.pathsep)[-1] + "/python3"
        marker = self.base / "quota-called"
        wrapper = self.bins / "python3"
        wrapper.write_text("#!/bin/sh\ncase \"$1\" in *quota_preflight.py) "
                           "touch " + shlex.quote(str(marker)) + "; exit 99;; esac\n"
                           "exec " + shlex.quote(real_python) + ' "$@"\n')
        wrapper.chmod(0o755)
        result = self.dispatch("--executor", "native", "--inherit", "--native-context", str(context_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["inherit"])
        self.assertNotIn("quota_preflight", json.loads(result.stdout))
        self.assertFalse(marker.exists())
        self.assertNotIn("quota", result.stderr)

    def test_e2_deep_context_refusal_is_one_json_line(self):
        self.snapshot(99)
        path = self.base / "deep-context.json"
        path.write_text("[" * 1200 + "]" * 1200)
        result = self.dispatch("--executor", "native", "--native-context", str(path), lane="hardest-coding")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(json.loads(result.stderr)["code"], "quota_near_limit_no_alternative")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertFalse(self.marker.exists())

    def test_disabled_matches_bafcedb_bytes(self):
        # Only the two changed production entrypoints are materialized. All
        # shared dependencies are unchanged since bafcedb; no repository copy.
        originals = {}
        for path in ("scripts/dispatch.sh", "scripts/lib/native.py"):
            result = subprocess.run(["git", "show", "bafcedb:" + path], cwd=ROOT,
                                    env=self.env, capture_output=True, timeout=20)
            if result.returncode:
                self.skipTest("bafcedb object unavailable (e.g. shallow checkout)")
            originals[path] = result.stdout
        native = self.base / "baseline-native.py"
        native.write_bytes(originals["scripts/lib/native.py"])
        baseline = self.base / "baseline-dispatch.sh"
        source = originals["scripts/dispatch.sh"].decode()
        original_import = 'source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"'
        self.assertEqual(source.count(original_import), 1)
        source = source.replace(original_import, "source " + shlex.quote(str(ROOT / "scripts/lib/common.sh")))
        source = source.replace('"$OMNILANE_REPO/scripts/lib/native.py"', shlex.quote(str(native)))
        baseline.write_text(source)
        self.env.update(OMNILANE_QUOTA_ENABLED="0", PYTHONPATH=str(ROOT / "scripts/lib"))
        self.snapshot(99)
        context = self.native_context("codex")
        cases = [[], ["--vendor", "codex"], ["--model", "fixture-codex"],
                 ["--effort", "high"], ["--thread", "new-thread"],
                 ["--executor", "auto", "--native-context", str(context)],
                 ["--executor", "native", "--native-context", str(context)],
                 ["--background", "--live"]]
        for flags in cases:
            args = ["--dry-run", "--executor", "cli", *flags, "quota-unit", "quota fixture"]
            current = subprocess.run(["/bin/bash", str(ROOT / "scripts/dispatch.sh"), *args],
                                     env=self.env, cwd=ROOT, capture_output=True, timeout=30)
            old = subprocess.run(["/bin/bash", str(baseline), *args],
                                 env=self.env, cwd=ROOT, capture_output=True, timeout=30)
            with self.subTest(flags=flags):
                self.assertEqual(current.returncode, 0, current.stderr)
                self.assertEqual((current.returncode, current.stdout, current.stderr),
                                 (old.returncode, old.stdout, old.stderr))
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.home / "jobs").exists())

    def test_e1_quota_record_exists_when_metadata_is_published(self):
        # Observe the actual metadata publication boundary without starting a
        # provider: the mv shim checks quota and deliberately stops dispatch.
        observed = self.base / "publication.json"
        wrapper = self.bins / "mv"
        wrapper.write_text("#!/bin/sh\ncase \"$2\" in */meta.json)\n"
                           "python3 - \"$2\" " + shlex.quote(str(observed)) + " <<'PY'\n"
                           "import json, pathlib, stat, sys\n"
                           "q=pathlib.Path(sys.argv[1]).with_name('quota-preflight.json')\n"
                           "v={'exists':q.exists(),'mode':stat.S_IMODE(q.stat().st_mode) if q.exists() else None,"
                           "'already_published':pathlib.Path(sys.argv[1]).exists()}\n"
                           "if q.exists(): v['job_id']=json.loads(q.read_text())['job_id']\n"
                           "pathlib.Path(sys.argv[2]).write_text(json.dumps(v))\nPY\nexit 88;; esac\n"
                           'exec /bin/mv "$@"\n')
        wrapper.chmod(0o755)
        result = self.dispatch(dry=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        value = json.loads(observed.read_text())
        self.assertTrue(value["exists"], value)
        self.assertFalse(value["already_published"], value)
        self.assertEqual(value["mode"], 0o600)
        self.assertTrue(value["job_id"])

    def test_e1_persist_replaces_symlink_without_touching_target(self):
        protected = self.base / "protected.json"
        protected.write_text("sentinel")
        record = self.base / "quota-preflight.json"
        record.symlink_to(protected)
        value = dict(job_id="fixture", action="unchanged")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/lib/quota_preflight.py"), "persist",
             "--path=" + str(record), "--decision=" + json.dumps(value)],
            env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(protected.read_text(), "sentinel")
        self.assertFalse(record.is_symlink())
        self.assertEqual(json.loads(record.read_text()), value)
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)

    def test_e1_persist_failure_does_not_publish_metadata_or_call_provider(self):
        real_python = self.env["PATH"].split(os.pathsep)[-1] + "/python3"
        wrapper = self.bins / "python3"
        wrapper.write_text("#!/bin/sh\n"
                           'case "$1:$2" in *quota_preflight.py:persist) exit 79;; esac\n'
                           'exec ' + shlex.quote(real_python) + ' "$@"\n')
        wrapper.chmod(0o755)
        result = self.dispatch(dry=False)
        self.assertEqual(result.returncode, 79, result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(list((self.home / "jobs").glob("*/meta.json")), [])

    def test_e2_native_validation_exceptions_are_value_errors(self):
        # Exercise invalid contexts through the actual capability CLI rather
        # than invent an exception native.py never raises.
        path = self.base / "bad-context.json"
        for raw in ("{}", "[]", "{", '{"schema_version":9}'):
            path.write_text(raw)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/lib/quota_preflight.py"), "capability",
                 "--executor=auto", "--vendor=claude", "--model=fixture-claude", "--effort=high",
                 "--mode=advise", "--workdir=" + str(ROOT), "--context=" + str(path)],
                env=self.env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
