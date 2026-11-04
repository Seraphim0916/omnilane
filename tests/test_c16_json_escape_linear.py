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


def normalize_locale(name):
    # Case and hyphens do not distinguish locale names: UTF-8 is utf8 and
    # ISO8859-1 is iso88591 in glibc's `locale -a`.
    return name.lower().replace("-", "")


# Spellings of each locale on macOS and glibc.
LOCALES = {
    "c": ("C",),
    "utf8": ("en_US.UTF-8", "en_US.utf8"),
    "big5": ("zh_TW.Big5", "zh_TW.big5"),
    "gbk": ("zh_CN.GBK", "zh_CN.gbk"),
    "gb18030": ("zh_CN.GB18030", "zh_CN.gb18030"),
    "sjis": ("ja_JP.SJIS", "ja_JP.sjis", "ja_JP.Shift_JIS"),
    "eucjp": ("ja_JP.eucJP", "ja_JP.eucjp"),
    "iso8859_1": ("en_US.ISO8859-1", "en_US.iso88591"),
    "big5hkscs": ("zh_HK.Big5HKSCS", "zh_HK.big5hkscs"),
    "gb2312": ("zh_CN.GB2312", "zh_CN.gb2312", "zh_CN.eucCN"),
    "euckr": ("ko_KR.eucKR", "ko_KR.euckr"),
}


class JsonEscapeLinearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = OLD_JSON_ESCAPE
        cls.new = extract_escape((ROOT / "scripts/lib/common.sh").read_bytes())
        try:
            names = subprocess.run(
                ["locale", "-a"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                check=True, timeout=10).stdout.decode("ascii", "replace").split()
        except (OSError, subprocess.SubprocessError):
            names = []
        cls.installed = {normalize_locale(name): name for name in names}

    def locale(self, *candidates):
        # macOS and glibc spell the same locale differently (zh_TW.Big5 and
        # zh_TW.big5, en_US.ISO8859-1 and en_US.iso88591); use the installed one.
        for candidate in candidates:
            if candidate in ("C", "POSIX"):
                return candidate
            name = self.installed.get(normalize_locale(candidate))
            if name is not None:
                return name
        self.skipTest(f"none of {', '.join(candidates)} is installed on this machine (locale -a)")

    def test_reference_is_the_pre_linear_implementation(self):
        # Equal functions would make the byte comparison vacuous.
        self.assertNotEqual(self.old, self.new)

    def escape(self, function, payload, locale, timeout=30, call=b"json_escape", path=None,
               variables=None, switch=b"", setlocale_warning=False):
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
        stderr = result.stderr
        if setlocale_warning:
            # Some Bash builds warn when setlocale fails; bash 3.2 on macOS does not.
            stderr = b"".join(line for line in stderr.splitlines(keepends=True)
                              if b"setlocale" not in line)
        self.assertEqual(stderr, b"")
        return result.stdout

    def test_byte_compatibility(self):
        for locale in ("C", ("en_US.UTF-8", "en_US.utf8", "C.UTF-8", "C.utf8")):
            for name, payload in CASES:
                with self.subTest(locale=locale, case=name):
                    name_ = self.locale(*locale) if isinstance(locale, tuple) else locale
                    self.assertEqual(self.escape(self.old, payload, name_),
                                     self.escape(self.new, payload, name_))

    def test_loop_and_python_paths_match(self):
        # These are the locales the router sends to Python; ISO8859-1 stands
        # for single-byte locales, where the loop also sees one byte per character.
        for candidates in (("C",), ("en_US.UTF-8", "C.UTF-8"), ("en_US.ISO8859-1",)):
            for name, payload in CASES:
                with self.subTest(locale=candidates[0], case=name):
                    locale = self.locale(*candidates)
                    self.assertEqual(
                        self.escape(self.new, payload, locale, call=b"json_escape_loop"),
                        self.escape(self.new, payload, locale, call=b"json_escape_python"))

    def test_without_python3_short_and_long_input_use_the_loop(self):
        unit = b'a\\"\x01\n' + "台".encode() + b"\xff"
        escaped = b'a\\\\\\"\\u0001\\n' + "台".encode() + b"\xff"
        with tempfile.TemporaryDirectory() as empty:
            for count in (1, 512):
                with self.subTest(count=count):
                    locale = self.locale("en_US.UTF-8", "C.UTF-8")
                    self.assertEqual(self.escape(self.new, unit * count, locale, path=empty),
                                     escaped * count)

    def test_python_starts_from_1024_characters_and_a_failure_uses_the_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "called"
            stub = Path(directory) / "python3"
            stub.write_text('#!/bin/sh\n: > "%s"\nexit 1\n' % marker)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            for count, called in ((1023, False), (1024, True)):
                for candidates, payload in ((("C",), b"a" * count),
                                            (("en_US.UTF-8", "C.UTF-8"), "台".encode() * count)):
                    with self.subTest(count=count, locale=candidates[0]):
                        locale = self.locale(*candidates)
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
            for candidates in LOCALES.values():
                for name, payload in payloads:
                    with self.subTest(locale=candidates[0], case=name):
                        locale = self.locale(*candidates)
                        loop = self.escape(self.old, payload, locale)
                        self.assertEqual(self.escape(self.new, payload, locale), loop)
                        self.assertEqual(self.escape(self.new, payload, locale, path=empty), loop)

    def test_a_failed_switch_to_a_utf8_name_keeps_big5_and_the_loop(self):
        # setlocale fails for these names, so Bash keeps decoding Big5.
        with tempfile.TemporaryDirectory() as empty:
            for name in (b"typo.UTF-8", b"en_US.UTF-8@euro"):
                for count in (1024, 1025):
                    payload = b"\xa4\x5c" * count
                    switch = b"LC_ALL=" + name + b"; "
                    with self.subTest(name=name, count=count):
                        big5 = self.locale(*LOCALES["big5"])
                        if normalize_locale(name.decode()) in self.installed:
                            self.skipTest(f"{name.decode()} is installed, so the switch succeeds")
                        options = dict(switch=switch, setlocale_warning=True)
                        loop = self.escape(self.old, payload, big5, **options)
                        self.assertEqual(len(loop), len(payload))
                        self.assertEqual(self.escape(self.new, payload, big5, **options), loop)
                        self.assertEqual(self.escape(self.new, payload, big5, path=empty, **options),
                                         loop)

    def test_python_is_used_only_where_bash_decodes_utf8_or_single_bytes(self):
        utf8, zh_utf8 = ("en_US.UTF-8", "C.UTF-8"), ("zh_TW.UTF-8",)
        big5 = LOCALES["big5"]
        # (LC_ALL, other variables, command run after reading input, Python used);
        # tuples list the spellings of one locale and resolve to the installed one.
        cases = [
            ("C", None, b"", True), ("POSIX", None, b"", True), (utf8, None, b"", True),
            (zh_utf8, None, b"", True), (("C.UTF-8",), None, b"", True),
            (LOCALES["iso8859_1"], None, b"", True),
            (big5, None, b"", False), (LOCALES["gbk"], None, b"", False),
            (LOCALES["gb18030"], None, b"", False), (LOCALES["sjis"], None, b"", False),
            (LOCALES["eucjp"], None, b"", False),
            (None, {}, b"", True),
            (None, {"LANG": big5}, b"", False),
            (None, {"LC_CTYPE": utf8, "LANG": big5}, b"", True),
            (None, {"LC_CTYPE": big5, "LANG": utf8}, b"", False),
            (utf8, {"LC_CTYPE": big5}, b"", True),
            (big5, None, b"LC_ALL=typo.UTF-8; ", False),
            (big5, None, b"LC_ALL=" + "{utf8}".encode() + b"; ", True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "called"
            stub = Path(directory) / "python3"
            stub.write_text('#!/bin/sh\n: > "%s"\nexit 1\n' % marker)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            for locale, variables, switch, called in cases:
                label = locale[0] if isinstance(locale, tuple) else locale
                with self.subTest(locale=label, variables=variables, switch=switch):
                    resolve = lambda name: self.locale(*name) if isinstance(name, tuple) else name
                    locale_ = resolve(locale)
                    variables_ = {k: resolve(v) for k, v in (variables or {}).items()}
                    switch_ = (switch.replace(b"{utf8}", resolve(utf8).encode())
                               if b"{utf8}" in switch else switch)
                    self.assertEqual(self.escape(self.new, b"a" * 1024, locale_, path=directory,
                                                 variables=variables_, switch=switch_,
                                                 setlocale_warning=b"typo" in switch_),
                                     b"a" * 1024)
                    self.assertEqual(marker.exists(), called)
                    if called:
                        marker.unlink()

    def test_an_uninstalled_locale_falls_back_to_c_and_uses_python(self):
        # Bash keeps C when setlocale fails at startup, so the probes read one
        # character per byte; this must route to Python, not fail.
        name = "xx_YY.UTF-8"
        if normalize_locale(name) in self.installed:
            self.skipTest(f"{name} is installed")
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "called"
            stub = Path(directory) / "python3"
            stub.write_text('#!/bin/sh\n: > "%s"\nexit 1\n' % marker)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            probe = (self.new + b'\na=$\'\\xc3\\x80\' b=$\'\\xa4\\x5c\' c=$\'\\xf0\\x9f\\x98\\x80\' '
                     b'd=$\'\\x95\\x5c\'; printf "%s %s %s %s" ${#a} ${#b} ${#c} ${#d}')
            env = {k: v for k, v in os.environ.items() if k not in ("LC_ALL", "LC_CTYPE", "LANG")}
            result = subprocess.run([BASH, "-c", probe], env=dict(env, LC_ALL=name),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, check=True, timeout=30)
            self.assertEqual(result.stdout, b"2 2 4 2")
            self.assertEqual(self.escape(self.new, b"a" * 1024, name, path=directory,
                                         setlocale_warning=True), b"a" * 1024)
            self.assertTrue(marker.exists())

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
