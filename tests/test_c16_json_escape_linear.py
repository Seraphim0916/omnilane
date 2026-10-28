"""Byte compatibility and bounded runtime for the shared Bash JSON escaper."""

import offline_env  # Activate suite isolation for direct file execution.

import os
from pathlib import Path
import re
import subprocess
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = "/bin/bash"  # Same interpreter used by tests/run.sh's isolated child.

# json_escape as of a281821, before the linear rewrite. Pinned here instead of
# read from origin/main: CI checkouts have no origin/main, and after merge
# origin/main would hold the new version and compare it with itself.
OLD_JSON_ESCAPE = rb'''json_escape() {
  local s="$1" out="" ch escaped code i
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:i:1}"
    case "$ch" in
      '"') out="$out\\\"" ;;
      \\) out="$out\\\\" ;;
      $'\b') out="$out\\b" ;;
      $'\f') out="$out\\f" ;;
      $'\n') out="$out\\n" ;;
      $'\r') out="$out\\r" ;;
      $'\t') out="$out\\t" ;;
      *)
        LC_CTYPE=C printf -v code '%d' "'$ch"
        if [[ "$code" -ge 0 && "$code" -lt 32 ]]; then
          printf -v escaped '\\u%04x' "$code"
          out="$out$escaped"
        else
          out="$out$ch"
        fi
        ;;
    esac
  done
  printf '%s' "$out"
}'''


def extract_escape(source):
    match = re.search(rb"(?ms)^json_escape\(\) \{\n.*?^\}", source)
    if match is None:
        raise AssertionError("json_escape definition not found")
    return match.group()


class JsonEscapeLinearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = OLD_JSON_ESCAPE
        cls.new = extract_escape((ROOT / "scripts/lib/common.sh").read_bytes())

    def test_reference_is_the_pre_linear_implementation(self):
        # Equal functions would make the byte comparison vacuous.
        self.assertNotEqual(self.old, self.new)

    def escape(self, function, payload, locale, timeout=30):
        # A sentinel preserves trailing newlines through command substitution.
        # Bash strings cannot contain NUL; all other bytes travel over stdin.
        script = function + b'\ns=$(cat; printf x); s=${s%x}; json_escape "$s"'
        return subprocess.run(
            [BASH, "-c", script], input=payload, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=dict(os.environ, LC_ALL=locale),
            check=True, timeout=timeout, cwd=ROOT).stdout

    def test_byte_compatibility(self):
        cases = [
            ("empty", b""),
            ("del", b"\x7f"),
            ("punctuation", b'\\"/'),
            ("utf8", "台灣中文😀é日本語𐐷".encode("utf-8")),
            ("invalid_utf8", b"\xc0\xaf\xed\xa0\x80\xf4\x90\x80\x80\xff\xfe\xc2"),
            ("all_non_nul_bytes", bytes(range(1, 256))),
            ("trailing_newlines", b"a\n\n"),
            ("long_ascii", b"a" * 8192),
            ("long_mixed", ("台灣😀".encode() + b'\xff\\"/\x01\t\n') * 256),
        ]
        cases += [("control_%02x" % c, bytes([c])) for c in range(1, 32)]
        for locale in ("C", "en_US.UTF-8"):
            for name, payload in cases:
                with self.subTest(locale=locale, case=name):
                    self.assertEqual(self.escape(self.old, payload, locale),
                                     self.escape(self.new, payload, locale))

    def test_256_kib_under_five_seconds(self):
        # Independently specified output checks correctness as well as speed.
        unit = b'a\\"/\x01\b\f\n\r\t\x7f' + "台灣😀".encode() + b"\xff"
        escaped = b'a\\\\\\"/\\u0001\\b\\f\\n\\r\\t\x7f' + "台灣😀".encode() + b"\xff"
        count, remainder = divmod(256 * 1024, len(unit))
        payload = unit * count + b"a" * remainder
        expected = escaped * count + b"a" * remainder
        start = time.monotonic()
        actual = self.escape(self.new, payload, "en_US.UTF-8", timeout=5)
        elapsed = time.monotonic() - start
        self.assertEqual(actual, expected)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
