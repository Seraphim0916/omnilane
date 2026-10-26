import offline_env  # Activate suite isolation for direct file execution.

import io
import itertools
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance


class SignatureOrderTests(unittest.TestCase):
    fields = ("TeamIdentifier=not set", "Identifier=vendor=fixture", "Signature=adhoc")

    def facts(self, lines):
        runner = Mock(side_effect=[
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr='\n'.join(lines)),
        ])
        target = Path("/fixture/cli")
        with patch("sys.platform", "darwin"), \
                patch.object(Path, "resolve", return_value=target), \
                patch.object(Path, "open", return_value=io.BytesIO(b"\xcf\xfa\xed\xfe")):
            result = cli_provenance.facts(target, runner=runner)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[0].args[0],
                         ["codesign", "--verify", "--strict", str(target)])
        self.assertEqual(runner.call_args_list[1].args[0],
                         ["codesign", "-dv", "--verbose=2", str(target)])
        return result

    def test_all_six_orders_preserve_equals_and_ignore_unknown_fields(self):
        count = 0
        for order in itertools.permutations(self.fields):
            with self.subTest(order=order):
                lines = ["Unknown=before=fixture", order[0], "Unknown=middle",
                         order[1], order[2], "Other=after"]
                self.assertEqual(self.facts(lines), {
                    "kind": "adhoc", "signer": "adhoc",
                    "identifier": "vendor=fixture", "valid": True,
                })
                count += 1
        self.assertEqual(count, 6)

    def test_duplicate_known_fields_are_rejected_in_every_order(self):
        for order in itertools.permutations(self.fields):
            for duplicate in self.fields:
                with self.subTest(order=order, duplicate=duplicate):
                    self.assertEqual(self.facts(list(order) + [duplicate]), {
                        "kind": "unverifiable", "signer": "unsigned",
                        "identifier": None, "valid": False,
                    })


if __name__ == "__main__":
    unittest.main()
