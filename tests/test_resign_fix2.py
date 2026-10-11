"""Absent CLI and pre-channel check contracts; all executables are offline fakes."""
import offline_env  # noqa: F401
import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_resign_fix import host
import build_overlay
import cli_provenance as cp
import origin_policy as op
import probe_sweep
import resign

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def baseline_resign():
    """Load the pinned baseline without replacing a worktree file or module."""
    try:
        source = subprocess.check_output(
            ["git", "show", "c7c63e8:scripts/lib/resign.py"], cwd=ROOT,
            stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise unittest.SkipTest("baseline comparison requires git: executable not found") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise unittest.SkipTest(
            "baseline comparison cannot read c7c63e8:scripts/lib/resign.py "
            f"from the repository: {detail}") from exc
    with tempfile.TemporaryDirectory(prefix="fix5-baseline-", dir=ROOT / ".sandbox-tmp") as directory:
        path = Path(directory) / "resign_c7c63e8.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("resign_c7c63e8", path)
        module = importlib.util.module_from_spec(spec)
        search_path = sys.path[:]
        try:
            spec.loader.exec_module(module)
            yield module
        finally:
            sys.path[:] = search_path


def file_snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def restore_snapshot(root, files, directories):
    """Rewind only the fake host, preserving identical paths and input bytes."""
    for path in root.rglob("*"):
        if path.is_file() and str(path.relative_to(root)) not in files:
            path.unlink()
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and str(path.relative_to(root)) not in directories:
            path.rmdir()
    for name, content in files.items():
        (root / name).write_bytes(content)


VENDORS = ("claude", "codex", "gemini", "grok")
PENDING = {"reason": "signer gate refused", "at": "2026-01-01T00:00:00+00:00",
           "consecutive_runs": 1}

# Literal pre-channel --check decisions, not values derived from the new gate.
# Columns: exit, drifted, reasons, only_missing, cli_changed. Each row applies
# independently to every vendor; {cid} is its last planned configuration ID.
LEGACY_CHECK_EXPECTED = {
    "factory": (0, False, (), False, False),
    "adhoc_untrusted": (0, False, (), False, False),
    "adhoc_trusted": (0, False, (), False, False),
    "script": (0, False, (), False, False),
    "no_platform": (0, False, (), False, False),
    "unsigned": (0, False, (), False, False),
    "pending": (10, True, ("pending re-check",), False, False),
    "missing": (10, True, ("1 configuration(s) never probed on this host: {cid}",), True, False),
    "expired": (10, True, ("1 configuration(s) have expired probe evidence: {cid}",), True, False),
}


def mark_pending(fixture, vendors):
    def update(overlay):
        for mapping in overlay["mappings"]:
            if mapping["identity"]["vendor"] in vendors:
                mapping["pending_recheck"] = dict(PENDING)
    fixture.change_overlay(update)


def channels_snapshot():
    path = op.policy_path()
    return {p.name: p.read_bytes() for p in path.parent.glob(path.name + "*") if p.is_file()}


def vendor_snapshot(fixture, vendor):
    overlay = fixture.overlay()
    return {"mappings": [m for m in overlay["mappings"] if m["identity"]["vendor"] == vendor],
            "evidence": [e for e in overlay["evidence"] if e.get("vendor") == vendor],
            "unproven": [m for m in overlay.get("unproven", [])
                         if build_overlay.ROWS.get(m["config_id"], {}).get("vendor") == vendor]}


@contextmanager
def absent(fixture, vendor):
    """Remove both PATH discovery and the fake pinned file, then restore its bytes."""
    path = fixture.bins[vendor]
    parked = path.with_name(path.name + ".not-installed")
    path.rename(parked)
    cli_path = build_overlay.cli_path
    try:
        with fixture.missing_cli(vendor), patch.object(
                build_overlay, "cli_path", side_effect=lambda name:
                None if fixture.vendor_of(name) == vendor else cli_path(name)):
            yield
    finally:
        parked.rename(path)


def check_cli():
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = resign.main(["--check", "--json"])
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


@contextmanager
def legacy_overlay(scenario):
    kinds = {"adhoc_untrusted": "adhoc", "adhoc_trusted": "adhoc", "script": "script",
             "no_platform": "no-platform-signature", "unsigned": "unsigned"}
    with host("TEST000001") as fixture:
        if scenario in kinds:
            fixture.signers.update({vendor: kinds[scenario] for vendor in VENDORS})
            fixture.build(fixture.sweep_root)
            fixture.live.write_bytes((fixture.sweep_root / "transport-contracts.local.json").read_bytes())
        if scenario == "missing":
            for vendor in VENDORS:
                fixture.never_probed(vendor)
        overlay = fixture.overlay()
        for entry in overlay["evidence"]:
            if isinstance(entry.get("codesign"), dict):
                entry["codesign"].pop("kind", None)
                if scenario == "adhoc_trusted":
                    entry["operator_trust"] = cp.TRUST_ADHOC
                    entry["operator_trust_recorded_at"] = PENDING["at"]
        if scenario == "pending":
            for mapping in overlay["mappings"]:
                mapping["pending_recheck"] = dict(PENDING)
        if scenario == "expired":
            expired = {probe_sweep.plan(vendor)[-1]["config_id"] for vendor in VENDORS}
            for mapping in overlay["mappings"]:
                if mapping["config_id"] in expired:
                    mapping["expires_at"] = "2000-01-01T00:00:00+00:00"
        fixture.live.write_text(json.dumps(overlay))
        yield fixture


def without_pending(overlay):
    result = copy.deepcopy(overlay)
    for mapping in result["mappings"]:
        mapping.pop("pending_recheck", None)
    return result


class AbsentResignTests(unittest.TestCase):
    def assert_skipped(self, output, vendor="claude"):
        lines = "\n".join(line for line in output.splitlines() if vendor in line)
        self.assertEqual([line for line in lines.splitlines() if "沒有安裝" in line],
                         [f"omnilane: {vendor}: not installed; skipped / 沒有安裝，略過"])
        for text in ("--approve", "--trust-", "could not be classified", "No approval command",
                     "無法分類", "核准", "install it and run again / 安裝後再執行"):
            self.assertNotIn(text, lines)

    def assert_pending(self, fixture, vendor, count):
        mappings = fixture.vendor_mappings(vendor)
        self.assertTrue(mappings)
        self.assertTrue(all(m["pending_recheck"]["reason"] == "CLI not found" and
                            m["pending_recheck"]["consecutive_runs"] == count
                            for m in mappings))

    def run_absent(self, pending):
        for with_channel in (False, True):
            with self.subTest(channel=with_channel), host("script") as fixture:
                if with_channel:
                    self.assertEqual(fixture.command("omnilane resign --trust-location claude"), 0)
                if pending:
                    mark_pending(fixture, ["claude"])
                before, channels = fixture.overlay(), channels_snapshot()
                facts = Mock(side_effect=fixture.facts)
                with absent(fixture, "claude"), patch.object(cp, "facts", facts):
                    self.assertEqual(fixture.run_retry(), 20, fixture.lines)
                    self.assert_skipped("\n".join(fixture.lines))
                    self.assertEqual(without_pending(fixture.overlay()), without_pending(before))
                    self.assert_pending(fixture, "claude", 2 if pending else 1)
                    self.assertEqual(channels_snapshot(), channels)
                    self.assertEqual(facts.call_count, 0)
                    row = fixture.args.status_summary["vendors"]["claude"]
                    for key in ("codesign", "origin_allowed", "origin_channels", "origin_reason"):
                        self.assertNotIn(key, row)
                    self.assertFalse(row["gate"]["allowed"])
                    self.assertEqual(fixture.args.status_summary["held"], ["claude"])
                self.assertEqual(fixture.calls, [])

    def test_absent_pending_keeps_overlay_except_pending_without_signature_calls(self):
        self.run_absent(pending=True)

    def test_absent_unmarked_keeps_overlay_except_pending_without_signature_calls(self):
        self.run_absent(pending=False)

    def test_absent_does_not_waive_another_changed_cli_without_channel(self):
        with host("script") as fixture:
            fixture.signers["codex"] = "script"
            fixture.build(fixture.sweep_root)
            fixture.live.write_bytes((fixture.sweep_root / "transport-contracts.local.json").read_bytes())
            mark_pending(fixture, ["claude"])
            fixture.update("codex")
            before, retained = fixture.live.read_bytes(), vendor_snapshot(fixture, "claude")
            channels = channels_snapshot()
            facts = Mock(side_effect=fixture.facts)
            with absent(fixture, "claude"), patch.object(cp, "facts", facts):
                self.assertEqual(fixture.run_retry(vendor=["claude", "codex"]), 20, fixture.lines)
            output = "\n".join(fixture.lines)
            self.assert_skipped(output)
            self.assertIn("codex: NOT re-signing", output)
            self.assertIn("--approve codex", output)
            self.assertIn("這次沒有執行", output)
            self.assertEqual(facts.call_count, 1)
            self.assertEqual(fixture.vendor_of(Path(facts.call_args.args[0]).name), "codex")
            self.assertEqual(fixture.calls, [])
            self.assertNotEqual(fixture.live.read_bytes(), before)
            self.assertEqual(without_pending(vendor_snapshot(fixture, "claude")),
                             without_pending(retained))
            self.assert_pending(fixture, "claude", 2)
            self.assertTrue(all(m.get("pending_recheck") for m in fixture.vendor_mappings("codex")))
            self.assertEqual(channels_snapshot(), channels)
            self.assertEqual(fixture.args.status_summary["held"], ["claude", "codex"])

    def test_absent_pending_is_preserved_when_another_vendor_resigns(self):
        with host("script") as fixture:
            mark_pending(fixture, ["claude"])
            fixture.update("codex")
            before, retained = fixture.live.read_bytes(), vendor_snapshot(fixture, "claude")
            channels = channels_snapshot()
            facts = Mock(side_effect=fixture.facts)
            with absent(fixture, "claude"), patch.object(cp, "facts", facts):
                self.assertEqual(fixture.run_resign(vendor=["claude", "codex"]), 20, fixture.lines)
            self.assert_skipped("\n".join(fixture.lines))
            self.assertIn("codex: re-probing", "\n".join(fixture.lines))
            self.assertEqual(facts.call_count, 4)
            self.assertTrue(all(fixture.vendor_of(Path(c.args[0]).name) != "claude"
                                for c in facts.call_args_list))
            self.assertEqual(fixture.sweeps, ["codex"])
            self.assertNotEqual(fixture.live.read_bytes(), before)
            self.assertEqual(without_pending(vendor_snapshot(fixture, "claude")),
                             without_pending(retained))
            self.assert_pending(fixture, "claude", 2)
            self.assertEqual(channels_snapshot(), channels)
            self.assertEqual(fixture.pinned("codex"), str(fixture.bins["codex"]))

    def test_reinstalled_unchanged_cli_keeps_pending_until_successful_reprobe(self):
        with host("script") as fixture:
            mark_pending(fixture, ["claude"])
            binary = fixture.bins["claude"].read_bytes()
            before, channels = fixture.live.read_bytes(), channels_snapshot()
            facts = Mock(side_effect=fixture.facts)
            with absent(fixture, "claude"), patch.object(cp, "facts", facts):
                self.assertEqual(fixture.run_retry(), 20, fixture.lines)
            self.assert_skipped("\n".join(fixture.lines))
            self.assertEqual(facts.call_count, 0)
            self.assertEqual(without_pending(fixture.overlay()), without_pending(json.loads(before)))
            self.assert_pending(fixture, "claude", 2)
            self.assertEqual(fixture.bins["claude"].read_bytes(), binary)
            pending_overlay = fixture.live.read_bytes()
            facts.reset_mock()
            with patch.object(cp, "facts", facts):
                code, summary, output = check_cli()
            self.assertEqual(code, 10)
            self.assertEqual(summary["vendors"]["claude"]["reasons"], ["pending re-check"])
            self.assertFalse(summary["vendors"]["claude"]["cli_changed"])
            self.assertEqual(facts.call_count, 4)
            self.assertNotIn("--approve", output)
            self.assertEqual(fixture.live.read_bytes(), pending_overlay)
            self.assertEqual(channels_snapshot(), channels)
            facts.reset_mock()
            with patch.object(cp, "facts", facts):
                self.assertEqual(fixture.run_retry(), 0, fixture.lines)
            self.assertIn("CLI unchanged", "\n".join(fixture.lines))
            self.assertNotIn("not installed", "\n".join(fixture.lines))
            self.assertEqual(facts.call_count, 4)
            self.assertTrue(fixture.calls)
            self.assertFalse(any(m.get("pending_recheck") for m in fixture.vendor_mappings("claude")))
            self.assertNotEqual(fixture.live.read_bytes(), before)
            self.assertEqual(channels_snapshot(), channels)


class MissingCliGuidanceTests(unittest.TestCase):
    def assert_no_guidance(self, output):
        for text in ("could not be classified", "No approval command", "--trust-",
                     "--approve", "無法分類", "核准", "install it and run again / 安裝後再執行"):
            self.assertNotIn(text, output)

    def assert_absent_result(self, code, output, vendor="grok"):
        self.assertEqual(code, 20, output)
        message = f"omnilane: {vendor}: not installed; skipped / 沒有安裝，略過"
        self.assertEqual([line for line in output.splitlines() if "沒有安裝" in line], [message])
        self.assertIn(f"omnilane: {vendor}: NOT re-signing", output)
        self.assertGreater(output.index(message),
                           output.index(f"omnilane: {vendor}: NOT re-signing"))
        self.assert_no_guidance(output)

    def assert_absent_without_guidance(self, code, output, vendor="grok", expected_code=20):
        self.assertEqual(code, expected_code, output)
        self.assertIn(f"omnilane: {vendor}: NOT re-signing", output)
        self.assertNotIn("not installed; skipped", output)
        self.assertEqual(output.count("沒有安裝"), 0, output)
        self.assert_no_guidance(output)

    def test_check_missing_cli_keeps_vendor_output_order(self):
        for vendor in VENDORS:
            with self.subTest(vendor=vendor), host("TEST000001") as fixture:
                before, channels = fixture.live.read_bytes(), channels_snapshot()
                stdout, stderr = io.StringIO(), io.StringIO()
                with fixture.missing_cli(vendor), redirect_stdout(stdout), redirect_stderr(stderr):
                    code = resign.main(["--check"])
                self.assertEqual(code, 10)
                self.assertEqual(stdout.getvalue(), "")
                rows = [line for line in stderr.getvalue().splitlines()
                        if any(line.startswith(f"omnilane: {name}:") for name in VENDORS)]
                self.assertEqual(len(rows), len(VENDORS))
                self.assertEqual([line.split(":", 2)[1].strip() for line in rows], list(VENDORS))
                self.assertEqual(rows[VENDORS.index(vendor)],
                                 f"omnilane: {vendor}: not installed; skipped / 沒有安裝，略過")
                self.assertEqual(fixture.live.read_bytes(), before)
                self.assertEqual(channels_snapshot(), channels)

    def test_guidance_when_another_vendor_resigns(self):
        with host("TEST000001") as fixture:
            fixture.update("codex")
            with fixture.missing_cli("grok"):
                code = fixture.run_resign()
            self.assert_absent_result(code, "\n".join(fixture.lines))
            self.assertEqual(fixture.sweeps, ["codex"])
            self.assertTrue(all(m["pending_recheck"]["consecutive_runs"] == 1
                                for m in fixture.vendor_mappings("grok")))

    def test_guidance_when_only_missing_cli(self):
        with host("TEST000001") as fixture:
            before = fixture.overlay()
            with fixture.missing_cli("grok"), patch.object(build_overlay, "main") as build:
                code = fixture.run_resign()
            self.assert_absent_result(code, "\n".join(fixture.lines))
            build.assert_not_called()
            self.assertEqual(without_pending(fixture.overlay()), without_pending(before))
            self.assertTrue(all(m["pending_recheck"]["reason"] == "CLI not found"
                                for m in fixture.vendor_mappings("grok")))

    def test_guidance_without_mappings(self):
        with host("TEST000001") as fixture:
            fixture.change_overlay(lambda overlay: overlay.update(mappings=[
                m for m in overlay["mappings"] if m["identity"]["vendor"] != "gemini"]))
            fixture.update("codex")
            with fixture.missing_cli("gemini"):
                code = fixture.run_resign()
            output = "\n".join(fixture.lines)
            self.assert_absent_without_guidance(code, output, "gemini")
            self.assertIn(
                "omnilane: gemini was not re-signed; no verified mappings to retain (CLI not found).",
                output.splitlines())
            self.assertEqual(fixture.vendor_mappings("gemini"), [])
            self.assertEqual(fixture.sweeps, ["codex"])
    def test_second_missing_removal_without_guidance(self):
        with host("TEST000001") as fixture:
            mark_pending(fixture, ["grok"])
            fixture.change_overlay(lambda overlay: [
                m["pending_recheck"].update(reason="CLI not found")
                for m in overlay["mappings"] if m["identity"]["vendor"] == "grok"])
            with fixture.missing_cli("grok"):
                code = fixture.run_resign(vendor=["grok"])
            output = "\n".join(fixture.lines)
            self.assert_absent_without_guidance(code, output)
            self.assertIn(
                "omnilane: grok mappings removed after two consecutive runs: CLI not found",
                output.splitlines())
            self.assertEqual(fixture.vendor_mappings("grok"), [])
    def test_pending_not_installed_without_guidance(self):
        with host("TEST000001") as fixture:
            before = fixture.live.read_bytes()
            original_verified = resign.verified

            def refuse_pending(path):
                if path != fixture.live:
                    raise ValueError("offline pending install refused")
                return original_verified(path)

            with fixture.missing_cli("grok"), patch.object(
                    resign, "verified", side_effect=refuse_pending):
                code = fixture.run_resign(vendor=["grok"])
            output = "\n".join(fixture.lines)
            self.assert_absent_without_guidance(code, output, expected_code=30)
            self.assertIn(
                "omnilane: grok was not re-signed; live mappings unchanged "
                "(pending update was not installed)", output.splitlines())
            self.assertEqual(fixture.live.read_bytes(), before)

    def test_guidance_via_main_vendor(self):
        with host("TEST000001") as fixture:
            stdout, stderr = io.StringIO(), io.StringIO()
            with fixture.missing_cli("grok"), redirect_stdout(stdout), redirect_stderr(stderr):
                code = resign.main(["--vendor", "grok"])
            self.assert_absent_result(code, stderr.getvalue())
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("grok is not on PATH", stderr.getvalue())
            self.assertIn("the CLI is not installed", stderr.getvalue())

    def test_check_and_resign_agree_on_missing_cli_drift(self):
        with host("TEST000001") as fixture:
            before, channels = fixture.live.read_bytes(), channels_snapshot()
            with fixture.missing_cli("grok"):
                code, summary, _ = check_cli()
                self.assertEqual(code, 10)
                self.assertTrue(summary["vendors"]["grok"]["drifted"])
                self.assertEqual(fixture.live.read_bytes(), before)
                code = fixture.run_resign(vendor=["grok"])
            output = "\n".join(fixture.lines)
            self.assert_absent_result(code, output)
            self.assertNotIn("nothing to re-sign", output)
            self.assertEqual(channels_snapshot(), channels)

    def test_never_configured_missing_cli_is_held_without_pending(self):
        with host("TEST000001") as fixture:
            def never_configured(overlay):
                overlay["mappings"] = [m for m in overlay["mappings"]
                                       if m["identity"]["vendor"] != "grok"]
                overlay["evidence"] = [e for e in overlay["evidence"]
                                       if e.get("vendor") != "grok"]
            fixture.change_overlay(never_configured)
            before, channels = fixture.overlay(), channels_snapshot()
            self.assertEqual(vendor_snapshot(fixture, "grok"),
                             {"mappings": [], "evidence": [], "unproven": []})
            with fixture.missing_cli("grok"), patch.object(cp, "facts") as facts:
                code = fixture.run_retry(vendor=["grok"])
            self.assert_absent_without_guidance(code, "\n".join(fixture.lines))
            self.assertIn(
                "omnilane: grok was not re-signed; no verified mappings to retain (CLI not found).",
                fixture.lines)
            self.assertNotIn("nothing to re-sign", "\n".join(fixture.lines))
            self.assertEqual(fixture.overlay(), before)
            self.assertNotIn("grok", fixture.args.status_summary["pending_recheck"])
            self.assertEqual(fixture.args.status_summary["held"], ["grok"])
            self.assertEqual(channels_snapshot(), channels)
            facts.assert_not_called()
            self.assertEqual(fixture.calls, [])

    def test_daily_records_cli_not_installed(self):
        import verification_status
        with host("TEST000001") as fixture:
            with fixture.missing_cli("grok"):
                code = verification_status.run_daily(
                    lambda: fixture.run_retry(vendor=["grok"]),
                    vendors=["grok"], summary=lambda: fixture.args.status_summary,
                    log=lambda _: None)
            self.assert_absent_result(code, "\n".join(fixture.lines))
            state = json.loads(verification_status.status_path().read_text())
            self.assertEqual(state["pending"]["grok"]["reason"], "cli-not-installed")
            self.assertEqual(state["pending"]["grok"]["exit_code"], 20)
            self.assertEqual(state["pending"]["grok"]["attempts"], 1)
            self.assertNotIn("running", state)

    def test_real_resign_matches_c7c63e8_except_final_missing_guidance(self):
        from verification_status import origin_guidance

        frozen = resign.datetime.now(resign.timezone.utc)

        class FixedDatetime(resign.datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

        advice = origin_guidance("grok", None, None)
        old_line = advice["message"] + " / " + advice["message_zh_tw"]
        new_line = "omnilane: grok: not installed; skipped / 沒有安裝，略過"
        self.comparisons = []
        with baseline_resign() as baseline:
            for scenario in ("first_missing", "second_missing", "no_mappings", "pending_not_installed"):
                for json_mode in (False, True):
                    with self.subTest(scenario=scenario, json=json_mode), host("TEST000001") as fixture:
                        if scenario == "second_missing":
                            mark_pending(fixture, ["grok"])
                            fixture.change_overlay(lambda overlay: [
                                m["pending_recheck"].update(reason="CLI not found")
                                for m in overlay["mappings"] if m["identity"]["vendor"] == "grok"])
                        elif scenario == "no_mappings":
                            fixture.change_overlay(lambda overlay: overlay.update(mappings=[
                                m for m in overlay["mappings"] if m["identity"]["vendor"] != "grok"]))
                        before = file_snapshot(fixture.base)
                        directories = {str(p.relative_to(fixture.base))
                                       for p in fixture.base.rglob("*") if p.is_dir()}
                        results = []
                        for module in (baseline, resign):
                            stdout, stderr = io.StringIO(), io.StringIO()
                            original_verified = module.verified

                            def verify(path):
                                if scenario == "pending_not_installed" and path != fixture.live:
                                    raise ValueError("offline pending install refused")
                                return original_verified(path)

                            with fixture.missing_cli("grok"), patch.object(module, "datetime", FixedDatetime), \
                                    patch.object(module, "verified", side_effect=verify), \
                                    redirect_stdout(stdout), redirect_stderr(stderr):
                                # Check and real resign use the actual parser and entry point.
                                check_code = module.main(["--check", "--json"])
                                check_output = (stdout.getvalue(), stderr.getvalue())
                                stdout.seek(0)
                                stdout.truncate()
                                stderr.seek(0)
                                stderr.truncate()
                                code = module.main(["--vendor", "grok"] + (["--json"] if json_mode else []))
                            results.append((code, stdout.getvalue(), stderr.getvalue(),
                                            file_snapshot(fixture.base), check_code, check_output))
                            restore_snapshot(fixture.base, before, directories)
                        old, new = results
                        self.assertEqual(new[0], old[0])
                        self.assertEqual(new[0], 30 if scenario == "pending_not_installed" else 20)
                        self.assertEqual(new[1].splitlines(keepends=True), old[1].splitlines(keepends=True))
                        if json_mode:
                            self.assertEqual(json.loads(new[1]), json.loads(old[1]))
                        self.assertEqual(new[3], old[3])  # Includes full resign-report.json bytes.
                        self.assertEqual(new[4:], old[4:])  # Full --check stdout, stderr and exit.
                        expected = old[2]
                        if scenario == "first_missing":
                            self.assertEqual(old[2].splitlines().count(old_line), 1)
                            self.assert_absent_result(new[0], new[2])
                            expected = expected.replace(old_line + "\n", new_line + "\n")
                        else:
                            self.assertNotIn("沒有安裝", new[2])
                            self.assert_no_guidance(new[2])
                        self.assertEqual(new[2].splitlines(keepends=True), expected.splitlines(keepends=True))
                        self.comparisons.append({"scenario": scenario, "json": json_mode,
                                                 "before_stdout": old[1], "after_stdout": new[1],
                                                 "before_stderr": old[2], "after_stderr": new[2],
                                                 "exit_code": new[0], "files": sorted(new[3]),
                                                 "check_equal": True, "fake_root": str(fixture.base)})


class LegacyCheckTableTests(unittest.TestCase):
    def test_pre_channel_overlay_check_matches_literal_legacy_table(self):
        for scenario, expected in LEGACY_CHECK_EXPECTED.items():
            with self.subTest(scenario=scenario), legacy_overlay(scenario) as fixture:
                self.assertEqual(channels_snapshot(), {})
                before = fixture.live.read_bytes()
                original = copy.deepcopy(fixture.overlay())
                facts = Mock(side_effect=fixture.facts)
                with patch.object(cp, "facts", facts):
                    code, summary, output = check_cli()
                self.assertEqual(code, expected[0])
                self.assertEqual(set(summary["vendors"]), set(VENDORS))
                self.assertEqual(facts.call_count, 4)
                for vendor in VENDORS:
                    with self.subTest(scenario=scenario, vendor=vendor):
                        row = summary["vendors"][vendor]
                        cid = probe_sweep.plan(vendor)[-1]["config_id"]
                        actual = (code, row["drifted"], tuple(row["reasons"]),
                                  row["only_missing"], row["cli_changed"])
                        wanted = (expected[0], expected[1],
                                  tuple(reason.format(cid=cid) for reason in expected[2]),
                                  expected[3], expected[4])
                        self.assertEqual(actual, wanted)
                        self.assertEqual(row["unprobed"], [cid] if scenario == "missing" else [])
                        self.assertEqual(row["expired"], [cid] if scenario == "expired" else [])
                        self.assertEqual(row["failed_environment"], {})
                        if scenario in ("adhoc_untrusted", "script", "no_platform", "unsigned"):
                            self.assertEqual(row["origin_channels"], [])
                        if scenario == "adhoc_trusted":
                            self.assertEqual([(c["kind"], c["accepts"]) for c in row["origin_channels"]],
                                             [("location", "adhoc")])
                self.assertNotIn("--approve", output)
                self.assertEqual(fixture.live.read_bytes(), before)
                self.assertEqual(fixture.overlay(), original)
                self.assertEqual(channels_snapshot(), {})


if __name__ == "__main__":
    unittest.main()
