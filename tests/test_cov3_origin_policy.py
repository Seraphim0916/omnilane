"""In-process coverage for scripts/lib/origin_policy.py edge cases."""
import offline_env  # Activate suite isolation for direct file execution.
import home_isolation  # noqa: F401

import copy
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance
import origin_policy as origin

TEAM = "ABCDE12345"
STAMP = "2026-10-01T00:00:00+00:00"


def developer_channel(location="/opt/vendor/bin", team=TEAM, identifier="com.vendor.cli", **extra):
    channel = dict(kind="developer-id", install_family=location, approved_at=STAMP,
                   approved_via="operator", team_id=team, identifier_family=identifier)
    channel.update(extra)
    return channel


def location_channel(accepts="script", location="/opt/vendor/bin", **extra):
    channel = dict(kind="location", accepts=accepts, install_family=location,
                   approved_at=STAMP, approved_via="operator")
    channel.update(extra)
    return channel


def policy(**vendors):
    return {"schema_version": 1, "host": socket.gethostname(),
            "vendors": {vendor: {"channels": channels} for vendor, channels in vendors.items()}}


class PolicyDir(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cov3-origin-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "transport-origins.local.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.exe = self.bin / "codex"
        self.exe.write_text("#!/bin/sh\n", encoding="utf-8")
        self.family = cli_provenance.family(self.exe.resolve())

    def write_raw(self, raw, mode=0o600):
        self.path.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        os.chmod(self.path, mode)

    def write_policy(self, data):
        self.write_raw(json.dumps(data))

    def backups(self):
        return sorted(p for p in self.root.iterdir() if ".before-" in p.name)


class ValidateTests(unittest.TestCase):
    def assertInvalid(self, data, message):
        with self.assertRaisesRegex(origin.OriginError, message):
            origin.validate(data)

    def test_valid_policy_is_returned_unchanged(self):
        data = policy(codex=[developer_channel(), location_channel("adhoc")],
                      grok=[location_channel("no-platform-signature", approved_at="2026-10-01T00:00:00Z")])
        self.assertIs(origin.validate(data), data)

    def test_top_level_shape(self):
        self.assertInvalid([], "invalid origin policy fields")
        self.assertInvalid({**policy(), "extra": 1}, "invalid origin policy fields")
        for version in (True, 1.0, "1", 2):
            with self.subTest(version=version):
                self.assertInvalid({**policy(), "schema_version": version}, "invalid origin schema")
        self.assertInvalid({**policy(), "host": "elsewhere.invalid"}, "another host")
        self.assertInvalid({**policy(), "vendors": []}, "invalid origin vendors")
        self.assertInvalid(policy(openai=[]), "invalid origin vendors")

    def test_channel_container_shape(self):
        data = policy()
        for entry in ([], {"channels": {}}, {"channels": [], "extra": 1}):
            with self.subTest(entry=entry):
                data["vendors"] = {"codex": entry}
                self.assertInvalid(data, "invalid origin channels")
        self.assertInvalid(policy(codex=["developer-id"]), "^invalid channel$")
        self.assertInvalid(policy(codex=[{"kind": 1}]), "invalid channel kind")
        self.assertInvalid(policy(codex=[{"kind": "adhoc"}]), "unknown origin kind")

    def test_developer_id_fields(self):
        for team in ("abcde12345", "ABCDE1234", None, "ABCDE12345\n"):
            with self.subTest(team=team):
                self.assertInvalid(policy(codex=[developer_channel(team=team)]), "invalid origin team")
        for identifier in (None, 3, "a\nb", "x" * 4097):
            with self.subTest(identifier=identifier):
                self.assertInvalid(policy(codex=[developer_channel(identifier=identifier)]),
                                   "missing identifier family")
        origin.validate(policy(codex=[developer_channel(identifier="")]))
        self.assertInvalid(policy(codex=[developer_channel(accepts="script")]), "unknown or missing channel fields")

    def test_location_fields(self):
        for accepts in ("developer-id", "unsigned", None):
            with self.subTest(accepts=accepts):
                self.assertInvalid(policy(codex=[location_channel(accepts)]), "invalid location kind")
        missing = location_channel()
        del missing["approved_via"]
        self.assertInvalid(policy(codex=[missing]), "unknown or missing channel fields")

    def test_install_family_and_approval(self):
        for location in ("", "relative/bin", "/opt/../etc", "/opt/\tbin", 7):
            with self.subTest(location=location):
                self.assertInvalid(policy(codex=[location_channel(location=location)]), "invalid install family")
        self.assertInvalid(policy(codex=[location_channel(approved_via="first_use")]), "invalid approval source")
        for stamp in ("2026-10-01T00:00:00", "yesterday", None, 0):
            with self.subTest(stamp=stamp):
                self.assertInvalid(policy(codex=[location_channel(approved_at=stamp)]), "invalid approval time")
        origin.validate(policy(codex=[location_channel(approved_at="2026-10-01T00:00:00z")]))

    def test_duplicate_channel_key(self):
        self.assertInvalid(policy(codex=[location_channel(), location_channel(approved_via="first-use")]),
                           "duplicate channel kind and location")
        origin.validate(policy(codex=[location_channel("script"), location_channel("adhoc")]))
        self.assertInvalid(policy(codex=[developer_channel(), developer_channel(team="ZYXWV98765")]),
                           "duplicate channel")


class ReadAndLoadTests(PolicyDir):
    def test_missing_file_gives_an_empty_policy_for_this_host(self):
        self.assertEqual(origin.load(self.path),
                         {"schema_version": 1, "host": socket.gethostname(), "vendors": {}})

    def test_dangling_symlink_is_refused_not_treated_as_missing(self):
        # O_NOFOLLOW reports ELOOP for any final symlink, dangling or not.
        self.path.symlink_to(self.root / "absent.json")
        with self.assertRaisesRegex(origin.OriginError, "cannot open origin policy"):
            origin.load(self.path)

    def test_symlink_appearing_after_a_missing_open_is_refused(self):
        def vanished_then_linked(*args, **kwargs):
            self.path.symlink_to(self.root / "absent.json")
            raise FileNotFoundError(2, "No such file or directory")
        with mock.patch.object(origin.os, "open", vanished_then_linked):
            with self.assertRaisesRegex(origin.OriginError, "symbolic origin policy"):
                origin.load(self.path)

    def test_symlink_to_a_real_file_cannot_be_opened(self):
        target = self.root / "real.json"
        target.write_text(json.dumps(policy()), encoding="utf-8")
        os.chmod(target, 0o600)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(origin.OriginError, "cannot open origin policy"):
            origin.load(self.path)

    def test_mode_type_and_size(self):
        self.write_policy(policy())
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(origin.OriginError, "owned regular 0600"):
            origin.load(self.path)
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        with self.assertRaisesRegex(origin.OriginError, "owned regular 0600"):
            origin.load(self.path)
        self.path.unlink()
        self.write_raw(b" " * origin.MAX_BYTES + b"{}")
        with self.assertRaisesRegex(origin.OriginError, "too large"):
            origin.load(self.path)
        self.write_raw(b" " * (origin.MAX_BYTES - 2) + b"{}")
        with self.assertRaisesRegex(origin.OriginError, "invalid origin policy fields"):
            origin.load(self.path)

    def test_malformed_json_is_wrapped(self):
        cases = {
            '{"schema_version": 1, "schema_version": 1}': "duplicate JSON key",
            '{"schema_version": NaN}': "invalid JSON number",
            '{"x": Infinity}': "invalid JSON number",
            "{": "invalid origin policy: ",
            b"\xff\xfe": "invalid origin policy: ",
            "[" * 60000 + "]" * 60000: "invalid origin policy: ",
        }
        for raw, message in cases.items():
            with self.subTest(raw=raw[:20]):
                self.write_raw(raw)
                with self.assertRaisesRegex(origin.OriginError, message):
                    origin.load(self.path)

    def test_default_path_follows_omnilane_home(self):
        with mock.patch.dict(os.environ, {"OMNILANE_HOME": str(self.root)}):
            self.assertEqual(origin.policy_path(), self.path)
            self.write_policy(policy(codex=[location_channel()]))
            self.assertIn("codex", origin.load()["vendors"])


class ImplicitAndGateTests(PolicyDir):
    def recorded(self, **extra):
        value = dict(signer=TEAM, valid=True, identifier="com.vendor.codex-1.2.3")
        value.update(extra)
        return value

    def test_install_family_requires_an_absolute_path(self):
        for value in (None, "", "bin/codex", 3, b"/bin/codex"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(origin.OriginError, "missing or invalid executable path"):
                    origin.install_family(value)
        self.assertEqual(origin.install_family(str(self.exe)), self.family)

    def test_implicit_channels_from_recorded_overlay(self):
        channels = origin.implicit_channels(self.recorded(operator_trust=cli_provenance.TRUST_ADHOC), self.exe)
        self.assertEqual([c["kind"] for c in channels], ["developer-id", "location"])
        self.assertEqual(channels[0]["identifier_family"], "com.vendor.codex-<version>")
        self.assertEqual(channels[1]["accepts"], "adhoc")
        self.assertTrue(all(c["approved_via"] == "migrated-from-overlay" for c in channels))

    def test_implicit_channels_require_complete_valid_records(self):
        for recorded in (self.recorded(valid="true"), self.recorded(signer="adhoc"),
                         self.recorded(identifier=""), self.recorded(kind="adhoc"), "not a dict"):
            with self.subTest(recorded=recorded):
                self.assertEqual(origin.implicit_channels(recorded, self.exe), [])
        self.assertEqual(origin.implicit_channels(self.recorded(), None), [])

    def test_implicit_channels_swallow_bad_recorded_paths(self):
        self.assertEqual(origin.implicit_channels(self.recorded(), "relative/codex"), [])

    def test_channels_for_prefers_the_written_policy(self):
        with self.assertRaisesRegex(origin.OriginError, "unknown vendor"):
            origin.channels_for("openai", path=self.path)
        self.assertEqual(len(origin.channels_for("codex", self.recorded(), self.exe, self.path)), 1)
        self.write_policy(policy(codex=[]))
        self.assertEqual(origin.channels_for("codex", self.recorded(), self.exe, self.path), [])

    def gate(self, current, recorded=None, recorded_path=None):
        return origin.gate("codex", recorded, recorded_path, current, self.exe, self.path)

    def test_gate_refuses_unsafe_or_incomplete_observations(self):
        cases = [
            (None, "unverifiable"), ({"kind": 3}, "unverifiable"), ({"kind": "unsigned"}, "unsigned"),
            ({"kind": "invalid"}, "invalid"), ({"kind": "mystery"}, "unverifiable"),
            ({"kind": "developer-id", "valid": 1}, "invalid"),
            ({"kind": "developer-id", "valid": True, "signer": "short", "identifier": "x"}, "unverifiable"),
            ({"kind": "developer-id", "valid": True, "signer": TEAM, "identifier": ""}, "unverifiable"),
            ({"kind": "adhoc", "valid": True, "signer": TEAM}, "unverifiable"),
        ]
        for current, reason in cases:
            with self.subTest(current=current):
                self.assertEqual(self.gate(current), (False, reason))

    def test_gate_reports_damaged_policy(self):
        self.write_raw("{")
        self.assertEqual(self.gate({"kind": "script"}), (False, "policy-invalid"))
        self.path.unlink()
        self.assertEqual(origin.gate("codex", None, None, {"kind": "script"}, "rel", self.path),
                         (False, "policy-invalid"))

    def test_gate_location_and_team_decisions(self):
        current = {"kind": "developer-id", "valid": True, "signer": TEAM, "identifier": "com.vendor.codex-9.9"}
        self.assertEqual(self.gate(current), (False, "origin-unapproved"))
        self.write_policy(policy(codex=[developer_channel(location="/elsewhere")]))
        self.assertEqual(self.gate(current), (False, "location-changed"))
        self.write_policy(policy(codex=[developer_channel(location=self.family, team="ZYXWV98765")]))
        self.assertEqual(self.gate(current), (False, "team-changed"))
        self.write_policy(policy(codex=[developer_channel(location=self.family, identifier="com.other")]))
        self.assertEqual(self.gate(current), (False, "identifier-changed"))
        self.write_policy(policy(codex=[developer_channel(location=self.family, identifier="com.vendor.codex-<version>")]))
        self.assertEqual(self.gate(current), (True, "origin-approved"))
        self.write_policy(policy(codex=[developer_channel(location=self.family, identifier="")]))
        self.assertEqual(self.gate(current), (True, "origin-approved"))

    def test_gate_location_channels_match_only_their_kind(self):
        self.write_policy(policy(codex=[location_channel("script", location=self.family)]))
        self.assertEqual(self.gate({"kind": "script"}), (True, "origin-approved"))
        self.assertEqual(self.gate({"kind": "no-platform-signature"}), (False, "origin-unapproved"))
        self.assertEqual(self.gate({"kind": "adhoc", "valid": True, "signer": "adhoc"}), (False, "origin-unapproved"))


class WriteTests(PolicyDir):
    def test_record_creates_private_policy_then_backs_up_on_change(self):
        origin.record("codex", location_channel(location=self.family), path=self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.backups(), [])
        first = self.path.read_bytes()
        origin.record("codex", location_channel("adhoc", location=self.family), path=self.path)
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), first)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        accepts = [c["accepts"] for c in origin.load(self.path)["vendors"]["codex"]["channels"]]
        self.assertEqual(accepts, ["script", "adhoc"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if ".tmp-" in p.name), [])

    def test_record_replaces_same_key_and_migrates_implicit_grants(self):
        recorded = dict(signer=TEAM, valid=True, identifier="com.vendor.codex")
        origin.record("codex", location_channel(location=self.family), recorded, self.exe, path=self.path)
        channels = origin.load(self.path)["vendors"]["codex"]["channels"]
        self.assertEqual([c["kind"] for c in channels], ["developer-id", "location"])
        replacement = location_channel(location=self.family, approved_via="first-use")
        origin.record("codex", replacement, path=self.path)
        channels = origin.load(self.path)["vendors"]["codex"]["channels"]
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[-1]["approved_via"], "first-use")

    def test_record_many_refuses_unknown_vendor_without_writing(self):
        with self.assertRaisesRegex(origin.OriginError, "unknown vendor"):
            origin.record_many([("codex", location_channel(), None, None),
                                ("openai", location_channel(), None, None)], self.path)
        self.assertFalse(self.path.exists())

    def test_invalid_channel_is_refused_before_writing(self):
        with self.assertRaisesRegex(origin.OriginError, "invalid install family"):
            origin.record("codex", location_channel(location="relative"), path=self.path)
        self.assertFalse(self.path.exists())

    def test_oversized_policy_is_not_written(self):
        channels = [location_channel(location=f"/opt/{'d' * 4000}/{index}") for index in range(40)]
        with self.assertRaisesRegex(origin.OriginError, "too large"):
            origin.record_many([("codex", channel, None, None) for channel in channels], self.path)
        self.assertFalse(self.path.exists())

    def test_concurrent_change_aborts_the_replace_and_cleans_up(self):
        self.write_policy(policy())
        original = self.path.read_bytes()
        real_read = origin._read
        calls = []
        def racing_read(path):
            calls.append(path)
            return real_read(path) if len(calls) < 3 else b"changed"
        with mock.patch.object(origin, "_read", racing_read):
            with self.assertRaisesRegex(origin.OriginError, "changed during write"):
                origin.record("codex", location_channel(location=self.family), path=self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual([p for p in self.root.iterdir() if ".tmp-" in p.name], [])

    def test_lock_must_be_a_regular_file(self):
        os.mkfifo(str(self.path) + ".lock", 0o600)
        with self.assertRaisesRegex(origin.OriginError, "invalid origin lock"):
            origin.record("codex", location_channel(location=self.family), path=self.path)
        self.assertFalse(self.path.exists())

    def test_revoke(self):
        with self.assertRaisesRegex(origin.OriginError, "unknown vendor or channel kind"):
            origin.revoke("openai", "script", path=self.path)
        with self.assertRaisesRegex(origin.OriginError, "unknown vendor or channel kind"):
            origin.revoke("codex", "unsigned", path=self.path)
        self.write_policy(policy(codex=[developer_channel(location=self.family),
                                        location_channel("script", location=self.family),
                                        location_channel("adhoc", location=self.family)]))
        origin.revoke("codex", "script", path=self.path)
        kinds = [(c["kind"], c.get("accepts")) for c in origin.load(self.path)["vendors"]["codex"]["channels"]]
        self.assertEqual(kinds, [("developer-id", None), ("location", "adhoc")])
        origin.revoke("codex", "location", path=self.path)
        kinds = [c["kind"] for c in origin.load(self.path)["vendors"]["codex"]["channels"]]
        self.assertEqual(kinds, ["developer-id"])

    def test_revoke_records_an_empty_vendor_instead_of_falling_back(self):
        recorded = dict(signer=TEAM, valid=True, identifier="com.vendor.codex")
        origin.revoke("codex", "developer-id", recorded, self.exe, path=self.path)
        self.assertEqual(origin.load(self.path)["vendors"]["codex"], {"channels": []})
        self.assertEqual(origin.channels_for("codex", recorded, self.exe, self.path), [])

    def test_preserve_before_override(self):
        with self.assertRaisesRegex(origin.OriginError, "unknown vendor"):
            origin.preserve_before_override("openai", None, None, self.path)
        recorded = dict(signer=TEAM, valid=True, identifier="com.vendor.codex")
        origin.preserve_before_override("codex", recorded, self.exe, self.path)
        frozen = copy.deepcopy(origin.load(self.path))
        self.assertEqual(len(frozen["vendors"]["codex"]["channels"]), 1)
        origin.preserve_before_override("codex", dict(recorded, signer="ZYXWV98765"), self.exe, self.path)
        self.assertEqual(origin.load(self.path), frozen)
        self.assertEqual(self.backups(), [])

    def test_record_first_use(self):
        current = {"kind": "developer-id", "valid": True, "signer": TEAM, "identifier": "com.vendor.codex"}
        refused = [
            ("openai", current, {}),
            ("codex", current, {"recorded": {}}),
            ("codex", current, {"recorded_path": self.exe}),
            ("codex", None, {}),
            ("codex", {"kind": 1}, {}),
            ("codex", {"kind": "unsigned"}, {}),
            ("codex", {"kind": "developer-id", "valid": False}, {}),
            ("codex", {"kind": "adhoc", "valid": True, "signer": TEAM}, {}),
            ("codex", {"kind": "developer-id", "valid": True, "signer": TEAM}, {}),
            ("codex", {"kind": "developer-id", "valid": True, "signer": "bad", "identifier": "x"}, {}),
        ]
        for vendor, observed, extra in refused:
            with self.subTest(vendor=vendor, observed=observed, extra=extra):
                self.assertFalse(origin.record_first_use(vendor, observed, self.exe, path=self.path, **extra))
        self.assertEqual(origin.load(self.path)["vendors"], {})
        self.assertTrue(origin.record_first_use("codex", current, self.exe, path=self.path))
        channel = origin.load(self.path)["vendors"]["codex"]["channels"][0]
        self.assertEqual((channel["approved_via"], channel["team_id"]), ("first-use", TEAM))
        self.assertFalse(origin.record_first_use("codex", current, self.exe, path=self.path))

    def test_record_first_use_location_kinds_and_errors(self):
        self.assertTrue(origin.record_first_use("grok", {"kind": "script"}, self.exe, path=self.path))
        self.assertTrue(origin.record_first_use("claude", {"kind": "adhoc", "valid": True, "signer": "adhoc"},
                                                self.exe, path=self.path))
        vendors = origin.load(self.path)["vendors"]
        self.assertEqual(vendors["grok"]["channels"][0]["accepts"], "script")
        self.assertEqual(vendors["claude"]["channels"][0]["accepts"], "adhoc")
        self.assertFalse(origin.record_first_use("gemini", {"kind": "script"}, "relative", path=self.path))
        self.write_raw("{")
        self.assertFalse(origin.record_first_use("gemini", {"kind": "script"}, self.exe, path=self.path))


if __name__ == "__main__":
    unittest.main()
