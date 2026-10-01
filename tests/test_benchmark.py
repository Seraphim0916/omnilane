import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "scripts" / "benchmark.py"


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="omnilane-benchmark-test-")
        self.root = Path(self.tempdir.name)
        self.repo = self.root / "repo"
        (self.repo / "scripts" / "runners").mkdir(parents=True)
        self.marker = self.root / "provider-invoked"
        self.listing = self.root / "routing-list.txt"
        self.listing.write_text("quality: codex fake-model medium\n", encoding="utf-8")
        dispatch = self.repo / "scripts" / "dispatch.sh"
        dispatch.write_text(
            """#!/usr/bin/env bash
if [[ "${1:-}" == "--list" ]]; then
  cat "$OMNILANE_TEST_BENCHMARK_LISTING"
  exit 0
fi
if [[ "$*" == *'--dry-run'* && "$*" == *'--vendor codex'* ]]; then
  printf 'dry_run=yes\\nlane=quality\\nvendor=codex\\nmodel=fake-model\\neffort=medium\\nprovider_invoked=no\\n'
  exit 0
fi
exit 4
""",
            encoding="utf-8",
        )
        runner = self.repo / "scripts" / "runners" / "run-codex.sh"
        runner.write_text(
            """#!/usr/bin/env bash
printf invoked >> "$OMNILANE_TEST_BENCHMARK_MARKER"
if grep -q ALPHA "$5"; then printf 'ALPHA\\n' > "$6"; else printf 'WRONG\\n' > "$6"; fi
""",
            encoding="utf-8",
        )
        dispatch.chmod(0o755)
        runner.chmod(0o755)
        self.workloads = self.root / "workloads.tsv"
        self.workloads.write_text(
            "alpha\tquality\t1\t^ALPHA\\s*$\tReply exactly ALPHA.\n"
            "beta\tquality\t1\t^BETA\\s*$\tReply exactly BETA.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def run_benchmark(self, *args, discover_vendors=False):
        env = os.environ.copy()
        env.update(
            {
                "OMNILANE_BENCHMARK_REPO": str(self.repo),
                "OMNILANE_TEST_BENCHMARK_MARKER": str(self.marker),
                "OMNILANE_TEST_BENCHMARK_LISTING": str(self.listing),
            }
        )
        vendor_args = [] if discover_vendors else ["--vendor", "codex"]
        return subprocess.run(
            ["python3", str(BENCHMARK), "--json", *vendor_args,
             "--workloads", str(self.workloads), "--cost-per-call", "codex=0.50", *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )

    def test_default_is_dry_run_and_cost_plan_is_transparent(self):
        result = self.run_benchmark()
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["provider_invoked"])
        self.assertEqual("dry-run", report["mode"])
        self.assertEqual(2, report["workload_count"])
        self.assertFalse(self.marker.exists())
        vendor = report["vendors"][0]
        self.assertEqual(2, vendor["planned_calls"])
        self.assertEqual("0.50", vendor["cost"]["per_call_usd"])
        self.assertEqual("1.00", vendor["cost"]["estimated_total_usd"])
        self.assertTrue(all(item["status"] == "planned" for item in vendor["workloads"]))

    def test_default_vendor_discovery_uses_available_vendor_rows(self):
        result = self.run_benchmark(discover_vendors=True)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(["codex"], [vendor["vendor"] for vendor in report["vendors"]])
        self.assertEqual("dry-run", report["mode"])
        self.assertFalse(report["provider_invoked"])
        self.assertFalse(self.marker.exists())
        self.assertEqual(["planned", "planned"], [
            item["status"] for item in report["vendors"][0]["workloads"]
        ])

    def test_default_vendor_discovery_ignores_unavailable_rows(self):
        self.listing.write_text(
            "quality: codex fake-model medium\n"
            "absent: unavailable   # no vendor CLI found in chain\n",
            encoding="utf-8",
        )
        result = self.run_benchmark(discover_vendors=True)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(["codex"], [vendor["vendor"] for vendor in report["vendors"]])
        self.assertFalse(report["provider_invoked"])
        self.assertFalse(self.marker.exists())
        self.assertEqual(["planned", "planned"], [
            item["status"] for item in report["vendors"][0]["workloads"]
        ])

    def test_default_vendor_discovery_errors_when_all_lanes_unavailable(self):
        self.listing.write_text(
            "absent: unavailable   # no vendor CLI found in chain\n",
            encoding="utf-8",
        )
        result = self.run_benchmark(discover_vendors=True)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertIn("no configured benchmark vendors found", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_list_routes_retains_unavailable_lanes(self):
        self.listing.write_text(
            "quality: codex fake-model medium\n"
            "absent: unavailable   # no vendor CLI found in chain\n",
            encoding="utf-8",
        )
        benchmark = runpy.run_path(str(BENCHMARK))
        env = os.environ.copy()
        env["OMNILANE_TEST_BENCHMARK_LISTING"] = str(self.listing)
        lanes, _ = benchmark["list_routes"](self.repo / "scripts" / "dispatch.sh", env)
        self.assertEqual(["quality", "absent"], lanes)
        self.assertFalse(self.marker.exists())

    def test_explicit_run_scores_quality_without_returning_bodies(self):
        result = self.run_benchmark("--run")
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["provider_invoked"])
        self.assertEqual("run", report["mode"])
        self.assertEqual("invokedinvoked", self.marker.read_text(encoding="utf-8"))
        vendor = report["vendors"][0]
        self.assertEqual(1, vendor["passed"])
        self.assertEqual(2, vendor["score_possible"])
        self.assertEqual(1, vendor["score_earned"])
        self.assertEqual(50, vendor["quality_percent"])
        self.assertEqual("1.00", vendor["cost"]["estimated_total_usd"])
        serialized = json.dumps(report)
        self.assertNotIn("ALPHA\\n", serialized)
        self.assertNotIn("WRONG", serialized)


if __name__ == "__main__":
    unittest.main()
