"""aa_rebaseline compat-entry rebuilds archive entries from transport_compat's contract functions.

An entry's selectors depend on build_overlay.PROVEN, so an archived entry reproduces against
the PROVEN of the commit that last wrote it: the history tests load that file with git show.
"""
import offline_env  # Activate suite isolation for direct file execution.
import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
import aa_rebaseline  # noqa: E402
import build_overlay  # noqa: E402

ARCHIVE = REPO / "config/transport-compatibility.json"
# snapshot id -> (commit holding its approved registry, commit that last wrote its archive entry)
HISTORY = {
    "aa-v4.3.2-2026-10-18-v1": ("939742b", "939742b"),
    "aa-v4.3.2-2026-10-27-v1": ("b2e4a94", "b2e4a94"),
    "aa-v4.3.2-2026-10-28-v1": ("762ffdd", "762ffdd"),
}


def git_show(revision):
    try:
        result = subprocess.run(["git", "show", revision], cwd=REPO, capture_output=True)
    except OSError as error:
        raise unittest.SkipTest(f"git is not available: {error}")
    if result.returncode != 0:
        raise unittest.SkipTest(f"history is not available for {revision}: "
                                f"{result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def proven_at(commit):
    module = types.ModuleType("build_overlay_at_" + commit)
    module.__file__ = build_overlay.__file__
    source = git_show(f"{commit}:scripts/lib/build_overlay.py")
    exec(compile(source, module.__name__, "exec"), module.__dict__)
    return module.PROVEN


class CompatEntryTests(unittest.TestCase):
    def setUp(self):
        self.archive = json.loads(ARCHIVE.read_bytes())

    def test_current_entry_matches_the_archive(self):
        registry = aa_rebaseline.REGISTRY.read_bytes()
        snapshot = json.loads(registry)["snapshot"]["id"]
        self.assertEqual(aa_rebaseline.compat_entry(registry), self.archive["snapshots"][snapshot])

    def test_archived_entries_reproduce_from_history(self):
        for snapshot, (registry_commit, entry_commit) in HISTORY.items():
            with self.subTest(snapshot=snapshot):
                registry = git_show(f"{registry_commit}:config/aa-model-policy.json")
                with patch.object(build_overlay, "PROVEN", proven_at(entry_commit)):
                    entry = aa_rebaseline.compat_entry(registry)
                self.assertEqual(entry, self.archive["snapshots"][snapshot])

    def test_write_reproduces_the_archive_and_check_detects_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "archive.json"
            shutil.copyfile(ARCHIVE, copy)
            args = SimpleNamespace(registry=str(aa_rebaseline.REGISTRY), archive=str(copy), write=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(aa_rebaseline.cmd_compat_entry(args), 0)
            self.assertEqual(copy.read_bytes(), ARCHIVE.read_bytes())

            args.write = False
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(aa_rebaseline.cmd_compat_entry(args), 0)
            self.assertIn("matches the archive", out.getvalue())

            drifted = json.loads(copy.read_bytes())
            snapshot = json.loads(aa_rebaseline.REGISTRY.read_bytes())["snapshot"]["id"]
            drifted["snapshots"][snapshot]["registry_sha256"] = "0" * 64
            copy.write_bytes(aa_rebaseline.dump_archive(drifted))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(aa_rebaseline.cmd_compat_entry(args), 1)
            self.assertIn("differs in registry_sha256", out.getvalue())

    def test_check_never_writes(self):
        before = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
        args = SimpleNamespace(registry=str(aa_rebaseline.REGISTRY), archive=str(ARCHIVE), write=False)
        with contextlib.redirect_stdout(io.StringIO()):
            aa_rebaseline.cmd_compat_entry(args)
        self.assertEqual(hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
