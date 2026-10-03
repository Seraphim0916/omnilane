"""Import first during plain unittest discovery; guard suite-wide home isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import offline_env

ROOT = Path(__file__).resolve().parents[1]


class PythonHomeIsolationTests(unittest.TestCase):
    def test_suite_home_isolation_is_active(self):
        resolved = os.environ.get("OMNILANE_HOME") or str(Path.home() / ".omnilane")
        self.assertIsNotNone(
            getattr(offline_env, "SUITE_ROOT", None),
            "test isolation inactive: plain discovery inherited the caller's homes; "
            f"HOME={os.environ.get('HOME')!r}, OMNILANE_HOME={os.environ.get('OMNILANE_HOME')!r}, "
            f"resolved omnilane home={resolved}",
        )
        home = offline_env.SUITE_ROOT / "home"
        self.assertEqual(Path(os.environ["HOME"]), home)
        self.assertEqual(Path(os.environ["OMNILANE_HOME"]), home / ".omnilane")
        self.assertTrue((home / ".omnilane").is_dir())

    def test_child_inherits_isolated_homes(self):
        result = subprocess.run(
            [sys.executable, "-I", "-c",
             "import json,os; print(json.dumps([os.environ.get('HOME'), os.environ.get('OMNILANE_HOME')]))"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout),
                         [os.environ["HOME"], os.environ["OMNILANE_HOME"]])
        self.assertIsNotNone(getattr(offline_env, "SUITE_ROOT", None),
                             "test isolation inactive in child inheritance test")

    def test_child_with_explicit_fixture_home_is_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="omnilane-explicit-home-") as directory:
            environment = dict(os.environ, OMNILANE_HOME=directory)
            for child_env in (environment, {os.fsencode(k): os.fsencode(v)
                                            for k, v in environment.items()}):
                with self.subTest(byte_keys=b"HOME" in child_env):
                    result = subprocess.run(
                        [sys.executable, "-I", "-c", "import os; print(os.environ['OMNILANE_HOME'])"],
                        env=child_env, capture_output=True, text=True, check=True,
                    )
                    self.assertEqual(result.stdout.strip(), directory)

    def test_unset_child_omnilane_home_falls_back_only_to_private_home(self):
        environment = os.environ.copy()
        environment.pop("OMNILANE_HOME")
        result = subprocess.run(
            ["/bin/sh", "-c", 'printf "%s\\n" "${OMNILANE_HOME:-$HOME/.omnilane}"'],
            env=environment, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), os.environ["OMNILANE_HOME"])

    def test_private_directory_is_removed_at_process_exit(self):
        result = subprocess.run(
            [sys.executable, "-I", "-c",
             f"import sys; sys.path.insert(0, {str(ROOT / 'tests')!r}); "
             "import offline_env; print(offline_env.SUITE_ROOT)"],
            capture_output=True, text=True, check=True,
        )
        self.assertFalse(Path(result.stdout.strip()).exists())

    def test_tripwire_names_test_even_for_missing_paths_and_swallowed_errors(self):
        # All attempted accesses are synthetic fixtures, never the developer's home.
        with tempfile.TemporaryDirectory(prefix="omnilane-tripwire-") as directory:
            real = Path(directory) / "pre-isolation-home"
            protected = real / ".omnilane" / "synthetic.sh"
            environment = dict(os.environ, HOME=str(real), OMNILANE_HOME=str(protected.parent))
            operations = (
                "protected.exists()",
                "protected.read_text()",
                "subprocess.run([sys.executable, '-c', 'pass'], env=dict(os.environ, HOME=str(real)))",
                "subprocess.run([sys.executable, '-c', 'pass'], env=dict(os.environ, OMNILANE_HOME=str(protected.parent)))",
                "subprocess.run([sys.executable, '-c', 'pass'], env={})",
                "subprocess.run(['/bin/sh', '-c', 'cat ' + str(protected)])",
                "thread=threading.Thread(target=protected.exists); thread.start(); thread.join()",
            )
            for populated in (False, True):
                if populated:
                    protected.parent.mkdir(parents=True)
                    protected.write_text("synthetic fixture only\n")
                for operation in operations:
                    with self.subTest(populated=populated, operation=operation):
                        code = (
                            "import os,sys,subprocess,threading,unittest\nfrom pathlib import Path\n"
                            f"real=Path({str(real)!r}); protected=Path({str(protected)!r})\n"
                            f"sys.path.insert(0, {str(ROOT / 'tests')!r})\nimport offline_env\n"
                            "class RealHomeProbe(unittest.TestCase):\n"
                            " def test_real_home_read(self):\n"
                            "  try:\n"
                            f"   {operation}\n"
                            "  except AssertionError:\n   pass\n"
                            "unittest.main()\n"
                        )
                        result = subprocess.run(
                            [sys.executable, "-I", "-c", code], env=environment,
                            capture_output=True, text=True, timeout=10,
                        )
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertIn("omnilane home tripwire:", result.stderr)
                        self.assertIn("RealHomeProbe.test_real_home_read", result.stderr)

    def test_all_direct_test_modules_import_the_bootstrap(self):
        import ast
        for path in (ROOT / "tests").glob("test_*.py"):
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text())
                self.assertTrue(any(
                    isinstance(node, ast.Import) and any(alias.name == "offline_env" for alias in node.names)
                    or isinstance(node, ast.ImportFrom) and node.module == "offline_env"
                    for node in tree.body
                ), "direct test file must import offline_env before loading production code")

    def run_real_home_probe(self, body):
        # Populate before activation; every "real" path here is a private fixture.
        import textwrap
        with tempfile.TemporaryDirectory(prefix="omnilane-read-allowance-") as directory:
            real = Path(directory) / "real-home"
            protected = real / ".omnilane"
            evidence = protected / "evidence"
            evidence.mkdir(parents=True)
            (protected / "transport-contracts.local.json").write_text(
                '{"mappings": [], "unproven": []}', encoding="utf-8")
            (evidence / "probe.json").write_text("fixture evidence", encoding="utf-8")
            environment = dict(os.environ, HOME=str(real),
                               OMNILANE_HOME=str(protected),
                               OMNILANE_TEST_LIVE_OVERLAY="1")
            code = (
                "import os,sys,unittest\nfrom pathlib import Path\n"
                f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
                "import offline_env,home_isolation\n"
                f"protected=Path({str(protected)!r})\n"
                "class AllowanceProbe(unittest.TestCase):\n"
                " def test_probe(self):\n"
                + textwrap.indent(textwrap.dedent(body), "  ")
                + "\nunittest.main()\n"
            )
            return subprocess.run(
                [sys.executable, "-I", "-c", code], env=environment,
                capture_output=True, text=True, timeout=10)

    def test_real_home_allowance_reads_overlay_evidence_and_directories(self):
        result = self.run_real_home_probe("""
            with home_isolation.allow_real_omnilane_reads() as home:
                self.assertEqual(home, protected)
                self.assertNotEqual(home, Path.home() / ".omnilane")
                self.assertTrue((home / "transport-contracts.local.json").is_file())
                self.assertIn("mappings", (home / "transport-contracts.local.json").read_text())
                self.assertEqual((home / "evidence/probe.json").read_text(), "fixture evidence")
                self.assertTrue((home / "evidence").lstat())
                self.assertIn("evidence", os.listdir(home))
                with os.scandir(home / "evidence") as entries:
                    self.assertEqual([entry.name for entry in entries], ["probe.json"])
                fd = os.open(home / "evidence/probe.json", os.O_RDONLY)
                os.close(fd)
        """)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_home_allowance_refuses_mutations(self):
        operations = (
            '(home / "evidence/probe.json").write_text("not allowed")',
            'os.open(home / "new.json", os.O_RDONLY | os.O_CREAT, 0o600)',
            '(home / "evidence/probe.json").rename(home / "moved.json")',
            '(home / "evidence/probe.json").unlink()',
            '(home / "new").mkdir()',
            '(home / "evidence/probe.json").chmod(0o600)',
        )
        for operation in operations:
            with self.subTest(operation=operation):
                result = self.run_real_home_probe(
                    "with home_isolation.allow_real_omnilane_reads() as home:\n"
                    "    with self.assertRaisesRegex(AssertionError, 'tripwire'):\n"
                    f"        {operation}\n")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("tripwire", result.stderr)
                self.assertIn("OK", result.stderr)  # Immediate refusal was asserted.

    def test_live_overlay_opt_in_uses_recorded_home_and_keeps_policy_environment(self):
        result = self.run_real_home_probe("""
            import json
            import test_aa_policy as policy
            from unittest.mock import patch
            opened = []
            def load_registry(path):
                overlay = Path(os.environ["OMNILANE_AA_TRANSPORT_OVERLAY"])
                assert overlay == protected / "transport-contracts.local.json"
                assert os.environ["OMNILANE_TEST_LIVE_OVERLAY"] == "1"
                assert json.loads(overlay.read_text())["mappings"] == []
                assert (overlay.parent / "evidence/probe.json").read_text() == "fixture evidence"
                assert "evidence" in os.listdir(overlay.parent)
                opened.append(path)
                return {"_stale_transport_vendors": [], "scored_configs": []}, "fixture-sha"
            case = policy.TransportOverlayEvidenceTests(
                "test_live_overlay_loads_and_verifies_every_unstale_mapping")
            with patch.object(policy.builder, "PROVEN", []), patch.object(
                    policy.aa_policy, "load_registry", side_effect=load_registry):
                case.test_live_overlay_loads_and_verifies_every_unstale_mapping()
            self.assertEqual(opened, [policy.REGISTRY_PATH])
            print("LIVE_FIXTURE_VERIFIED")
        """)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LIVE_FIXTURE_VERIFIED", result.stdout)

    def test_real_home_allowance_does_not_leak_after_exit(self):
        result = self.run_real_home_probe("""
            with home_isolation.allow_real_omnilane_reads() as home:
                self.assertTrue(home.is_dir())
            with self.assertRaisesRegex(AssertionError, "tripwire"):
                (home / "transport-contracts.local.json").read_text()
        """)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OK", result.stderr)
        self.assertIn("tripwire", result.stderr)

    def test_another_test_cannot_inherit_real_home_allowance(self):
        result = self.run_real_home_probe("""
            class OtherTest(unittest.TestCase):
                def test_read(self):
                    protected.read_text()
            with home_isolation.allow_real_omnilane_reads():
                other = unittest.TestResult()
                OtherTest("test_read").run(other)
                self.assertEqual(len(other.failures), 1)
                self.assertIn("tripwire", other.failures[0][1])
        """)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OtherTest.test_read", result.stderr)
        self.assertIn("OK", result.stderr)

    def test_opt_in_alone_does_not_allow_real_home_reads(self):
        result = self.run_real_home_probe("""
            with self.assertRaisesRegex(AssertionError, "tripwire"):
                (protected / "transport-contracts.local.json").read_text()
        """)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OK", result.stderr)
        self.assertIn("tripwire", result.stderr)

    def test_completion_fixture_copies_the_whole_runtime_library(self):
        source = (ROOT / "tests/test_completion_idle_fixes.sh").read_text()
        fixture = source.split("test_background_worker_uses_readonly_snapshot()", 1)[1]
        self.assertIn('cp -R "$ROOT/scripts/lib/." "$fixture/scripts/lib/"', fixture)


if __name__ == "__main__":
    unittest.main()
