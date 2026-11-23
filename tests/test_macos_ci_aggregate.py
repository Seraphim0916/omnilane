#!/usr/bin/env python3
"""Offline regression for the macOS CI aggregate's dependency results."""

import offline_env  # Activate suite isolation for direct file execution.
from itertools import product
import os
from pathlib import Path
import re
import subprocess
from textwrap import dedent
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ("success", "failure", "cancelled", "skipped")


class MacOSAggregateTests(unittest.TestCase):
    def aggregate_step(self):
        # Match this workflow's job/step indentation, not arbitrary YAML.
        # Fail loudly if its structure changes rather than executing a fallback.
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        job = re.search(r"(?ms)^  macos-behavior:[ \t]*\n(.*?)(?=^  [\w-]+:|\Z)", workflow)
        self.assertIsNotNone(job, "missing macos-behavior job")
        body = job.group(1)
        step = re.search(
            r"(?ms)^      - name: require both macOS suites to pass[ \t]*\n"
            r"(.*?)(?=^      - |\Z)",
            body,
        )
        self.assertIsNotNone(step, "missing macOS aggregate step")
        return body, step.group(1)

    def test_aggregate_runs_after_both_jobs_and_maps_their_results(self):
        job, step = self.aggregate_step()
        needs = re.findall(r"(?m)^    needs:[ \t]*\[([^\]\n]+)\][ \t]*$", job)
        self.assertEqual(len(needs), 1, "expected one inline macOS needs list")
        self.assertCountEqual(
            [name.strip() for name in needs[0].split(",")],
            ["macos-python", "macos-shell"],
        )
        condition = re.findall(r"(?m)^    if:[ \t]*(.*)$", job)
        self.assertIn(condition, (["always()"], ["${{ always() }}"]))
        self.assertNotRegex(job, r"(?m)^    continue-on-error:")
        self.assertNotRegex(step, r"(?m)^        (?:if|continue-on-error):")

        env_block = re.search(r"(?m)^        env:[ \t]*\n((?: {10}[^\n]+\n)+)", step)
        self.assertIsNotNone(env_block, "missing macOS aggregate result environment")
        bindings = [tuple(line.strip().split(": ", 1)) for line in env_block[1].splitlines()]
        self.assertCountEqual(bindings, [
            ("PYTHON_RESULT", "${{ needs.macos-python.result }}"),
            ("SHELL_RESULT", "${{ needs.macos-shell.result }}"),
        ])

    def test_only_two_successful_dependencies_pass_the_actual_shell(self):
        _, step = self.aggregate_step()
        block = re.search(
            r"(?m)^        run: \|[ \t]*\n((?:(?: {10}[^\n]*|[ \t]*)\n)+)",
            step,
        )
        self.assertIsNotNone(block, "missing literal macOS aggregate run block")
        script = dedent(block[1])
        self.assertTrue(script.strip(), "empty macOS aggregate run block")
        env = offline_env.fixture_environment_with_isolated_tools(
            self, {"HOME": os.environ["HOME"]}
        )
        for python_result, shell_result in product(RESULTS, repeat=2):
            with self.subTest(python_result=python_result, shell_result=shell_result):
                result = subprocess.run(
                    ["/bin/bash", "-e", "-c", script],
                    env={
                        **env,
                        "PYTHON_RESULT": python_result,
                        "SHELL_RESULT": shell_result,
                    },
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                expected = 0 if python_result == shell_result == "success" else 1
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
