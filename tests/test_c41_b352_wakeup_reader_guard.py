import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
import io
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    'c41_b352_wakeup', Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py')
wakeup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wakeup)


class ReaderGuardTests(unittest.TestCase):
    def exercise(self, mode, owner, accepted):
        stream = io.BytesIO(b'public record')
        stream.fileno = Mock(return_value=73)
        stream.read = Mock(wraps=stream.read)
        info = SimpleNamespace(st_mode=mode, st_uid=owner)
        with patch.object(wakeup, 'no_links', return_value=Path('/synthetic/record')), \
                patch.object(wakeup.os, 'open', return_value=73) as opened, \
                patch.object(wakeup.os, 'fdopen', return_value=stream) as fdopen, \
                patch.object(wakeup.os, 'fstat', return_value=info) as fstat, \
                patch.object(wakeup.os, 'getuid', return_value=1001):
            if accepted:
                self.assertEqual(wakeup.read_bytes('/synthetic/record'), b'public record')
                stream.read.assert_called_once_with(wakeup.LIMIT + 1)
            else:
                with self.assertRaisesRegex(wakeup.Invalid, 'invalid record owner/type'):
                    wakeup.read_bytes('/synthetic/record')
                stream.read.assert_not_called()
            opened.assert_called_once_with(
                Path('/synthetic/record'),
                wakeup.os.O_RDONLY | wakeup.os.O_NOFOLLOW | wakeup.os.O_NONBLOCK)
            fdopen.assert_called_once_with(73, 'rb')
            fstat.assert_called_once_with(73)
            self.assertTrue(stream.closed)

    def test_rejects_owner_directory_and_fifo_before_reading(self):
        for kind, mode, owner in (
                ('foreign owner', stat.S_IFREG | 0o600, 1002),
                ('directory', stat.S_IFDIR | 0o700, 1001),
                ('fifo', stat.S_IFIFO | 0o600, 1001)):
            with self.subTest(kind=kind):
                self.exercise(mode, owner, accepted=False)

    def test_owned_regular_file_is_read_and_closed(self):
        self.exercise(stat.S_IFREG | 0o600, 1001, accepted=True)


if __name__ == '__main__':
    unittest.main()
