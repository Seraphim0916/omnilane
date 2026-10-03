"""Offline CLI coverage for the operator-only macOS Grok work option."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from offline_env import fixture_environment_with_isolated_tools

ROOT = Path(__file__).resolve().parents[1]
OPTION = "option.grok-macos-work: unconfined\n"
NOTICE = ("omnilane: Grok work on macOS runs without isolation (operator opt-in): "
          "commands can reach the network and write outside the workdir")
REFUSAL = ("omnilane: Grok work requires disabled agent-tool network; "
           "xAI CLI child-network restrictions are not enforced on macOS\n")


class GrokUnconfinedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnilane-grok-option-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.bin = self.base / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.local = self.home / "routing.local.yaml"
        self.marker = self.base / "grok-args.txt"
        self.env = fixture_environment_with_isolated_tools(self)
        self.env.update({
            "OMNILANE_HOME": str(self.home), "HOME": str(self.home),
            "TMPDIR": str(self.base), "OMNILANE_AA_OPERATOR_ASSERTED_HUMAN": "1",
            "OMNILANE_GROK_MAX_ATTEMPTS": "1", "OMNILANE_DEPTH": "0",
            "OMNILANE_GROK_MACOS_WORK_UNCONFINED": "0",
        })
        for name in ("OMNILANE_AA_CALLER_CONTEXT", "OMNILANE_AA_CALLER_SHA256",
                     "OMNILANE_INBOX", "OMNILANE_THREAD_MODE", "OMNILANE_THREAD_ID",
                     "CLAUDE_CODE_SESSION_ID", "OMNILANE_NATIVE_CONTEXT"):
            self.env.pop(name, None)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]
        self.script("uname", 'printf "%s\\n" "${FAKE_SYSTEM:-Darwin}"\n')
        grok = self.script("fake-grok", f'printf "%s\\n" "$@" > "{self.marker}"\nprintf "fake-result\\n"\n')
        ok = self.script("fake-ok", "printf 'fallback-result\\n'\n")
        for key in ("CODEX_BIN", "CLAUDE_BIN", "AGY_BIN", "GROK_BIN"):
            self.env[key] = str(grok if key == "GROK_BIN" else ok)
        self.gate = self.script("gate.sh", "printf 'fallback-result\\n'\n")
        self.local.write_text(f'probe: grok fixture - | exec "{self.gate}" -\n')

    def script(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o700)
        return path

    def run_cli(self, script, *args, input=None, env=None, repo=ROOT):
        return subprocess.run(["/bin/bash", str(repo / script), *map(str, args)],
                              cwd=repo, env=env or self.env, input=input,
                              text=True, capture_output=True, timeout=45)

    def dispatch(self, *args, repo=ROOT):
        return self.run_cli("scripts/dispatch.sh", *args, repo=repo)

    def work(self, *flags, lane="probe", repo=ROOT):
        return self.dispatch("--mode", "work", "--workdir", self.base,
                             "--executor", "cli", *flags, lane, "fixture task", repo=repo)

    def enable(self):
        self.local.write_text(self.local.read_text() + OPTION)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def fixture_repo(self):
        repo = self.base / "repo"
        for directory in ("scripts", "hooks", "config"):
            for source in (ROOT / directory).rglob("*"):
                if source.suffix not in (".sh", ".py", ".pl", ".yaml", ".yml", ".json"):
                    continue
                if re.search(r"env|credential|login|keychain|keystore", source.name, re.I):
                    continue
                target = repo / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        for name in ("routing.yaml", "VERSION", "bin/omnilane"):
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        return repo

    def test_off_explicit_refusal_byte_for_byte_and_no_job(self):
        for flags in ((), ("--dry-run",)):
            result = self.work("--vendor", "grok", *flags)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (2, "", REFUSAL))
        self.assertFalse((self.home / "jobs").exists())

    def test_off_chain_falls_through_in_real_and_dry_run(self):
        dry = self.work("--dry-run")
        self.assert_ok(dry)
        self.assertIn("vendor=exec\n", dry.stdout)
        self.assertIn("candidate=2/2\n", dry.stdout)
        real = self.work()
        self.assert_ok(real)
        self.assertIn("fallback-result", real.stdout)
        self.assertFalse(self.marker.exists())

    def test_shipped_coding_overflow_skips_both_grok_rows(self):
        result = self.work("--dry-run", lane="coding-overflow")
        self.assert_ok(result)
        self.assertIn("vendor=gemini\n", result.stdout)
        self.assertIn("candidate=3/7\n", result.stdout)

    def test_shipped_option_is_lint_failure_and_ignored(self):
        repo = self.fixture_repo()
        routing = repo / "routing.yaml"
        routing.write_text(routing.read_text() + OPTION)
        lint = self.dispatch("--validate", repo=repo)
        self.assertEqual(lint.returncode, 2, lint.stdout + lint.stderr)
        self.assertIn("FAIL option.grok-macos-work", lint.stdout)
        result = self.work("--vendor", "grok", repo=repo)
        self.assertEqual((result.returncode, result.stderr), (2, REFUSAL))
        self.assertIn("vendor=exec\n", self.work("--dry-run", repo=repo).stdout)

    def test_invalid_duplicate_and_inherited_flag_stay_off(self):
        lane = self.local.read_text()
        self.env["OMNILANE_GROK_MACOS_WORK_UNCONFINED"] = "1"
        for option in ("", "option.grok-macos-work: yes\n", OPTION + OPTION,
                       OPTION + "option.grok-macos-work: invalid\n"):
            with self.subTest(option=option):
                self.local.write_text(lane + option)
                if option:
                    lint = self.dispatch("--validate")
                    self.assertEqual(lint.returncode, 2, lint.stdout)
                result = self.work("--vendor", "grok")
                self.assertEqual((result.returncode, result.stderr), (2, REFUSAL))

    def test_on_flags_metadata_four_notice_surfaces_and_audit(self):
        self.enable()
        dry = self.work("--dry-run", "--vendor", "grok")
        self.assert_ok(dry)
        self.assertIn(NOTICE, dry.stdout)
        self.assertEqual(dry.stderr, NOTICE + "\n")
        self.assertFalse((self.home / "jobs").exists())
        result = self.work("--vendor", "grok")
        self.assert_ok(result)
        self.assertEqual(result.stdout, "fake-result\n")
        self.assertEqual(result.stderr.count(NOTICE), 2)
        args = self.marker.read_text().splitlines()
        self.assertIn("--always-approve", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "off")
        self.assertNotIn("--disable-web-search", args)
        self.assertNotIn("--permission-mode", args)
        jobs = list((self.home / "jobs").glob("*/meta.json"))
        self.assertEqual(len(jobs), 1)
        meta = json.loads(jobs[0].read_text())
        self.assertEqual((meta["mode"], meta["isolation"], meta["session_mode"]),
                         ("work", "none", "single-shot"))
        job_id = jobs[0].parent.name
        for cmd, output in (("status", "done exit=0"), ("result", "fake-result")):
            shown = self.run_cli("scripts/jobs.sh", cmd, job_id)
            self.assert_ok(shown)
            self.assertLess(shown.stdout.index(NOTICE), shown.stdout.index(output))
        audit = self.run_cli("scripts/jobs.sh", "audit", "--json")
        self.assert_ok(audit)
        self.assertEqual(json.loads(audit.stdout)["failed"], 0, audit.stdout)
        self.assertIn(job_id, json.loads(audit.stdout)["passed_ids"])
        completion = json.loads((self.home / "inbox" / f"{job_id}.json").read_text())
        self.assertEqual(completion["isolation"], "none")
        env = dict(self.env, CLAUDE_PROJECT_DIR=str(self.base))
        notice = self.run_cli("hooks/report-completions.sh", input="{}", env=env)
        self.assert_ok(notice)
        self.assertLess(notice.stdout.index(NOTICE), notice.stdout.index("fake-result"))
        merged = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/dispatch.sh"), "--mode", "work",
             "--workdir", str(self.base), "--vendor", "grok", "--executor", "cli",
             "probe", "fixture task"], env=self.env, cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
        self.assertEqual(merged.returncode, 0, merged.stdout)
        self.assertEqual(merged.stdout.count(NOTICE), 2)
        self.assertLess(merged.stdout.rindex(NOTICE), merged.stdout.index("fake-result"))

    def test_on_live_and_runner_inbox_have_specific_refusal(self):
        self.enable()
        result = self.work("--vendor", "grok", "--live", "--background")
        self.assertEqual(result.returncode, 2)
        self.assertIn("single-shot work only", result.stderr)
        self.assertNotIn(REFUSAL, result.stderr)
        prompt = self.base / "prompt.md"
        prompt.write_text("fixture")
        env = dict(self.env, OMNILANE_GROK_MACOS_WORK_UNCONFINED="1", OMNILANE_INBOX="fixture")
        direct = self.run_cli("scripts/runners/run-grok.sh", "work", self.base,
                              "fixture", "-", prompt, self.base / "out.txt", env=env)
        self.assertEqual(direct.returncode, 2)
        self.assertIn("single-shot work only", direct.stderr)
        self.assertFalse(self.marker.exists())

    def test_linux_flags_and_modes_unchanged_with_option_on_or_off(self):
        self.env["FAKE_SYSTEM"] = "Linux"
        for on in (False, True):
            if on:
                self.enable()
            result = self.work("--vendor", "grok")
            self.assert_ok(result)
            args = self.marker.read_text().splitlines()
            self.assertEqual(args[args.index("--sandbox") + 1], "strict")
            self.assertIn("--disable-web-search", args)
            self.assertNotIn("--always-approve", args)
            self.assertNotIn(NOTICE, result.stderr)
            self.assertIn("vendor=grok\n", self.work("--dry-run").stdout)
        for path in (self.home / "jobs").glob("*/meta.json"):
            self.assertNotIn("isolation", json.loads(path.read_text()))

    def test_list_lint_explain_json_and_lane_extraction_with_option(self):
        before = self.dispatch("--list")
        self.enable()
        after = self.dispatch("--list")
        self.assert_ok(after)
        lanes = lambda s: [line for line in s.splitlines() if re.match(r"^[a-z][a-z0-9-]*:", line)]
        self.assertEqual(lanes(before.stdout), lanes(after.stdout))
        self.assertEqual(after.stdout.count("option.grok-macos-work:"), 1)
        self.assertIn("enabled by the operator", after.stdout)
        lint = self.dispatch("--validate")
        self.assertIn(lint.returncode, (0, 4), lint.stdout)
        self.assertNotIn("FAIL", lint.stdout)
        explain = self.dispatch("--explain", "probe")
        self.assert_ok(explain)
        self.assertIn("decision: candidate 1/2", explain.stdout)
        for command in ("--list", "--validate"):
            result = self.dispatch("--json", command)
            self.assertEqual(json.loads(result.stdout)["exit_code"], result.returncode)
        entry = self.run_cli("bin/omnilane", "list")
        self.assert_ok(entry)
        self.assertEqual(lanes(entry.stdout), lanes(after.stdout))

    def test_option_preserves_advise_sysops_and_linux_live_refusal(self):
        self.enable()
        for system in ("Darwin", "Linux"):
            self.env["FAKE_SYSTEM"] = system
            for mode in ("advise", "sysops"):
                with self.subTest(system=system, mode=mode):
                    result = self.dispatch("--mode", mode, "--vendor", "grok",
                                           "--workdir", self.base, "--executor", "cli",
                                           "probe", "fixture task")
                    self.assert_ok(result)
                    args = self.marker.read_text().splitlines()
                    self.assertNotIn(NOTICE, result.stderr)
                    if mode == "sysops":
                        self.assertIn("--always-approve", args)
                        self.assertEqual(args[args.index("--sandbox") + 1], "off")
                    else:
                        self.assertIn("dontAsk", args)
                        self.assertNotIn("--always-approve", args)
        result = self.work("--vendor", "grok", "--live", "--background")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Grok --live supports only explicit --mode sysops", result.stderr)

    def test_configure_set_get_unset_list_diff_preserve_option(self):
        self.enable()
        configure = lambda *a: self.run_cli("scripts/configure.sh", *a)
        self.assert_ok(configure("set", "triage", "codex fixture low"))
        self.assertEqual(self.local.read_text().count(OPTION), 1)
        self.assertIn("fixture", configure("get", "triage").stdout)
        self.assertIn(OPTION, configure("list").stdout)
        self.assertNotIn("option.grok", configure("diff").stdout)
        self.assert_ok(configure("unset", "triage"))
        self.assertIn(OPTION, self.local.read_text())

        first_lane = next(re.match(r"^([a-z][a-z0-9-]*):", line).group(1)
                          for line in (ROOT / "routing.yaml").read_text().splitlines()
                          if re.match(r"^[a-z][a-z0-9-]*:", line) and not line.startswith("consult:"))
        # Verify the interactive path below actually writes, not merely exits.
        menu = self.run_cli("scripts/configure.sh", input="1\n16\n\n")
        self.assert_ok(menu)
        self.assertIn(first_lane + ": off", self.local.read_text())
        self.assertIn(OPTION, self.local.read_text())

    def test_configure_menu_does_not_offer_option_and_preserves_it(self):
        self.enable()
        result = self.run_cli("scripts/configure.sh", input="1\n16\n\n")
        self.assert_ok(result)
        self.assertNotRegex(result.stdout, r"\d+\)\s+option\.grok")
        self.assertIn(OPTION, self.local.read_text())

    def test_doctor_visibility_counters_and_strict(self):
        off = self.run_cli("scripts/doctor.sh", "--json")
        self.assertIn("grok-macos-work", off.stdout)
        self.assertIn("option.grok-macos-work: off", off.stdout)
        self.assertRegex(off.stdout, r'"level":"PASS","check":"grok-macos-work"')
        self.enable()
        on = self.run_cli("scripts/doctor.sh", "--json")
        data = json.loads(on.stdout)
        self.assertIn("WARN", on.stdout)
        self.assertIn("enabled by the operator", on.stdout)
        self.assertRegex(on.stdout, r'"level":"WARN","check":"grok-macos-work"')
        self.assertNotEqual(self.run_cli("scripts/doctor.sh", "--strict").returncode, 0)
        # Counted lanes must be identical: option visibility is not another lane.
        extract = lambda s: re.findall(r"\d+ lanes parsed[^\"]*", s)
        self.assertEqual(extract(off.stdout), extract(on.stdout))
        self.assertIsInstance(data, dict)

    def test_aa_lanes_ignores_option_in_both_files(self):
        sys.path.insert(0, str(ROOT / "scripts/lib"))
        self.addCleanup(sys.path.pop, 0)
        import aa_lanes
        before = aa_lanes.lanes([self.local, ROOT / "routing.yaml"])
        self.enable()
        after = aa_lanes.lanes([self.local, ROOT / "routing.yaml"])
        self.assertEqual(before, after)
        shipped = self.base / "routing.yaml"
        shipped.write_text((ROOT / "routing.yaml").read_text() + OPTION)
        self.assertEqual(after, aa_lanes.lanes([self.local, shipped]))

    def test_bash_and_zsh_completion_readers_exclude_option(self):
        self.enable()
        env = dict(self.env, OMNILANE_COMPLETION_REPO=str(ROOT))
        for shell, source in (("/bin/bash", "omnilane.bash"), ("/bin/zsh", "_omnilane")):
            with self.subTest(shell=shell):
                if not Path(shell).exists():
                    continue
                result = subprocess.run(
                    [shell, "-c", 'source "$1"; _omnilane_lanes', "fixture",
                     str(ROOT / "completions" / source)],
                    env=env, text=True, capture_output=True, timeout=10)
                self.assert_ok(result)
                self.assertIn("probe", result.stdout.splitlines())
                self.assertIn("coding-overflow", result.stdout.splitlines())
                self.assertNotIn("option.grok-macos-work", result.stdout)

    def test_aa_rebaseline_lane_table_matrix_lanes_value(self):
        spec = importlib.util.spec_from_file_location("grok_test_rebaseline", ROOT / "scripts/aa_rebaseline.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        routing = self.base / "routing.yaml"
        routing.write_text((ROOT / "routing.yaml").read_text())
        before = module.lane_table(routing)
        routing.write_text(routing.read_text() + OPTION)
        self.enable()
        self.assertEqual(before, module.lane_table(routing))
        extract = self.base / "extract.json"
        extract.write_text('{"records":{}}')
        registry = json.loads((ROOT / "config/aa-model-policy.json").read_text())
        controller = registry["scored_configs"][0]["id"]
        commands = [
            ["matrix", "--controller", controller],
            ["lanes", "--extract", str(extract)],
            ["value", "--extract", str(extract)],
        ]
        for args in commands:
            with self.subTest(command=args[0]):
                base = [sys.executable, str(ROOT / "scripts/aa_rebaseline.py"), *args]
                original = subprocess.run(base, env=self.env, text=True, capture_output=True)
                changed = base + (["--routing", str(routing)] if args[0] != "value" else [])
                result = subprocess.run(changed, env=self.env, text=True, capture_output=True)
                self.assertEqual(original.returncode, 0, original.stderr)
                self.assertEqual((result.returncode, result.stdout), (0, original.stdout), result.stderr)

    def test_static_document_tables_and_release_manifest_ignore_option(self):
        self.enable()
        lanes = set(re.findall(r"^([a-z][a-z0-9-]*):", (ROOT / "routing.yaml").read_text(), re.M))
        self.assertNotIn("option.grok-macos-work", lanes)
        for name in ("skills/omnilane/SKILL.md", "README.md", "README.zh-TW.md",
                     "README.zh-CN.md", "README.ja.md", "README.ko.md"):
            text = (ROOT / name).read_text()
            self.assertNotRegex(text, r"(?m)^\|\s*`?option\.grok-macos-work`?\s*\|")
            self.assertIn("grok-unconfined-work.md", text)
        self.assertIn("routing.yaml", (ROOT / "scripts/release-audit.sh").read_text())


if __name__ == "__main__":
    unittest.main()
