import offline_env  # Activate suite isolation for direct file execution.
import importlib.util
from pathlib import Path
import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'c41_b353_wakeup', Path(__file__).resolve().parents[1] / 'scripts/completion-wakeup.py')
wakeup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wakeup)


class CommandQuoteTests(unittest.TestCase):
    def test_special_paths_round_trip_as_individual_arguments(self):
        for suffix in ('with spaces', "single'quote", '中文路徑',
                       '$(echo synthetic); & | > < * ? [x] `echo synthetic`',
                       "中文 ' mixed;$()\nsecond line"):
            with self.subTest(suffix=suffix):
                executable = '/synthetic/' + suffix + '/python'
                script = Path('/synthetic/' + suffix + '/completion-wakeup.py')
                home = Path('/synthetic/' + suffix + '/home')
                args = SimpleNamespace(home=str(home), thread_id='thread-1',
                                       host_id='host-1', run_id='run-1')
                with patch.object(wakeup.sys, 'executable', executable), \
                        patch.object(wakeup.Path, 'resolve', return_value=script), \
                        patch.object(wakeup, 'no_links', return_value=home) as checked:
                    command = wakeup.command_prefix(args)
                checked.assert_called_once_with(args.home)
                self.assertEqual(shlex.split(command), [
                    executable, str(script), 'poll', '--home', str(home),
                    '--thread-id', 'thread-1', '--host-id', 'host-1', '--run-id', 'run-1'])


if __name__ == '__main__':
    unittest.main()
