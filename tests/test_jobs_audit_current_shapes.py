"""jobs audit accepts what dispatch writes today: the read-only worker copy, the
executor fields in meta.json, and a native handoff that has no pid."""
import offline_env  # Activate suite isolation for direct file execution.
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "scripts" / "jobs.sh"
SHA = "a" * 64
CLI_META = (
    '{"lane":"triage","vendor":"codex","session_mode":"single-shot","idle_timeout":900,'
    '"model":"gpt-6-luna","effort":"high","timeout":600,"job_timeout":null,"mode":"advise",'
    '"workdir":"/tmp/project","foreman_session":"","candidate":"1/5",'
    '"started":"2026-10-02T07:26:38Z","executor":"cli","executor_reason":"no-native-context",'
    '"worker_interpreter_path":"/bin/bash","worker_interpreter_version":"3.2.57(1)-release",'
    '"job_worker_source_path":"/repo/scripts/lib/job-worker.sh",'
    f'"job_worker_source_sha256":"{SHA}","job_worker_path":"/home/jobs/x/job-worker.sh",'
    f'"job_worker_sha256":"{SHA}"}}'
)
NATIVE_META = (
    '{"lane":"bulk-mechanical","vendor":"claude","model":"claude-opus-5-5","effort":"inherited",'
    '"mode":"work","workdir":"/tmp/project","executor":"native",'
    '"executor_reason":"inherited-caller-runtime","aa_policy_code":"native-inherited-allowed",'
    '"aa_effective_ceiling":null,"started":"2026-09-29T19:49:36Z","session_mode":"native"}'
)


class JobsAuditCurrentShapesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-audit-shapes-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "home"
        self.jobs = self.home / "jobs"
        self.jobs.mkdir(parents=True)
        os.chmod(self.jobs, 0o700)

    def job(self, name: str, meta: str, files: dict) -> Path:
        directory = self.jobs / name
        directory.mkdir()
        os.chmod(directory, 0o700)
        for filename, (content, mode) in {"meta.json": (meta + "\n", 0o600),
                                           "task.txt": ("probe\n", 0o600), **files}.items():
            path = directory / filename
            path.write_text(content, encoding="utf-8")
            os.chmod(path, mode)
        return directory

    def audit(self) -> dict:
        env = {key: value for key, value in os.environ.items() if not key.startswith("OMNILANE_")}
        env["OMNILANE_HOME"] = str(self.home)
        result = subprocess.run(["/bin/bash", str(JOBS), "--json", "audit"],
                                capture_output=True, text=True, env=env, timeout=60)
        report = json.loads(result.stdout)
        report["codes"] = sorted({(f["scope"], f["code"]) for f in report.get("findings", [])})
        return report

    def test_current_cli_job_and_native_handoff_pass(self):
        self.job("20261002-072638-00001-00001", CLI_META,
                 {"pid": ("4242\n", 0o600), "exit": ("0\n", 0o600), "out.txt": ("done\n", 0o600),
                  "job-worker.sh": ("#!/bin/bash\n", 0o400)})
        self.job("20260930-034936-00002-00002", NATIVE_META, {"native.json": ("{}\n", 0o600)})
        report = self.audit()
        self.assertEqual(report["codes"], [])
        self.assertEqual((report["sampled"], report["passed"], report["failed"]), (2, 2, 0))

    def test_real_problems_are_still_reported(self):
        self.job("20261002-072638-00003-00003", CLI_META,
                 {"exit": ("0\n", 0o600), "out.txt": ("done\n", 0o644),
                  "job-worker.sh": ("#!/bin/bash\n", 0o644)})
        self.job("20261002-072638-00004-00004", CLI_META.replace('"executor":"cli"', '"executor":"other"'),
                 {"pid": ("4242\n", 0o600)})
        codes = self.audit()["codes"]
        self.assertIn(("20261002-072638-00003-00003", "unsafe-file-mode"), codes)
        self.assertIn(("20261002-072638-00003-00003", "missing-pid"), codes)
        self.assertIn(("20261002-072638-00004-00004", "invalid-metadata"), codes)


if __name__ == "__main__":
    unittest.main()
