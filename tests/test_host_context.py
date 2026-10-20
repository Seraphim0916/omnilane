"""Host-written capability files: harness signals, configured model, aliases, install and verification."""

from __future__ import annotations
import offline_env  # noqa: F401
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".sandbox-tmp"
sys.path.insert(0, str(ROOT / "scripts/lib"))
import host_context

REGISTRY = str(ROOT / "config/aa-model-policy.json")
HARNESS_VARIABLES = ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


class Fixture(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.codex = self.base / "codex"
        self.claude = self.base / "claude"
        self.codex.mkdir()
        self.claude.mkdir()
        env = patch.dict(os.environ, {"OMNILANE_HOME": str(self.base / "home"),
                                      "CODEX_HOME": str(self.codex), "CLAUDE_CONFIG_DIR": str(self.claude)})
        env.start()
        self.addCleanup(env.stop)
        for name in HARNESS_VARIABLES:
            os.environ.pop(name, None)
        self.lines = []

    def codex_config(self, text):
        (self.codex / "config.toml").write_text(text)

    def claude_settings(self, value):
        (self.claude / "settings.json").write_text(json.dumps(value))

    def install(self, *harnesses):
        code, written = host_context.install(list(harnesses or host_context.SUPPORTED), REGISTRY,
                                             log=self.lines.append)
        self.written = written
        return code

    def host(self, harness):
        return host_context.host_dir() / f"{harness}.json"


class HarnessSignalTests(unittest.TestCase):
    def test_each_harness_is_told_apart_by_its_own_variables(self):
        for env, expected in (({"CODEX_THREAD_ID": "t"}, ["codex"]), ({"CODEX_SANDBOX": "seatbelt"}, ["codex"]),
                              ({"CLAUDECODE": "1"}, ["claude-code"]),
                              ({"CLAUDE_CODE_ENTRYPOINT": "cli"}, ["claude-code"]),
                              ({"CLAUDECODE": "0"}, []), ({}, [])):
            with self.subTest(env=env):
                self.assertEqual(host_context.harness_signals(env), expected)

    def test_no_signal_or_two_signals_is_a_refusal(self):
        with self.assertRaisesRegex(host_context.HostContextError, "no harness signal"):
            host_context.detect_harness({})
        with self.assertRaisesRegex(host_context.HostContextError, "more than one harness"):
            host_context.detect_harness({"CODEX_THREAD_ID": "t", "CLAUDECODE": "1"})


class ConfiguredModelTests(Fixture):
    def test_codex_profile_wins_over_the_top_level_model(self):
        self.codex_config('model = "gpt-6-astra"\nprofile = "fast"\n[profiles.fast]\nmodel = "gpt-6-luna"\n')
        self.assertEqual(host_context.configured_model("codex")[0], "gpt-6-luna")
        self.codex_config('model = "gpt-6-astra"\n')
        self.assertEqual(host_context.configured_model("codex"), (
            "gpt-6-astra", {"file": str(self.codex / "config.toml"), "key": "model"}))

    def test_the_pre_3_11_reader_finds_the_same_keys(self):
        text = ('# comment\nmodel = "gpt-6-astra"  # trailing\nprofile = "fast"\n'
                '[mcp_servers.x]\nmodel = "not-this"\n[profiles.fast]\nmodel = "gpt-6-luna"\n')
        value = host_context._toml_fallback(text)
        self.assertEqual((value["model"], value["profile"], value["profiles"]["fast"]["model"]),
                         ("gpt-6-astra", "fast", "gpt-6-luna"))

    def test_claude_env_model_wins_over_the_settings_model(self):
        self.claude_settings({"model": "opus[1m]", "env": {"ANTHROPIC_MODEL": "claude-sonnet-5-5"}})
        self.assertEqual(host_context.configured_model("claude-code")[0], "claude-sonnet-5-5")

    def test_an_alias_maps_and_an_unknown_name_is_refused(self):
        registry, _ = host_context.caller_identity.load_registry(REGISTRY)
        aliases = host_context.load_aliases()
        self.assertEqual(host_context.exact_model("claude-code", "opus[1m]", registry, aliases),
                         ("claude-opus-5-5", "opus[1m]"))
        self.assertEqual(host_context.exact_model("codex", "gpt-6-astra", registry, aliases),
                         ("gpt-6-astra", None))
        with self.assertRaisesRegex(host_context.HostContextError, "has no mapping"):
            host_context.exact_model("claude-code", "sonnet", registry, aliases)
        with self.assertRaisesRegex(host_context.HostContextError, "does not score"):
            host_context.exact_model("claude-code", "opus[1m]", registry, {"claude-code": {"opus[1m]": "nope"}})


class InstallTests(Fixture):
    def test_install_writes_private_files_with_a_record(self):
        self.codex_config('model = "gpt-6-astra"\n')
        self.claude_settings({"model": "opus[1m]"})
        self.assertEqual(self.install(), 0, self.lines)
        claude = json.loads(self.host("claude-code").read_text())
        self.assertEqual((claude["current_model"], claude["source"], claude["caller_identity_verified"],
                          claude["source_detail"]["alias"]), ("claude-opus-5-5", "host-config", False, "opus[1m]"))
        self.assertNotIn("workdirs", json.dumps(claude))
        for harness in host_context.SUPPORTED:
            self.assertEqual(self.host(harness).stat().st_mode & 0o777, 0o600)
            self.assertEqual(host_context.verify(harness)["harness"], harness)

    def test_install_refuses_inside_a_harness(self):
        self.codex_config('model = "gpt-6-astra"\n')
        os.environ["CLAUDECODE"] = "1"
        self.assertEqual(self.install(), 2)
        self.assertIn("not a security boundary", self.lines[0])
        self.assertFalse(host_context.host_dir().exists())

    def test_a_missing_config_is_skipped_and_an_unmapped_model_writes_nothing(self):
        self.claude_settings({"model": "sonnet"})
        self.assertEqual(self.install(), 3)
        self.assertTrue(any("codex: skipped" in line for line in self.lines), self.lines)
        self.assertTrue(any("claude-code: not written" in line and "no mapping" in line
                            for line in self.lines), self.lines)
        self.assertFalse(self.host("claude-code").exists())
        self.codex_config('model = "gpt-6-astra"\n')
        self.assertEqual(self.install(), 1)
        self.assertTrue(self.host("codex").exists())


class VerifyTests(Fixture):
    def setUp(self):
        super().setUp()
        self.codex_config('model = "gpt-6-astra"\n')
        self.assertEqual(self.install("codex"), 0, self.lines)
        self.path = self.host("codex")

    def test_same_size_edits_and_touched_times_are_both_seen(self):
        info = self.path.stat()
        text = self.path.read_text()
        self.path.write_text(text.replace("gpt-6-astra", "gpt-6-astrb"))
        os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(host_context.HostContextError, "changed after it was installed"):
            host_context.verify("codex")
        self.path.write_text(text)
        with self.assertRaisesRegex(host_context.HostContextError, "changed after it was installed"):
            host_context.verify("codex")

    def test_permissions_links_and_a_missing_record_are_refused(self):
        os.chmod(self.path, 0o620)
        with self.assertRaisesRegex(host_context.HostContextError, "group/world-writable"):
            host_context.verify("codex")
        os.chmod(self.path, 0o600)
        record = self.path.with_name("codex.install.json")
        record.rename(self.base / "record")
        with self.assertRaisesRegex(host_context.HostContextError, "missing or unreadable"):
            host_context.verify("codex")
        (self.base / "record").rename(record)
        real = self.base / "real.json"
        self.path.rename(real)
        self.path.symlink_to(real)
        with self.assertRaisesRegex(host_context.HostContextError, "not a regular file"):
            host_context.verify("codex")
        with self.assertRaisesRegex(host_context.HostContextError, "no host capability file for claude-code"):
            host_context.verify("claude-code")

    def test_the_context_gets_this_calls_workdir_and_mode_in_memory(self):
        context, value = host_context.build_context(str(self.base), "work", {"CODEX_THREAD_ID": "t"})
        self.assertEqual(value["current_model"], "gpt-6-astra")
        cap = context["capabilities"][0]
        self.assertEqual((cap["model"], cap["modes"], cap["workdirs"], cap["efforts"]),
                         ("gpt-6-astra", ["work"], [str(self.base.resolve())], ["unverified"]))
        self.assertEqual((context["inherits_caller_runtime"], context["caller_identity_verified"]), (True, False))
        with self.assertRaisesRegex(host_context.HostContextError, "no inherited worker"):
            host_context.build_context(str(self.base), "sysops", {"CODEX_THREAD_ID": "t"})


class CommandLineTests(Fixture):
    def run_main(self, *argv):
        import contextlib
        import io
        import native_context
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = native_context.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_install_and_resolve_reject_flags_that_do_not_apply(self):
        for argv in (("--install", "--workdir", str(self.base)), ("--install", "--mode", "work"),
                     ("--install", "--vendor", "codex", "--model", "gpt-6-astra"),
                     ("--install", "--harness", "grok-build"), ("--install", "--resolve-host"),
                     ("--resolve-host",), ("--resolve-host", "--workdir", str(self.base))):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_main(*argv)[0], 2)

    def test_install_prints_the_files_and_resolve_prints_the_host_file(self):
        self.codex_config('model = "gpt-6-astra"\n')
        code, out, _ = self.run_main("--install", "--harness", "codex")
        self.assertEqual((code, out.strip()), (0, str(self.host("codex"))))
        os.environ["CODEX_THREAD_ID"] = "t"
        code, out, _ = self.run_main("--resolve-host", "--workdir", str(self.base), "--mode", "advise")
        self.assertEqual((code, out.strip()), (0, str(self.host("codex"))))
        os.environ["CLAUDECODE"] = "1"
        code, _, err = self.run_main("--resolve-host", "--workdir", str(self.base), "--mode", "advise")
        self.assertEqual(code, 3)
        self.assertIn("more than one harness", err)


if __name__ == "__main__":
    unittest.main()
