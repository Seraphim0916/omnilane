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
