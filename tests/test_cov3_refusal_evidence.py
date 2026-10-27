"""In-process coverage for scripts/lib/refusal_evidence.py edge cases."""
import offline_env  # Activate suite isolation for direct file execution.

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import stat
import sys
import tempfile
import unittest
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / "scripts/lib"
sys.path.insert(0, str(LIB))
import refusal_evidence as evidence


def refusal_line(**fields):
    return json.dumps(dict(allowed=False, **fields))


class RefusalFromTests(unittest.TestCase):
    def test_last_refusal_wins_and_non_refusals_are_ignored(self):
        text = "\n".join([
            "headline text",
            refusal_line(code="first"),
            json.dumps({"allowed": True, "code": "ok"}),
            json.dumps({"allowed": 0, "code": "falsy-not-false"}),
            "  " + refusal_line(code="second") + "  ",
            "[" + refusal_line(code="in-a-list") + "]",
            "{broken",
        ])
        self.assertEqual(evidence.refusal_from(text)["code"], "second")

    def test_nothing_found_returns_none(self):
        for text in ("", "\n\n", '{"allowed": true}', "[1]", "{}"):
            with self.subTest(text=text):
                self.assertIsNone(evidence.refusal_from(text))

    def test_deeply_nested_json_is_skipped_not_raised(self):
        deep = '{"allowed": false, "x": ' + "[" * 200000 + "]" * 200000 + "}"
        self.assertEqual(evidence.refusal_from(deep + "\n" + refusal_line(code="after"))["code"], "after")


class OneLineAndSummaryTests(unittest.TestCase):
    def test_whitespace_collapses_and_falsy_values_become_empty(self):
        self.assertEqual(evidence.one_line(" a \n\t b  "), "a b")
        for value in (None, "", 0, False):
            with self.subTest(value=value):
                self.assertEqual(evidence.one_line(value), "")

    def test_limit_boundary(self):
        self.assertEqual(evidence.one_line("x" * 5, 5), "xxxxx")
        self.assertEqual(evidence.one_line("x" * 6, 5), "xxxx…")
        self.assertEqual(len(evidence.one_line("y" * 500, evidence.QUOTE_LIMIT)), evidence.QUOTE_LIMIT)

    def test_summary_defaults_and_quote_escaping(self):
        line = evidence.summary({"message": 'say "no"\nnow'}, None)
        self.assertEqual(line, "omnilane refusal: failed_gate=unknown code=unknown "
                               "reason=\"say 'no' now\" quote=\"say 'no' now\"")
        self.assertNotIn("evidence=", line)

    def test_summary_prefers_reason_and_adds_evidence_path(self):
        line = evidence.summary({"failed_gate": "quota", "code": "Q1", "reason": "over budget",
                                 "message": "m"}, Path("/tmp/x y.json"))
        self.assertIn("failed_gate=quota code=Q1 reason=\"over budget\" quote=\"m\"", line)
        self.assertTrue(line.endswith("evidence=/tmp/x y.json"))


class StoreAndMainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cov3-refusal-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "home"
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("OMNILANE_REFUSAL_EVIDENCE", None)

    def run_main(self, stdin, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = evidence.main(["--home", str(self.home), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_store_writes_private_record(self):
        path = evidence.store(self.home, {"allowed": False, "code": "C"}, "lane-a", "head")
        self.assertEqual(path.parent, self.home / "refusals")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual((record["lane"], record["headline"]), ("lane-a", "head"))
        self.assertEqual(record["refusal"], {"allowed": False, "code": "C"})
        self.assertRegex(record["recorded_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_no_refusal_returns_one_and_writes_nothing(self):
        code, out, err = self.run_main('{"allowed": true}\n')
        self.assertEqual((code, out, err), (1, "", ""))
        self.assertFalse(self.home.exists())

    def test_refusal_is_stored_and_path_printed(self):
        code, out, err = self.run_main("blocked\n" + refusal_line(code="Z", message="nope"),
                                       "--lane", "fast", "--headline", " two\n lines ")
        self.assertEqual(code, 0)
        path = Path(out.strip())
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["headline"], "two lines")
        self.assertIn("code=Z", err)
        self.assertIn("evidence=" + str(path), err)

    def test_opt_out_skips_storage(self):
        os.environ["OMNILANE_REFUSAL_EVIDENCE"] = "0"
        code, out, err = self.run_main(refusal_line(code="Z"))
        self.assertEqual((code, out), (0, ""))
        self.assertNotIn("evidence=", err)
        self.assertFalse(self.home.exists())

    def test_storage_failure_is_reported_but_not_fatal(self):
        self.home.parent.mkdir(parents=True, exist_ok=True)
        self.home.write_text("a file, not a directory", encoding="utf-8")
        code, out, err = self.run_main(refusal_line(code="Z"))
        self.assertEqual((code, out), (0, ""))
        self.assertIn("refusal evidence not stored (", err)
        self.assertIn("omnilane refusal: ", err)
        self.assertNotIn("evidence=", err)

    def test_home_is_required(self):
        with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                evidence.main([])
        self.assertEqual(raised.exception.code, 2)

    def test_script_entry_exits_with_main_status(self):
        with mock.patch("sys.argv", ["refusal_evidence.py", "--home", str(self.home)]), \
                mock.patch("sys.stdin", io.StringIO("no refusal here")):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(LIB / "refusal_evidence.py"), run_name="__main__")
        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
