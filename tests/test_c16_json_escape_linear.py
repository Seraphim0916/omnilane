"""Byte compatibility and bounded runtime for the shared Bash JSON escaper."""

import offline_env  # Activate suite isolation for direct file execution.

import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
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


CASES = [
    ("empty", b""),
    ("del", b"\x7f"),
    ("punctuation", b'\\"/'),
    ("utf8", "台灣中文😀é日本語𐐷".encode("utf-8")),
    ("invalid_utf8", b"\xc0\xaf\xed\xa0\x80\xf4\x90\x80\x80\xff\xfe\xc2"),
    ("all_non_nul_bytes", bytes(range(1, 256))),
    ("trailing_newlines", b"a\n\n"),
    ("long_ascii", b"a" * 8192),
    ("long_mixed", ("台灣😀".encode() + b'\xff\\"/\x01\t\n') * 256),
] + [("control_%02x" % c, bytes([c])) for c in range(1, 32)]


def extract_escape(source):
    match = re.search(rb"(?ms)^json_escape_loop\(\) \{\n.*?^json_escape\(\) \{\n.*?^\}", source)
    if match is None:
        raise AssertionError("json_escape definition not found")
    return match.group()


class JsonEscapeLinearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = OLD_JSON_ESCAPE
        cls.new = extract_escape((ROOT / "scripts/lib/common.sh").read_bytes())
        try:
            cls.installed = set(subprocess.run(
                ["locale", "-a"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                check=True, timeout=10).stdout.decode("ascii", "replace").split())
        except (OSError, subprocess.SubprocessError):
            cls.installed = set()

    def require_locale(self, locale):
        if locale not in self.installed:
            self.skipTest(f"{locale} is not installed on this machine (locale -a)")

    def test_reference_is_the_pre_linear_implementation(self):
        # Equal functions would make the byte comparison vacuous.
        self.assertNotEqual(self.old, self.new)

    def escape(self, function, payload, locale, timeout=30, call=b"json_escape", path=None,
               variables=None, switch=b""):
        # A sentinel preserves trailing newlines through command substitution.
        # Bash strings cannot contain NUL; all other bytes travel over stdin.
        # PATH changes only after cat ran, so the function alone sees the stub.
        # `switch` runs in the same process after the input was read.
        script = (function + b'\ns=$(cat; printf x); s=${s%x}; '
                  b'[ -z "${JE_PATH:-}" ] || PATH=$JE_PATH; ' + switch + call + b' "$s"')
        env = {k: v for k, v in os.environ.items() if k not in ("LC_ALL", "LC_CTYPE", "LANG")}
        if locale is not None:
            env["LC_ALL"] = locale
        env.update(variables or {})
        if path is not None:
            env["JE_PATH"] = str(path)
        result = subprocess.run(
            [BASH, "-c", script], input=payload, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, check=True, timeout=timeout, cwd=ROOT)
        self.assertEqual(result.stderr, b"")
        return result.stdout

    def test_byte_compatibility(self):
        for locale in ("C", "en_US.UTF-8"):
            for name, payload in CASES:
                with self.subTest(locale=locale, case=name):
                    self.assertEqual(self.escape(self.old, payload, locale),
                                     self.escape(self.new, payload, locale))

    def test_loop_and_python_paths_match(self):
        # These are the locales the router sends to Python; ISO8859-1 stands
        # for single-byte locales, where the loop also sees one byte per character.
        for locale in ("C", "en_US.UTF-8", "en_US.ISO8859-1"):
            for name, payload in CASES:
                with self.subTest(locale=locale, case=name):
                    if locale != "C":
                        self.require_locale(locale)
                    self.assertEqual(
                        self.escape(self.new, payload, locale, call=b"json_escape_loop"),
                        self.escape(self.new, payload, locale, call=b"json_escape_python"))

    def test_without_python3_short_and_long_input_use_the_loop(self):
        unit = b'a\\"\x01\n' + "台".encode() + b"\xff"
        escaped = b'a\\\\\\"\\u0001\\n' + "台".encode() + b"\xff"
        with tempfile.TemporaryDirectory() as empty:
            for count in (1, 512):
                with self.subTest(count=count):
                    self.assertEqual(self.escape(self.new, unit * count, "en_US.UTF-8", path=empty),
                                     escaped * count)

    def test_python_starts_from_1024_characters_and_a_failure_uses_the_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "called"
            stub = Path(directory) / "python3"
            stub.write_text('#!/bin/sh\n: > "%s"\nexit 1\n' % marker)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            for count, called in ((1023, False), (1024, True)):
                for locale, payload in (("C", b"a" * count), ("en_US.UTF-8", "台".encode() * count)):
                    with self.subTest(count=count, locale=locale):
                        self.assertEqual(self.escape(self.new, payload, locale, path=directory), payload)
                        self.assertEqual(marker.exists(), called)
                        if called:
                            marker.unlink()

    def test_multibyte_output_does_not_change_across_the_threshold(self):
        # Every byte 0x80-0xff followed by each byte the escaper rewrites: a
        # locale that folds one of those into a character would differ here.
        risky = b"".join(bytes((lead, trail)) for lead in range(0x80, 0x100)
                         for trail in [0x22, 0x5c] + list(range(1, 32)))
        payloads = [("risky_pairs", risky), ("all_non_nul_bytes", bytes(range(1, 256)) * 8)]
        for count in (1023, 1024, 1025):
            payloads += [("a4_5c_x%d" % count, b"\xa4\x5c" * count),
                         ("95_5c_x%d" % count, b"\x95\x5c" * count)]
        with tempfile.TemporaryDirectory() as empty:
            for locale in ("C", "en_US.UTF-8", "zh_TW.Big5", "zh_CN.GBK", "zh_CN.GB18030",
                           "ja_JP.SJIS", "ja_JP.eucJP", "en_US.ISO8859-1", "zh_HK.Big5HKSCS",
                           "zh_CN.GB2312", "ko_KR.eucKR"):
                for name, payload in payloads:
                    with self.subTest(locale=locale, case=name):
                        if locale != "C":
                            self.require_locale(locale)
                        loop = self.escape(self.old, payload, locale)
                        self.assertEqual(self.escape(self.new, payload, locale), loop)
                        self.assertEqual(self.escape(self.new, payload, locale, path=empty), loop)

    def test_a_failed_switch_to_a_utf8_name_keeps_big5_and_the_loop(self):
        # setlocale fails for these names, so Bash keeps decoding Big5.
        self.require_locale("zh_TW.Big5")
        with tempfile.TemporaryDirectory() as empty:
            for name in (b"typo.UTF-8", b"en_US.UTF-8@euro"):
                for count in (1024, 1025):
                    payload = b"\xa4\x5c" * count
                    switch = b"LC_ALL=" + name + b"; "
                    with self.subTest(name=name, count=count):
                        loop = self.escape(self.old, payload, "zh_TW.Big5", switch=switch)
                        self.assertEqual(len(loop), len(payload))
                        self.assertEqual(self.escape(self.new, payload, "zh_TW.Big5", switch=switch),
                                         loop)
                        self.assertEqual(self.escape(self.new, payload, "zh_TW.Big5", path=empty,
                                                     switch=switch), loop)

    def test_python_is_used_only_where_bash_decodes_utf8_or_single_bytes(self):
        # (LC_ALL, other variables, command run after reading input, Python used)
        cases = [
            ("C", None, b"", True), ("POSIX", None, b"", True), ("en_US.UTF-8", None, b"", True),
            ("zh_TW.UTF-8", None, b"", True), ("C.UTF-8", None, b"", True),
            ("en_US.ISO8859-1", None, b"", True),
            ("zh_TW.Big5", None, b"", False), ("zh_CN.GBK", None, b"", False),
            ("zh_CN.GB18030", None, b"", False), ("ja_JP.SJIS", None, b"", False),
            ("ja_JP.eucJP", None, b"", False),
            (None, {}, b"", True),
            (None, {"LANG": "zh_TW.Big5"}, b"", False),
            (None, {"LC_CTYPE": "en_US.UTF-8", "LANG": "zh_TW.Big5"}, b"", True),
            (None, {"LC_CTYPE": "zh_TW.Big5", "LANG": "en_US.UTF-8"}, b"", False),
            ("en_US.UTF-8", {"LC_CTYPE": "zh_TW.Big5"}, b"", True),
            ("zh_TW.Big5", None, b"LC_ALL=typo.UTF-8; ", False),
            ("zh_TW.Big5", None, b"LC_ALL=en_US.UTF-8; ", True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "called"
            stub = Path(directory) / "python3"
            stub.write_text('#!/bin/sh\n: > "%s"\nexit 1\n' % marker)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            for locale, variables, switch, called in cases:
                with self.subTest(locale=locale, variables=variables, switch=switch):
                    for name in [locale] + list((variables or {}).values()):
                        if name not in (None, "C", "POSIX"):
                            self.require_locale(name)
                    self.assertEqual(self.escape(self.new, b"a" * 1024, locale, path=directory,
                                                 variables=variables, switch=switch), b"a" * 1024)
                    self.assertEqual(marker.exists(), called)
                    if called:
                        marker.unlink()

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
