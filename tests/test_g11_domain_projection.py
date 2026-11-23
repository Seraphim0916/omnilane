"""Public CLI contract for the stage-A domain projection."""

import offline_env  # Activate suite isolation before production imports.

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/aa_domain_projection.py"
REGISTRY = ROOT / "config/aa-model-policy.json"
COMPANION = ROOT / "config/aa-domain-strength.json"
EXTRACT = ROOT / "docs/reports/aa-v5.0.1-extract-2026-11-19.json"
BUILDER = ROOT / "scripts/aa_rebaseline.py"


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_script(script, *args):
    return subprocess.run([sys.executable, "-B", str(script), *map(str, args)],
                          capture_output=True, check=False, cwd=ROOT)


class ProjectionTests(unittest.TestCase):
    def test_without_companion_preserves_rows_and_ceiling(self):
        proc = run_script(CLI, "--registry", REGISTRY)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(proc.stderr, b"")
        projection = json.loads(proc.stdout)
        registry = json.loads(REGISTRY.read_bytes())
        self.assertEqual(projection["scored_configs"], registry["scored_configs"])
        self.assertEqual(projection["policy"], registry["policy"])
        self.assertIs(projection["dispatch_eligible"], False)
        self.assertIsNone(projection["companion_sha256"])

    def setUp(self):
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(exist_ok=True)
        self.directory = Path(self.enter_temp_dir(scratch))

    def enter_temp_dir(self, scratch):
        directory = tempfile.TemporaryDirectory(prefix="g11-projection-", dir=scratch)
        self.addCleanup(directory.cleanup)
        return directory.name

    def pointer_fixture(self):
        registry = json.loads(REGISTRY.read_bytes())
        companion = json.loads(COMPANION.read_bytes())
        seed = companion["measurements"][0]
        companion["measurements"].extend(
            [dict(seed, evidence_kind="estimated"), dict(seed, evidence_kind="derived")]
        )
        path = write_json(self.directory / "companion.json", companion)
        pointer = {
            "path": path.relative_to(ROOT).as_posix(),
            "schema_version": 1,
            "sha256": sha256(path.read_bytes()),
        }
        registry["snapshot"]["domain_strength"] = pointer
        return registry, path

    def test_pointed_projection_preserves_rows_and_input_bytes(self):
        registry, companion = self.pointer_fixture()
        registry_path = write_json(self.directory / "registry.json", registry)
        before = {f: sha256(f.read_bytes()) for f in (registry_path, companion, EXTRACT)}
        one = run_script(CLI, "--registry", registry_path)
        two = run_script(CLI, "--registry", registry_path, "--companion", companion)
        self.assertEqual(one.returncode, 0, one.stderr.decode())
        self.assertEqual(two.returncode, 0, two.stderr.decode())
        self.assertEqual(one.stdout, two.stdout)
        self.assertEqual(before, {f: sha256(f.read_bytes()) for f in before})
        evidence = json.loads(companion.read_bytes())
        expected = {
            "schema_version": 1,
            "snapshot_id": registry["snapshot"]["id"],
            "registry_sha256": evidence["registry_sha256"],
            "companion_sha256": before[companion],
            "dispatch_eligible": False,
            "policy": registry["policy"],
            "scored_configs": registry["scored_configs"],
            "measurements": {kind: [r for r in evidence["measurements"] if r["evidence_kind"] == kind]
                             for kind in ("measured", "estimated", "derived", "unknown")},
            "vendor_claims": evidence["vendor_claims"],
        }
        serialized = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(one.stdout, (serialized + "\n").encode("utf-8"))
        self.assertTrue(all(expected["measurements"].values()))
        self.assertIsNone(expected["measurements"]["unknown"][0]["value"])

    def test_conflicting_observation_never_emits_projection(self):
        companion = json.loads(COMPANION.read_bytes())
        row = companion["measurements"][0]
        companion["measurements"].append(dict(row, value=0 if row["value"] else 1))
        path = write_json(self.directory / "conflict.json", companion)
        result = run_script(CLI, "--registry", REGISTRY, "--companion", path)
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"conflicting values", result.stderr)

    def project_source_pair(self, first, second):
        companion = json.loads(COMPANION.read_bytes())
        seed = companion["measurements"][0]
        companion["measurements"] = [
            dict(seed, source_ref=first, value=0.5),
            dict(seed, source_ref=second, value=0.75),
        ]
        path = write_json(self.directory / "sources.json", companion)
        return run_script(CLI, "--registry", REGISTRY, "--companion", path)

    def test_https_host_case_conflict(self):
        result = self.project_source_pair("https://example.org/score", "HTTPS://EXAMPLE.ORG/score")
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"conflicting values", result.stderr)

    def test_https_default_port_conflict(self):
        result = self.project_source_pair("https://example.org/score", "https://example.org:443/score")
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"conflicting values", result.stderr)

    def test_https_path_case_preserves_both_original_sources(self):
        sources = ["HTTPS://EXAMPLE.ORG:443/score?Q=A#Model", "https://example.org/Score?Q=A#Model"]
        result = self.project_source_pair(*sources)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        rows = json.loads(result.stdout)["measurements"]["measured"]
        self.assertEqual([row["source_ref"] for row in rows], sources)

    def test_explicit_companion_for_unmodified_registry(self):
        result = run_script(CLI, "--registry", REGISTRY, "--companion", COMPANION)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout)["companion_sha256"], sha256(COMPANION.read_bytes()))

    def test_invalid_pointer_never_emits_projection(self):
        registry, path = self.pointer_fixture()
        good_pointer = registry["snapshot"]["domain_strength"].copy()
        outside = self.directory / "outside.json"
        outside.symlink_to("/usr/bin/true")
        cases = (
            ("sha256", "a" * 64), ("sha256", "short"),
            ("path", str(path)), ("path", "../companion.json"),
            ("path", ".test-scratch/../config/aa-domain-strength.json"),
            ("path", "C:/something"), ("path", "%2e%2e/something"),
            ("path", outside.relative_to(ROOT).as_posix()),
            ("path", "config/does-not-exist-g11.json"),
            ("schema_version", 2), ("schema_version", True),
            ("unexpected", 1),
        )
        for index, (field, value) in enumerate(cases):
            with self.subTest(field=field, value=value):
                registry["snapshot"]["domain_strength"] = dict(good_pointer, **{field: value})
                registry_path = write_json(self.directory / f"bad-{index}.json", registry)
                result = run_script(CLI, "--registry", registry_path)
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertIn(f"snapshot.domain_strength.{field}:".encode(), result.stderr)

    def test_pointer_cannot_be_overridden_by_explicit_companion(self):
        registry, _ = self.pointer_fixture()
        path = write_json(self.directory / "registry.json", registry)
        result = run_script(CLI, "--registry", path, "--companion", COMPANION)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"companion.path:", result.stderr)

    def test_malformed_companion_never_emits_projection(self):
        source = json.loads(COMPANION.read_bytes())
        for field, value in (("snapshot_id", "wrong"), ("registry_sha256", "f" * 64),
                             ("schema_version", 2)):
            with self.subTest(field=field):
                path = write_json(self.directory / f"{field}.json", dict(source, **{field: value}))
                result = run_script(CLI, "--registry", REGISTRY, "--companion", path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertIn(f"companion.{field}:".encode(), result.stderr)

    def test_builder_rebinding_changes_only_pointer_and_writes_new_candidate(self):
        base = self.directory / "base.json"
        candidate = self.directory / "candidate.json"
        build = ("build", "--base", REGISTRY, "--extract", EXTRACT, "--as-of", "2026-11-19",
                 "--revision", "3")
        inputs_before = {path: sha256(path.read_bytes()) for path in (REGISTRY, EXTRACT, COMPANION)}
        unbound_result = run_script(BUILDER, *build, "--out", base)
        self.assertEqual(unbound_result.returncode, 0, unbound_result.stderr.decode())
        unbound = json.loads(base.read_bytes())
        self.assertEqual(unbound["snapshot"]["approval"]["status"], "proposed")
        canonical = json.dumps(unbound, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        companion = json.loads(COMPANION.read_bytes())
        companion.update(snapshot_id=unbound["snapshot"]["id"], registry_sha256=sha256(canonical.encode("utf-8")))
        staged_companion = write_json(self.directory / "companion.json", companion)

        no_output = run_script(BUILDER, *build, "--domain-strength", staged_companion)
        self.assertNotEqual(no_output.returncode, 0)
        self.assertIn(b"requires a new --out path", no_output.stderr)
        existing = run_script(BUILDER, *build, "--domain-strength", staged_companion, "--out", base)
        self.assertNotEqual(existing.returncode, 0)
        self.assertIn(b"build: --out:", existing.stderr)
        self.assertEqual(json.loads(base.read_bytes()), unbound)

        incomplete = self.directory / "wrong-binding.json"
        bad_result = run_script(BUILDER, *build, "--domain-strength", COMPANION, "--out", incomplete)
        self.assertNotEqual(bad_result.returncode, 0)
        self.assertIn(b"companion.snapshot_id:", bad_result.stderr)
        self.assertFalse(incomplete.exists())

        bound_result = run_script(BUILDER, *build, "--domain-strength", staged_companion, "--out", candidate)
        self.assertEqual(bound_result.returncode, 0, bound_result.stderr.decode())
        bound = json.loads(candidate.read_bytes())
        pointer = bound["snapshot"].pop("domain_strength")
        self.assertEqual(pointer, {
            "path": staged_companion.relative_to(ROOT).as_posix(),
            "schema_version": 1,
            "sha256": sha256(staged_companion.read_bytes()),
        })
        self.assertEqual(bound, unbound)

        original = json.loads(REGISTRY.read_bytes())
        self.assertEqual(bound["policy"], original["policy"])
        self.assertEqual([row["id"] for row in bound["scored_configs"]],
                         [row["id"] for row in original["scored_configs"]])
        for old, new in zip(original["scored_configs"], bound["scored_configs"]):
            self.assertEqual({k: v for k, v in old.items() if k != "evidence_report"},
                             {k: v for k, v in new.items() if k != "evidence_report"})
        self.assertEqual(inputs_before, {path: sha256(path.read_bytes()) for path in inputs_before})

        module_candidate = self.directory / "module-candidate.json"
        module_result = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.aa_rebaseline", *map(str, build),
             "--domain-strength", str(staged_companion), "--out", str(module_candidate)],
            capture_output=True, check=False, cwd=ROOT,
        )
        self.assertEqual(module_result.returncode, 0, module_result.stderr.decode())
        self.assertEqual(module_candidate.read_bytes(), candidate.read_bytes())

        projected = run_script(CLI, "--registry", candidate)
        self.assertEqual(projected.returncode, 0, projected.stderr.decode())
        self.assertIs(json.loads(projected.stdout)["dispatch_eligible"], False)
        self.assertEqual(json.loads(projected.stdout)["companion_sha256"], pointer["sha256"])

    def test_builder_approval_basis_still_requires_approved(self):
        output = self.directory / "unapproved.json"
        result = run_script(
            BUILDER, "build", "--extract", EXTRACT, "--as-of", "2026-11-19", "--revision", 3,
            "--out", output, "--approval", "proposed", "--approval-basis", "fixture",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"--approval-basis requires --approval approved", result.stderr)
        self.assertFalse(output.exists())
