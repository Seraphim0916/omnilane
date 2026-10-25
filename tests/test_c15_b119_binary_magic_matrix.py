import offline_env  # Activate suite isolation for direct file execution.

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance


class BinaryMagicMatrixTests(unittest.TestCase):
    def test_native_headers_do_not_grant_signature_trust(self):
        # Explicit fixtures keep this oracle independent of the production set.
        native = (
            bytes.fromhex("feedface"), bytes.fromhex("cefaedfe"),
            bytes.fromhex("feedfacf"), bytes.fromhex("cffaedfe"),
            bytes.fromhex("cafebabe"), bytes.fromhex("bebafeca"),
            bytes.fromhex("cafebabf"), bytes.fromhex("bfbafeca"),
            b"\x7fELF", b"MZ\x90\x00",
        )
        cases = [(magic, "no-platform-signature") for magic in native]
        cases += [(b"", "unverifiable"), (b"M", "unverifiable")]
        cases += [(magic[:length], "unverifiable")
                  for magic in native[:9] for length in (1, 2, 3)]
        runner = Mock(side_effect=AssertionError("signature tool must not run"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "binary"
            for magic, kind in cases:
                with self.subTest(magic=magic.hex()):
                    path.write_bytes(magic)
                    with patch.object(sys, "platform", "linux"):
                        result = cli_provenance.facts(path, runner=runner)
                    self.assertEqual(result, dict(kind=kind, signer="unsigned",
                                                  identifier=None, valid=False))
                    runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
