import offline_env  # Activate suite isolation for direct file execution.

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import verification_status as status


class OriginDetailFieldsTests(unittest.TestCase):
    def assert_details(self, current, expected):
        before = copy.deepcopy(current)
        result = status.origin_guidance("codex", "invalid", current)
        details = []
        for field, marker, suffix in (
                ("message", "Observed: ", ". "),
                ("message_zh_tw", "觀察值：", "。")):
            with self.subTest(language=field):
                detail = result[field].split(marker, 1)[1]
                decoded, end = json.JSONDecoder().raw_decode(detail)
                self.assertEqual(decoded, expected)
                self.assertEqual(detail[:end], json.dumps(decoded, ensure_ascii=True))
                details.append(detail[:end])
                self.assertTrue(detail[end:].startswith(suffix))
                self.assertFalse(any(ord(char) < 32 or ord(char) == 127
                                     for char in result[field]))
        self.assertEqual(details[0], details[1])
        self.assertEqual(current, before)
        return result

    def test_only_allowed_string_fields_are_rendered(self):
        current = dict(signer="synthetic-signer", identifier="synthetic-id",
                       private_extra="EXTRA-MUST-NOT-APPEAR",
                       nested={"signer": "NESTED-MUST-NOT-APPEAR"})
        result = self.assert_details(current, {
            "signer": "synthetic-signer", "identifier": "synthetic-id"})
        for field in ("message", "message_zh_tw"):
            for forbidden in ("private_extra", "nested", "EXTRA-MUST-NOT-APPEAR",
                              "NESTED-MUST-NOT-APPEAR"):
                self.assertNotIn(forbidden, result[field])

    def test_non_string_values_are_excluded_independently(self):
        for value in (None, False, 0, 1.5, [], {}, ["synthetic"]):
            for field, other in (("signer", "identifier"), ("identifier", "signer")):
                with self.subTest(field=field, value=value):
                    self.assert_details({field: value, other: "kept",
                                         "observed_team": value}, {other: "kept"})
        self.assert_details({}, {})

    def test_string_observed_team_overrides_signer_including_empty_string(self):
        for signer in ("original", None, 42):
            for team in ("synthetic-team", ""):
                with self.subTest(signer=signer, team=team):
                    self.assert_details(dict(signer=signer, identifier="id",
                                             observed_team=team),
                                        dict(signer=team, identifier="id"))
        self.assert_details(dict(signer="", identifier=""),
                            dict(signer="", identifier=""))

    def test_control_characters_and_unicode_are_escaped_in_both_messages(self):
        control_text = '\n\r\t\x00\x1b\x7f"\\簽署者'
        for use_team in (False, True):
            current = dict(signer=control_text, identifier=control_text)
            if use_team:
                current.update(signer="replaced", observed_team=control_text)
            with self.subTest(use_team=use_team):
                self.assert_details(current, dict(signer=control_text,
                                                  identifier=control_text))


if __name__ == "__main__":
    unittest.main()
