"""Public installer regression tests; all files live in disposable fixture homes."""
from offline_env import isolated_environment
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
START = b'<!-- omnilane-routing:start -->'
END = b'<!-- omnilane-routing:end -->'


class InstallerRoutingTests(unittest.TestCase):
    def setUp(self):
        # Leave disposable directories to the enclosing test scratch lifetime.
        self.base = Path(tempfile.mkdtemp(prefix='installer-routing-'))
        self.env, self.violations = isolated_environment(self.base / 'isolation', os.environ['PATH'])
        self.addCleanup(lambda: self.assertFalse(self.violations.exists()))
        self.home = Path(self.env['HOME'])
        bins = self.base / 'bin'
        bins.mkdir()
        (bins / 'codex').write_text('#!/bin/sh\nexit 0\n')
        (bins / 'codex').chmod(0o755)
        self.env.update(PATH=str(bins) + os.pathsep + self.env['PATH'],
                        OMNILANE_HOOKS='codex', OMNILANE_LANG='en')
        self.file = self.home / '.codex/AGENTS.md'
        self.file.parent.mkdir()
        # Use the reviewed expected text even against the pre-change installer.
        self.short = (
            START + b'\n## OMNILANE\n'
            b'Read the omnilane skill (`SKILL.md`) only when dispatching or delegating work.\n'
            b'Use `omnilane list` for effective routing; do not copy model tables into instruction files.\n'
            b'If dispatch is refused (for example, `missing-caller-context`), run `omnilane whoami` first.\n'
            b'Do not claim a human-operator exemption or switch yourself to a higher-tier model.\n' + END + b'\n')
        self.full = (ROOT / 'hooks/routing-instruction.md').read_bytes()

    def run_install(self, *args, expected=0):
        result = subprocess.run(['/bin/bash', str(ROOT / 'install.sh'), *args],
                                env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def snapshot(self):
        return [(str(p.relative_to(self.home)), p.lstat().st_mode,
                 os.readlink(p) if p.is_symlink() else
                 hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'directory')
                for p in sorted(self.home.rglob('*'))]

    def test_fresh_installs_short_block(self):
        self.run_install()
        self.assertEqual(self.file.read_bytes(), self.short)
        self.assertLessEqual(len(self.short.splitlines()) - 2, 6)

    def test_upgrade_preserves_surrounding_bytes_and_inode(self):
        prefix, suffix = b'alpha\r\n\x00\xff\n\n', b'\n\r\nomega\xff'
        self.file.write_bytes(prefix + self.full + suffix)
        inode = self.file.stat().st_ino
        self.run_install()
        self.assertEqual(self.file.read_bytes(), prefix + self.short + suffix)
        self.assertEqual(self.file.stat().st_ino, inode)
        self.run_install()
        self.assertEqual(self.file.read_bytes(), prefix + self.short + suffix)

    def test_append_and_reinstall_are_idempotent(self):
        for original in (b'', b'alpha\n', b'alpha'):
            with self.subTest(original=original):
                self.file.write_bytes(original)
                self.run_install()
                expected = original + (b'\n' if original else b'') + self.short
                self.assertEqual(self.file.read_bytes(), expected)
                self.run_install()
                self.assertEqual(self.file.read_bytes(), expected)

    def test_malformed_markers_are_unchanged(self):
        for broken in (START + b'\nbody\n', END + b'\n', END + b'\n' + START + b'\n',
                       START + b'\n' + START + b'\n' + END + b'\n',
                       START + b'\n' + END + b'\n' + END + b'\n'):
            with self.subTest(broken=broken):
                self.file.write_bytes(b'prefix\n' + broken + b'suffix')
                before = self.file.read_bytes()
                output = self.run_install(expected=2)
                self.assertIn('warning:', output)
                self.assertEqual(self.file.read_bytes(), before)
                self.run_install('--dry-run', expected=2)
                self.assertEqual(self.file.read_bytes(), before)

    def test_dry_run_preserves_fresh_and_installed_homes(self):
        before = self.snapshot()
        self.assertIn('would write routing reminder', self.run_install('--dry-run'))
        self.assertEqual(self.snapshot(), before)
        self.run_install()
        self.file.write_bytes(b'prefix\n' + self.full + b'suffix')
        self.file.chmod(0o400)
        before = self.snapshot()
        self.run_install('--dry-run')
        self.assertEqual(self.snapshot(), before)
        self.run_install('--uninstall', '--dry-run')
        self.assertEqual(self.snapshot(), before)

    def test_check_accepts_short_and_rejects_full_without_writes(self):
        self.run_install()
        self.file.write_bytes(self.short)
        before = self.snapshot()
        self.assertIn('PASS codex-hook', self.run_install('--check'))
        self.assertEqual(self.snapshot(), before)
        self.file.write_bytes(self.full)
        before = self.snapshot()
        self.assertIn('DRIFT codex-hook', self.run_install('--check', expected=1))
        self.assertEqual(self.snapshot(), before)

    def test_upgrade_preserves_symlink_target_and_inode(self):
        target = self.home / 'shared.md'
        target.write_bytes(b'prefix\n' + self.full + b'suffix')
        self.file.symlink_to('../shared.md')
        inode = target.stat().st_ino
        self.run_install()
        self.assertTrue(self.file.is_symlink())
        self.assertEqual(os.readlink(self.file), '../shared.md')
        self.assertEqual(target.stat().st_ino, inode)
        self.assertEqual(target.read_bytes(), b'prefix\n' + self.short + b'suffix')

    def test_upgrade_at_eof_preserves_missing_final_newline(self):
        self.file.write_bytes(b'prefix\n' + self.full.rstrip(b'\n'))
        self.run_install()
        expected = b'prefix\n' + self.short.rstrip(b'\n')
        self.assertEqual(self.file.read_bytes(), expected)
        self.run_install()
        self.assertEqual(self.file.read_bytes(), expected)
        self.assertIn('PASS codex-hook', self.run_install('--check'))


if __name__ == '__main__':
    unittest.main()
