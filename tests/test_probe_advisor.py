"""The re-sign sweep keeps claude's server-side advisor out of every probe."""

from __future__ import annotations
import offline_env  # noqa: F401
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".sandbox-tmp"
sys.path.insert(0, str(ROOT / "scripts/lib"))
import probe_failure
import probe_sweep
import transport_compat

KEY = probe_failure.ADVISOR_KILL_SWITCH


class AdvisorKillSwitchTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "evidence").mkdir()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(KEY, None)

    def sweep_once(self, vendor):
        seen = []

        def probe(name, argv, **kwargs):
            seen.append(os.environ.get(KEY))
            return {"verdict": "pass"}

        def sweep(vendor_name, root, run_probe, **_):
            entry = probe_sweep.plan(vendor_name)[0]
            run_probe(entry["name"], ["unused"], root=root, vendor=vendor_name)
            return {"vendor": vendor_name, "outcome": "done", "detail": "",
                    "passed": [entry["config_id"]], "failed": []}

        with patch.object(probe_sweep.probe_module, "probe", probe), \
                patch.object(probe_sweep, "sweep", sweep):
            probe_failure.run_sweep(vendor, self.root, log=lambda *_: None)
        return seen

    def test_claude_probes_run_with_the_advisor_tool_disabled(self):
        self.assertEqual(self.sweep_once("claude"), ["1"])

    def test_other_vendors_are_not_given_the_claude_switch(self):
        for vendor in ("grok", "codex"):
            with self.subTest(vendor=vendor):
                self.assertEqual(self.sweep_once(vendor), [None])

    def test_the_switch_is_restored_after_the_probe(self):
        self.sweep_once("claude")
        self.assertNotIn(KEY, os.environ)
        os.environ[KEY] = "0"
        self.assertEqual(self.sweep_once("claude"), ["1"])
        self.assertEqual(os.environ[KEY], "0")

    def test_the_pinned_engine_files_are_untouched(self):
        # Proofs anchor these hashes; the switch must not live in either file.
        for name in ("probe.py", "probe_sweep.py"):
            self.assertNotIn(KEY, (ROOT / "scripts/lib" / name).read_text())
        self.assertEqual(set(transport_compat.engine_contract()), {"probe.py", "probe_sweep.py"})


if __name__ == "__main__":
    unittest.main()
