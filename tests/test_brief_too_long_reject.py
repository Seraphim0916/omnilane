"""Dispatch tier planning and runaway refusals before any worker starts."""

import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from offline_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLI = "#!/usr/bin/env bash\ntouch \"$FAKE_CALLED\"\necho OK\n"


def common(script, env=None):
    return subprocess.run(
        ["bash", "-c", 'source "$1/scripts/lib/common.sh"; ' + script, "common", str(ROOT)],
        env=env, capture_output=True, text=True, timeout=30)


class PromptCapTableTests(unittest.TestCase):
    def test_limits_match_the_former_runner_caps(self):
        gemini = "130000" if platform.system() == "Linux" else "180000"
        argument_cap = "131071" if platform.system() == "Linux" else "140000"
        expected = {
            "claude": "102400", "codex": "140000", "grok": "140000", "exec": "140000",
            "kimi": argument_cap, "opencode": argument_cap, "qwen": argument_cap, "groq": "140000",
            "openrouter": "140000", "vote": "100000", "gemini": gemini,
        }
        for vendor, cap in expected.items():
            self.assertEqual(common(f"prompt_cap_bytes {vendor}").stdout.strip(), cap, vendor)
        self.assertEqual(common("prompt_cap_bytes codex live").stdout.strip(), "102400")
        self.assertEqual(common("prompt_cap_bytes gemini live").stdout.strip(), gemini)

    def test_runner_check_refuses_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p"
            for size, rc in ((102400, 0), (102401, 2)):
                f.write_bytes(b"x" * size)
                r = common(f'enforce_prompt_cap "{f}" claude')
                self.assertEqual(r.returncode, rc, r.stderr)
                self.assertEqual(f.read_bytes(), b"x" * size)
            self.assertIn("task is 102401 bytes as sent to claude; limit is 102400 bytes", r.stderr)

    def test_table_matches_the_pinned_runner_caps(self):
        # Runner bytes are pinned in the signed transport evidence, so they keep
        # their literal caps; the pre-flight table must agree with every one.
        import re
        for runner in (ROOT / "scripts/runners").glob("run-*.sh"):
            vendor = runner.name[len("run-"):-len(".sh")]
            for line in runner.read_text().splitlines():
                m = re.match(r'\s*truncate_payload "\$PROMPT_FILE" (\d+)$', line)
                if m:
                    self.assertEqual(common(f"prompt_cap_bytes {vendor}").stdout.strip(), str(min(int(m.group(1)), 131071)) if platform.system() == "Linux" and vendor in ("opencode", "kimi", "qwen") else m.group(1),
                                     runner.name)


class DispatchPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omnilane-brief-limit-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.env, violations = isolated_environment(base / "isolated", os.environ["PATH"])
        self.addCleanup(lambda: self.assertFalse(violations.exists()))
        self.home, self.work, bins = base / "omnilane", base / "work", base / "bin"
        for directory in (self.home, self.work, bins):
            directory.mkdir()
        self.called = base / "called"
        for name in ("codex", "claude"):
            (bins / name).write_text(FAKE_CLI)
            (bins / name).chmod(0o755)
        (self.home / "routing.local.yaml").write_text(
            "consult: codex gpt-6.1-sol-medium -\nreview: claude claude-sonnet-5-5-medium -\n")
        self.env.update(OMNILANE_HOME=str(self.home), FAKE_CALLED=str(self.called),
                        CODEX_BIN=str(bins / "codex"), CLAUDE_BIN=str(bins / "claude"),
                        PATH=str(bins) + os.pathsep + self.env["PATH"])
        self.rules = len(common('printf "%s\\n%s\\n\\n" "$OMNILANE_WORKER_RULES_MARKER" "$OMNILANE_WORKER_RULES"')
                         .stdout.encode())

    def dispatch(self, payload, *flags, lane="consult"):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/dispatch.sh"), "--workdir", str(self.work),
             "--timeout", "30", *flags, lane, "-"],
            input=payload, env=self.env, capture_output=True, timeout=90)

    def jobs(self):
        jobs = self.home / "jobs"
        return list(jobs.iterdir()) if jobs.exists() else []

    def assert_refused(self, result, size, vendor, cap):
        self.assertEqual(result.returncode, 2, result.stderr.decode(errors="replace")[-2000:])
        err = result.stderr.decode()
        self.assertIn(f"task is {size} bytes", err)
        self.assertIn(str(cap), err)
        self.assertIn("nothing was cut", err.lower())
        self.assertEqual(err.count("task is "), 1)
        self.assertFalse(self.called.exists(), "a worker CLI was started")
        self.assertEqual(self.jobs(), [], "a job directory was left behind")

    def test_dry_run_exact_limit_inline_and_one_more_byte_staged(self):
        task = 140000 - self.rules
        ok = self.dispatch(b"a" * task, "--dry-run")
        self.assertEqual(ok.returncode, 0, ok.stderr.decode(errors="replace")[-2000:])
        self.assertIn(b"dry_run=yes", ok.stdout)
        over = self.dispatch(b"a" * (task + 1), "--dry-run")
        self.assertEqual(over.returncode, 0, over.stderr)
        self.assertIn(b"task_delivery=file", over.stdout)
        self.assertEqual(self.jobs(), [])
        # A preview reports the file tier without staging anything.
        self.assertFalse((self.work / ".omnilane-tmp").exists())

    def test_multibyte_character_across_the_limit(self):
        task = 140000 - self.rules
        # A 3-byte character whose last byte lands one past the limit.
        payload = b"a" * (task - 2) + "灌".encode()
        self.assertEqual(len(payload), task + 1)
        over = self.dispatch(payload, "--dry-run")
        self.assertEqual(over.returncode, 0, over.stderr)
        self.assertIn(b"task_delivery=file", over.stdout)
        exact = b"a" * (task - 3) + "灌".encode()
        self.assertEqual(self.dispatch(exact, "--dry-run").returncode, 0)

    def test_claude_counts_the_worker_rules_inside_102400(self):
        task = 102400 - self.rules
        self.assertEqual(self.dispatch(b"b" * task, "--dry-run", lane="review").returncode, 0)
        over = self.dispatch(b"b" * (task + 1), "--dry-run", lane="review")
        self.assertEqual(over.returncode, 0, over.stderr)
        self.assertIn(b"task_delivery=file", over.stdout)

    def test_background_refusal_leaves_no_job(self):
        self.assert_refused(self.dispatch(b"c" * 1048577, "--background"),
                            1048577, "codex", 1048576)

    def test_live_refusal_is_one_line_even_when_timeout_would_warn(self):
        result=self.dispatch(b"x"*1048577,"--background","--live","--idle-timeout","1")
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(result.stderr.count(b"\n"),1,result.stderr)
        self.assertIn(b"tier 3 refusal",result.stderr)
        self.assertEqual(self.jobs(),[])

    def test_foreground_refusal_leaves_no_job(self):
        self.assert_refused(self.dispatch(b"d" * 1048577), 1048577, "codex", 1048576)

    def test_argument_task_counts_its_newline(self):
        task = 102400 - self.rules - 1
        args = ["bash", str(ROOT / "scripts/dispatch.sh"), "--workdir", str(self.work),
                "--timeout", "30", "--dry-run", "review"]
        ok = subprocess.run(args + ["e" * task], env=self.env, capture_output=True, timeout=90)
        self.assertEqual(ok.returncode, 0, ok.stderr.decode(errors="replace")[-2000:])
        over = subprocess.run(args + ["e" * (task + 1)], env=self.env, capture_output=True, timeout=90)
        self.assertEqual(over.returncode, 0, over.stderr)
        self.assertIn(b"task_delivery=file", over.stdout)


if __name__ == "__main__":
    unittest.main()
