"""Command discovery reads only ordinary offline fixture names and CLI help."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
TOP_COMMANDS = set("version list route dispatch goal jobs mcp completion release-audit doctor which-bin whoami native-context resign benchmark ui configure help".split())
GOAL_COMMANDS = set("open dispatch note status close".split())
JOB_COMMANDS = set("list status result complete-native tail send watch close retry stats recommend wait cancel rm threads audit prune help".split())
ID_COMMANDS = set("status result complete-native tail send watch close retry wait cancel rm".split())


class CommandCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-command-completion-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env, violations = isolated_environment(self.root / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home = self.root / "records"
        self.job = "20261001-000000-123-1"
        self.goal = "20261001-000000-123-2"
        (self.home / "jobs" / self.job).mkdir(parents=True)
        (self.home / "goals" / self.goal).mkdir(parents=True)
        (self.home / "jobs" / self.job / "meta.json").write_text('{"lane":"fixture","vendor":"exec"}\n')
        (self.home / "goals" / self.goal / "goal.txt").write_text("Ordinary fixture goal\n")
        (self.home / "goals" / self.goal / "budget.json").write_text('{"jobs":"unlimited","seconds":"unlimited"}\n')
        (self.home / "routing.local.yaml").write_text("fixture: exec /bin/true -\n")
        self.env.update(OMNILANE_HOME=str(self.home), OMNILANE_COMPLETION_REPO=str(ROOT))

    def run_command(self, argv, expected=0):
        result = subprocess.run(argv, env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def complete(self, shell, *words):
        if not shutil.which(shell, path=self.env["PATH"]):
            self.skipTest(shell + " is unavailable")
        if shell == "bash":
            script = '''source "$1"
shift
COMP_WORDS=(omnilane "$@")
COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
_omnilane
printf '%s\\n' "${COMPREPLY[@]}"
'''
            source = "omnilane.bash"
        else:
            # Exercise actual zsh branching and helpers; capture the contracts
            # passed to its standard completion primitives, as adjacent tests do.
            script = '''source "$1"
shift
_values() { shift; print -l -- "$@"; }
_arguments() { print -l -- "$@"; }
words=(omnilane "$@")
CURRENT=${#words}
_omnilane
'''
            source = "_omnilane"
        output = self.run_command([shell, "-c", script, "_", str(ROOT / "completions" / source), *words])
        return set(filter(None, output.splitlines()))

    def test_inventory_matches_actual_help_and_entrypoint(self):
        help_text = self.run_command(["bash", str(ROOT / "bin/omnilane"), "help"])
        documented = set(re.findall(r"^  omnilane ([a-z-]+)", help_text, re.M))
        self.assertEqual(TOP_COMMANDS, documented | {"dispatch", "help"})
        goal_help = self.run_command(["bash", str(ROOT / "bin/omnilane"), "goal", "help"], expected=2)
        self.assertEqual(set(re.findall(r"omnilane goal ([a-z-]+)", goal_help)), GOAL_COMMANDS)
        job_help = self.run_command(["bash", str(ROOT / "bin/omnilane"), "jobs", "help"])
        self.assertEqual(set(re.findall(r"\|([a-z-]+)(?= |\n|$)", job_help)) | {"list"}, JOB_COMMANDS)

    def test_bash_top_level_and_shell_inventory(self):
        self.assertEqual(self.complete("bash", ""), TOP_COMMANDS)
        self.assertEqual(self.complete("bash", "completion", ""), {"bash", "zsh", "fish"})

    def test_zsh_top_level_and_shell_inventory(self):
        self.assertEqual(self.complete("zsh", ""), TOP_COMMANDS)
        self.assertEqual(self.complete("zsh", "completion", ""), {"bash", "zsh", "fish"})

    def test_bash_goal_hierarchy_and_ids(self):
        self.check_goal_hierarchy("bash")

    def test_zsh_goal_hierarchy_and_ids(self):
        self.check_goal_hierarchy("zsh")

    def check_goal_hierarchy(self, shell):
        self.assertEqual(self.complete(shell, "goal", ""), GOAL_COMMANDS)
        for action in GOAL_COMMANDS - {"open"}:
            with self.subTest(action=action):
                self.assertEqual(self.complete(shell, "goal", action, ""), {self.goal})
        self.assertEqual(self.complete(shell, "goal", "note", self.goal, ""), set())
        self.assertEqual(self.complete(shell, "goal", "status", self.goal, ""), set())

    def test_bash_goal_flags_and_dispatch_boundary(self):
        self.assertEqual(self.complete("bash", "goal", "open", "A goal", ""),
                         {"--budget-jobs", "--budget-seconds", "--workdir"})
        self.assertEqual(self.complete("bash", "goal", "open", "A goal", "--budget-jobs", ""), set())
        self.assertEqual(self.complete("bash", "goal", "close", self.goal, ""), {"--summary"})
        self.assertEqual(self.complete("bash", "goal", "close", self.goal, "--summary", ""), set())
        flags = self.complete("bash", "goal", "dispatch", self.goal, "")
        self.assertIn("fixture", flags)
        self.assertIn("--thread", flags)
        self.assertNotIn("--dry-run", flags)
        self.assertNotIn("--help", flags)
        self.assertIn("--dry-run", self.complete("bash", "dispatch", ""))
        self.assertIn("--help", self.complete("bash", "dispatch", ""))
        self.assertEqual(self.complete("bash", "goal", "dispatch", self.goal, "--mode", ""), {"advise", "work"})

    def test_zsh_goal_flags_and_dispatch_boundary(self):
        opened = "\n".join(self.complete("zsh", "goal", "open", "A goal", ""))
        for flag in ("--budget-jobs[", "--budget-seconds[", "--workdir["):
            self.assertIn(flag, opened)
        self.assertIn("--summary[", "\n".join(self.complete("zsh", "goal", "close", self.goal, "")))
        flags = "\n".join(self.complete("zsh", "goal", "dispatch", self.goal, ""))
        self.assertIn("fixture", flags)
        self.assertIn("--thread[", flags)
        self.assertNotIn("--dry-run[", flags)
        self.assertIn("--dry-run[", "\n".join(self.complete("zsh", "dispatch", "")))

    def test_zsh_goal_arguments_receive_the_right_position(self):
        if not shutil.which("zsh", path=self.env["PATH"]):
            self.skipTest("zsh is unavailable")
        script = '''source "$1"
shift
_arguments() { print -r -- "$CURRENT"; print -l -- "${words[@]}"; }
words=(omnilane "$@")
CURRENT=${#words}
_omnilane
'''
        for args, expected in ((["goal", "dispatch", self.goal, "--mode", ""], ["3", "omnilane", "--mode", ""]),
                               (["goal", "open", "A goal", "--budget-jobs", ""], ["4", "omnilane", "A goal", "--budget-jobs", ""]),
                               (["goal", "close", self.goal, "--summary", ""], ["4", "omnilane", self.goal, "--summary", ""])):
            with self.subTest(args=args):
                output = self.run_command(["zsh", "-c", script, "_", str(ROOT / "completions/_omnilane"), *args])
                self.assertEqual(output.splitlines(), expected)

    def test_completion_leaves_ordinary_records_unchanged(self):
        def snapshot():
            return {str(path.relative_to(self.home)): path.read_bytes()
                    for path in self.home.rglob("*") if path.is_file()}
        before = snapshot()
        for shell in ("bash", "zsh"):
            if shutil.which(shell, path=self.env["PATH"]):
                for command in ID_COMMANDS:
                    self.complete(shell, "jobs", command, "")
                for command in GOAL_COMMANDS:
                    self.complete(shell, "goal", command, "")
        self.assertEqual(snapshot(), before)

    def test_bash_new_file_and_directory_arguments(self):
        completion = self.root / "ordinary completion.json"
        completion.write_text('{"ok":true}\n')
        directory = self.root / "ordinary workdir"
        directory.mkdir()
        self.assertEqual(self.complete("bash", "jobs", "complete-native", self.job, str(completion)[:-5]),
                         {str(completion)})
        self.assertEqual(self.complete("bash", "goal", "open", "A goal", "--workdir", str(directory)[:-3]),
                         {str(directory)})

    def test_bash_jobs_hierarchy_and_ids(self):
        self.check_jobs_hierarchy("bash")

    def test_zsh_jobs_hierarchy_and_ids(self):
        self.check_jobs_hierarchy("zsh")

    def check_jobs_hierarchy(self, shell):
        for prefix in (("jobs",), ("jobs", "--json")):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.complete(shell, *prefix, ""), JOB_COMMANDS)
                self.assertEqual(self.complete(shell, *prefix, "threads", ""), {"list", "show", "rm"})
        self.assertEqual(self.complete(shell, "jobs", "threads", "--json", ""), {"list", "show", "rm"})
        for action in ID_COMMANDS:
            with self.subTest(action=action):
                self.assertEqual(self.complete(shell, "jobs", action, ""), {self.job})
        for action in ("list",):
            flags = "\n".join(self.complete(shell, "jobs", "threads", action, ""))
            self.assertIn("--json", flags)
        flags = "\n".join(self.complete(shell, "jobs", "threads", "show", "fixture-thread", ""))
        self.assertIn("--json", flags)
        self.assertEqual(self.complete(shell, "jobs", "threads", "rm", "fixture-thread", ""), set())

    def test_fish_declares_complete_static_hierarchy(self):
        source = (ROOT / "completions/omnilane.fish").read_text()
        top = set(re.findall(r"-n __fish_use_subcommand -a ([a-z-]+)", source))
        self.assertEqual(top, TOP_COMMANDS)
        declarations = [shlex.split(line.replace("\\\n", " "))
                        for line in source.replace("\\\n", " ").splitlines()
                        if line.startswith("complete ")]
        def candidates(label):
            return set(next(parts[parts.index("-a") + 1].split() for parts in declarations
                            if "-d" in parts and parts[parts.index("-d") + 1] == label))
        self.assertEqual(candidates("jobs subcommand"), JOB_COMMANDS)
        self.assertEqual(candidates("goal subcommand"), GOAL_COMMANDS)
        self.assertEqual(candidates("thread action"), {"list", "show", "rm"})
        goal_options = [parts for parts in declarations if "-n" in parts and
                        "goal" in parts[parts.index("-n") + 1] and "-l" in parts]
        self.assertTrue({"budget-jobs", "budget-seconds", "workdir", "summary"}.issubset(
            {parts[parts.index("-l") + 1] for parts in goal_options}))
        dry_run = next(parts for parts in declarations if "-l" in parts and parts[parts.index("-l") + 1] == "dry-run")
        self.assertEqual(dry_run[dry_run.index("-n") + 1], "__omnilane_command_path route; or __omnilane_command_path dispatch")
        status = next(parts for parts in declarations if "-l" in parts and parts[parts.index("-l") + 1] == "status")
        self.assertEqual(status[status.index("-n") + 1], "__omnilane_command_path jobs list")

    def test_fish_runtime_when_available(self):
        if not shutil.which("fish", path=self.env["PATH"]):
            self.skipTest("fish unavailable; static declaration checks only")
        source = ROOT / "completions/omnilane.fish"
        self.run_command(["fish", "-n", str(source)])
        for command, expected in (("omnilane ", TOP_COMMANDS),
                                  ("omnilane goal ", GOAL_COMMANDS),
                                  ("omnilane jobs ", JOB_COMMANDS),
                                  ("omnilane jobs threads ", {"list", "show", "rm"})):
            output = self.run_command(["fish", "-c", 'source $argv[1]; complete -C "$argv[2]"', str(source), command])
            self.assertEqual({line.split("\t")[0] for line in output.splitlines() if not line.startswith("-")}, expected)
        output = self.run_command(["fish", "-c", 'source $argv[1]; complete -C "$argv[2]"', str(source),
                                   "omnilane goal dispatch " + self.goal + " --"])
        self.assertNotIn("--dry-run", output)
        self.assertIn("--mode", output)
        output = self.run_command(["fish", "-c", 'source $argv[1]; complete -C "$argv[2]"', str(source),
                                   "omnilane jobs threads list --"])
        self.assertIn("--json", output)
        self.assertNotIn("--status", output)
        self.assertNotIn("--lane", output)

    def test_available_shell_syntax(self):
        for shell, source in (("bash", "omnilane.bash"), ("zsh", "_omnilane")):
            if shutil.which(shell, path=self.env["PATH"]):
                self.run_command([shell, "-n", str(ROOT / "completions" / source)])


if __name__ == "__main__":
    unittest.main()
