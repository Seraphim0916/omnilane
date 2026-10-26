import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "scripts" / "lib" / "overlay_health.py"
REGISTRY = json.loads((ROOT / "config" / "aa-model-policy.json").read_text())


class OverlayHealthInProcessTests(unittest.TestCase):
    """Run the doctor check in this process so its branches are observable.

    The environment mirrors the subprocess fixtures: isolated tools, a temporary
    OMNILANE_HOME and no inherited vendor configuration.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="overlay-health-cov-")
        self.addCleanup(temp.cleanup)
        # macOS /var is a symlink to /private/var; the check compares resolved paths.
        self.base = Path(temp.name).resolve()
        env, violations = offline_env.isolated_environment(self.base / "tools", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists(), "unmocked network command"))
        env.pop("OMNILANE_AA_OPERATOR_ASSERTED_HUMAN")
        env["OMNILANE_HOME"] = str(self.base / "omnilane")
        Path(env["OMNILANE_HOME"]).mkdir()
        self.env = env
        self.evidence = self.base / "fake-cli"
        self.evidence.write_text("#!/bin/sh\nprintf 'fake codex\\n'\n")
        self.evidence.chmod(0o755)
        self.digest = hashlib.sha256(self.evidence.read_bytes()).hexdigest()

    def overlay(self, **changes):
        entry = {"path": str(self.evidence), "sha256": self.digest}
        entry.update(changes.pop("evidence", {}))
        value = {
            "schema_version": 1,
            "snapshot_id": REGISTRY["snapshot"]["id"],
            "host": socket.gethostname(),
            "source": "test fixture",
            "evidence": [entry],
            "mappings": [],
        }
        value.update(changes)
        path = self.base / "overlay.json"
        path.write_text(json.dumps(value))
        return path

    def mapping(self, config_id, **extra):
        row = next(r for r in REGISTRY["scored_configs"] if r["id"] == config_id)
        value = {
            "config_id": config_id,
            "identity": {k: row[k] for k in ("vendor", "model", "effort", "reasoning", "fallback")},
            "runtime_model": row["model"], "runtime_effort": row["effort"],
            "selector_type": "model_and_effort", "verification": "request-selector-contract",
        }
        value.update(extra)
        return value

    def check(self, overlay=None, *options, resign=None, **extra):
        env = dict(self.env, **extra)
        if overlay is not None:
            env["OMNILANE_AA_TRANSPORT_OVERLAY"] = str(overlay)
        modules = {} if resign is None else {"resign": resign}
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "argv", [str(HEALTH), str(ROOT), *options]), \
                mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.dict(sys.modules, modules), \
                contextlib.redirect_stdout(stdout):
            try:
                runpy.run_path(str(HEALTH), run_name="overlay_health")
                code = None
            except SystemExit as exit_:
                code = exit_.code
        lines = stdout.getvalue().splitlines()
        return code, [line.split("\t") for line in lines]

    def single(self, *args, **kwargs):
        code, lines = self.check(*args, **kwargs)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(len(lines[0]), 2, lines)
        return lines[0]

    def test_absent_overlay_warns_or_passes_for_a_human(self):
        level, message = self.single()
        self.assertEqual(level, "WARN")
        self.assertIn("build_overlay.py", message)
        level, message = self.single(OMNILANE_AA_OPERATOR_ASSERTED_HUMAN="1")
        self.assertEqual(level, "PASS")
        self.assertIn("fine for a human operator", message)

    def test_missing_overlay_fails_on_one_escaped_line(self):
        level, message = self.single(self.base / "not\there\nat all.json")
        self.assertEqual(level, "FAIL")
        self.assertIn("configured overlay is missing", message)
        self.assertIn("not\\there\\nat all.json", message)

    def test_invalid_overlay_diagnostics_fail(self):
        broken = self.base / "broken.json"
        for text in ("{", "[]", '{"unproven": {}}', '{"unproven": [3]}'):
            with self.subTest(text=text):
                broken.write_text(text)
                level, message = self.single(broken)
                self.assertEqual(level, "FAIL")
                self.assertIn("invalid overlay diagnostics", message)

    def test_untagged_drift_and_missing_evidence_name_the_offender(self):
        level, message = self.single(self.overlay(evidence={"sha256": "0" * 64}))
        self.assertEqual(level, "FAIL")
        self.assertIn(f"untagged:hash drift {self.evidence}", message)
        self.assertIn("every dispatch is refused", message)
        gone = self.base / "gone"
        level, message = self.single(self.overlay(evidence={"path": str(gone)}))
        self.assertEqual(level, "FAIL")
        self.assertIn(f"untagged:missing {gone}", message)

    def test_loadable_overlay_passes_and_reports_unproven_configs(self):
        level, message = self.single(self.overlay())
        self.assertEqual(level, "PASS")
        self.assertTrue(message.startswith("verified mappings: none"), message)
        self.assertIn("codex not installed; skipped", message)
        overlay = self.overlay(unproven=[{"config_id": "claude/claude-fable-5-1", "verdict_reason": "quota"}])
        code, lines = self.check(overlay)
        self.assertEqual(code, 0)
        self.assertEqual(lines[0][0], "PASS")
        self.assertIn("1 config(s) recorded unproven", lines[0][1])
        for warning in lines[1:]:
            self.assertEqual(warning[0], "WARN")
            self.assertEqual(json.loads(warning[2])["config_id"], "claude/claude-fable-5-1")

    def test_tagged_drift_degrades_the_vendor(self):
        level, message = self.single(self.overlay(evidence={"sha256": "0" * 64, "vendor": "codex"}))
        self.assertEqual(level, "WARN")
        self.assertIn("stale vendor(s) codex degraded to unverified", message)
        self.assertIn(f"codex:hash drift {self.evidence}", message)

    def test_selector_only_and_pending_mappings_are_called_out(self):
        overlay = self.overlay(evidence={"vendor": "codex"}, mappings=[
            self.mapping("codex/gpt-6-astra", evidence_tier="selector-only"),
            self.mapping("grok/grok-4-6", evidence_tier="billed-model",
                         pending_recheck={"reason": "CLI not found", "at": "2026-10-03T00:00:00+00:00",
                                          "consecutive_runs": 1}),
        ])
        level, message = self.single(overlay)
        self.assertEqual(level, "WARN")
        self.assertIn("verified mappings: codex 1 selector-only, grok 1 billed-model", message)
        self.assertIn("codex prove only the request selector", message)
        self.assertIn("pending re-check: grok: CLI not found", message)

    def test_pending_option_prints_only_pending_vendors(self):
        overlay = self.overlay(mappings=[
            self.mapping("grok/grok-4-6", pending_recheck={"reason": "quota"}),
            self.mapping("codex/gpt-6-astra"),
        ])
        code, lines = self.check(overlay, "--pending")
        self.assertEqual((code, lines), (None, [["pending re-check: grok: quota"]]))
        self.assertEqual(self.check(self.overlay(), "--pending"), (None, []))
        self.assertEqual(self.check(self.base / "absent.json", "--pending"), (None, []))
        broken = self.base / "broken.json"
        broken.write_text('{"mappings": [{"pending_recheck": {"reason": "x"}}]}')
        self.assertEqual(self.check(broken, "--pending"), (None, []))

    def stub_resign(self, report):
        module = ModuleType("resign")
        module.current_anchors = lambda: {}
        module.detect = lambda overlay, anchors: report
        module.origin_status = lambda value: None
        return module

    @staticmethod
    def entry(**changes):
        value = {"cli": "/opt/codex", "recorded_cli_path": "/opt/codex", "reasons": [],
                 "codesign": {"kind": "script"}, "origin_channels": [], "cli_changed": False,
                 "origin_allowed": True, "origin_reason": None}
        value.update(changes)
        return value

    def test_cli_moved_without_going_stale_warns(self):
        resign = self.stub_resign({"codex": self.entry(reasons=["/opt/codex changed", "unrelated"])})
        level, message = self.single(self.overlay(), resign=resign)
        self.assertEqual(level, "WARN")
        self.assertIn("the runners no longer execute what the overlay pinned (codex: /opt/codex changed)",
                      message)
        self.assertIn("codex origin kind=script, channels=[]", message)

    def test_runner_path_mismatch_fails(self):
        resign = self.stub_resign({"codex": self.entry(cli="/new/codex")})
        level, message = self.single(self.overlay(), resign=resign)
        self.assertEqual(level, "FAIL")
        self.assertIn("codex: runs /new/codex, overlay pins /opt/codex", message)

    def test_stale_vendor_also_lists_what_moved(self):
        overlay = self.overlay(evidence={"sha256": "0" * 64, "vendor": "codex"})
        resign = self.stub_resign({"grok": self.entry(reasons=["runs /x"])})
        level, message = self.single(overlay, resign=resign)
        self.assertEqual(level, "WARN")
        self.assertIn("stale vendor(s) codex", message)
        self.assertIn("also moved without going stale: grok: runs /x", message)

    def test_disallowed_origin_change_adds_bilingual_guidance(self):
        resign = self.stub_resign({"codex": self.entry(cli_changed=True, origin_allowed=False,
                                                       origin_reason="unknown-signer")})
        level, message = self.single(self.overlay(), resign=resign)
        self.assertEqual(level, "PASS")
        self.assertGreater(message.count(" / "), 0)
        self.assertIn("codex origin kind=script", message)

    def test_resign_failure_never_breaks_the_health_line(self):
        module = ModuleType("resign")
        module.current_anchors = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        level, message = self.single(self.overlay(), resign=module)
        self.assertEqual(level, "PASS")
        self.assertEqual(message, "verified mappings: none")

    def test_digest_streams_large_files(self):
        big = self.base / "big"
        big.write_bytes(b"x" * (2 * 1024 * 1024 + 7))
        # The module runs main() on import; load its helpers without that final call.
        namespace = {}
        source = HEALTH.read_text().rsplit("\nmain()", 1)[0]
        with mock.patch.object(sys, "argv", [str(HEALTH), str(ROOT)]), \
                mock.patch.object(sys, "path", list(sys.path)):
            exec(compile(source, str(HEALTH), "exec"), namespace)
        self.assertEqual(namespace["digest"](big), hashlib.sha256(big.read_bytes()).hexdigest())
        self.assertEqual(namespace["offenders"](self.base / "absent.json"), [])


if __name__ == "__main__":
    unittest.main()
