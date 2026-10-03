#!/usr/bin/env python3
"""Static least-privilege contract for the GitHub Actions workflow."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


class WorkflowPolicyTests(unittest.TestCase):
    def test_strict_doctor_is_an_offline_acceptance_gate(self):
        self.assertIn("Strict doctor acceptance", WORKFLOW)
        self.assertIn("bin/omnilane doctor --strict --json", WORKFLOW)
        self.assertIn("OMNILANE_HOME=", WORKFLOW)
        self.assertIn("$RUNNER_TEMP/omnilane-doctor-", WORKFLOW)

        step = WORKFLOW.split("Strict doctor acceptance", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertNotIn("--probe", step)

    def test_token_is_read_only_and_stale_runs_are_cancelled(self):
        self.assertRegex(
            WORKFLOW,
            re.compile(r"^permissions:\n  contents: read$", re.MULTILINE),
        )

    def test_third_party_actions_are_pinned_to_full_commits(self):
        uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", WORKFLOW, re.MULTILINE)
        self.assertTrue(uses, "workflow should contain at least one action")
        for action in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertRegex(
            WORKFLOW,
            re.compile(
                r"^concurrency:\n"
                r"  group: \$\{\{ github\.workflow \}\}-\$\{\{ github\.ref \}\}\n"
                r"  cancel-in-progress: true$",
                re.MULTILINE,
            ),
        )

    def job(self, name):
        match = re.search(
            r"^  " + re.escape(name) + r":\n(.*?)(?=^  [\w-]+:|\Z)",
            WORKFLOW,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "missing job: " + name)
        return match.group(1)

    def test_shellcheck_is_verified_pinned_and_printed_before_lint(self):
        job = self.job("static-checks")
        commands = [
            'archive="$RUNNER_TEMP/shellcheck-v0.11.0.linux.x86_64.tar.xz"',
            "https://github.com/koalaman/shellcheck/releases/download/v0.11.0/"
            "shellcheck-v0.11.0.linux.x86_64.tar.xz",
            'echo "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198  $archive"'
            " | sha256sum --check --strict",
            'tar -xJf "$archive" -C "$RUNNER_TEMP"',
            'echo "$RUNNER_TEMP/shellcheck-v0.11.0" >> "$GITHUB_PATH"',
            "shellcheck --version",
            "shellcheck -S warning bin/omnilane scripts/*.sh scripts/lib/*.sh "
            "scripts/runners/*.sh install.sh",
        ]
        positions = []
        for command in commands:
            self.assertRegex(job, r"(?m)^ {10,12}" + re.escape(command) + r"(?: \\)?$")
            positions.append(job.index(command))
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(job, r"(?m)^          curl --fail --silent --show-error --location \\$")
        self.assertIn("# ShellCheck 0.11.0:", job)
        self.assertNotRegex(job, r"(?m)^\s*(?:if|continue-on-error):")
        self.assertNotIn("|| true", job)

    def test_python_units_cover_supported_floor_and_current_stable(self):
        job = self.job("python-unit")
        self.assertRegex(job, r'(?m)^        python-version: \["3.9", "3.14"\]$')
        self.assertNotRegex(job, r"(?m)^\s*(?:include|exclude):")
        self.assertRegex(job, r"(?m)^          python-version: \$\{\{ matrix\.python-version \}\}$")
        self.assertRegex(job, r"(?m)^      fail-fast: false$")
        self.assertRegex(job, r"(?m)^        run: python -m unittest discover -s tests -p 'test_\*\.py'$")
        self.assertEqual(job.count("- name: Python unit tests"), 1)

    def test_unit_jobs_are_independent_unconditional_failure_gates(self):
        for name in ("python-unit", "shell-unit"):
            with self.subTest(job=name):
                job = self.job(name)
                self.assertNotRegex(job, r"(?m)^\s*(?:needs|if|continue-on-error):")
                self.assertNotRegex(job, r"(?m)^\s*(?:shell|defaults):")
                self.assertNotIn("||", job)
                self.assertNotIn("set +e", job)
                self.assertNotIn("shellcheck", job)
                self.assertNotIn("Strict doctor acceptance", job)
                # Before the test, only checkout and Python setup may run.
                self.assertEqual(len(re.findall(r"(?m)^\s+run:", job)), 1)
                self.assertEqual(len(re.findall(r"(?m)^\s+- uses:", job)), 1)
                self.assertEqual(len(re.findall(r"(?m)^\s+uses:", job)), 1)
                self.assertRegex(job, r"(?m)^      - uses: actions/checkout@[0-9a-f]{40}(?: #.*)?$")
                self.assertRegex(job, r"(?m)^        uses: actions/setup-python@[0-9a-f]{40}(?: #.*)?$")
        shell = self.job("shell-unit")
        self.assertRegex(shell, r"(?m)^            bash tests/run\.sh$")
        self.assertIn("OMNILANE_DEPTH=1 OMNILANE_TIMEOUT=1800 OMNILANE_TIMEOUT_PROBE=99", shell)
        static = self.job("static-checks")
        for name in ("bash syntax", "perl syntax", "shellcheck", "Python syntax", "no stale project name"):
            self.assertIn("- name: " + name, static)
        self.assertNotIn("Python unit tests", static)
        self.assertNotIn("shell unit tests", static)
        self.assertNotIn("needs:", self.job("smoke"))


if __name__ == "__main__":
    unittest.main()
