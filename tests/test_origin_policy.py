"""Adversarial source-channel decisions and strict host-local persistence."""
import offline_env  # noqa: F401
import copy
import json
import os
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import origin_policy as op
import cli_provenance as cp

TEAM = "TEST000001"


def sample(kind="developer-id", **kwargs):
    value = dict(kind=kind, signer=TEAM if kind == "developer-id" else kind,
                 identifier="vendor.1.2.3", valid=kind in ("developer-id", "adhoc"))
    value.update(kwargs)
    return value


class PolicyTests(unittest.TestCase):
    def test_install_family_preserves_versioned_ancestors(self):
        for ancestor in ("/work/verify-0.55", "/Users/operator-1.2.3", "/work/0.55/1.2.3"):
            for suffix, expected in (
                (".local/share/claude/versions/2.1.276", ".local/share/claude/versions/<version>"),
                (".codex/packages/standalone/releases/0.55.0/bin/codex", ".codex/packages/standalone/releases/<version>/bin/codex"),
                (".grok/downloads/grok-1.0.25-macos-aarch64", ".grok/downloads/grok-<version>-macos-aarch64"),
                ("vendor/1.2.3/cli", "vendor/1.2.3/cli"),
                ("bin/tool-1.2.3", "bin/tool-1.2.3"),
            ):
                with self.subTest(ancestor=ancestor, suffix=suffix):
                    self.assertEqual(cp.family(ancestor + "/" + suffix), ancestor + "/" + expected)
        self.assertNotEqual(cp.family("/work/verify-0.55/vendor/1.2.3/cli"),
                            cp.family("/work/verify-0.56/vendor/1.2.3/cli"))
        self.assertEqual(cp.identifier_family("vendor.1.2.3"), "vendor.<version>")
        self.assertEqual(cp.family("/Users/1.2.3/cli"), "/Users/1.2.3/cli")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "verify-0.55" / "home-1.2.3"
        self.root.mkdir(parents=True)
        self.path = self.root / "origins.json"
        self.cli = self.root / ".local/share/claude/versions/1.2.3"
        self.cli.parent.mkdir(parents=True)
        self.cli.write_bytes(b"fixture")
        self.current = sample()
        self.recorded = dict(signer=TEAM, identifier="vendor.1.2.3", valid=True)
        self.trusted = dict(signer="adhoc", operator_trust=cp.TRUST_ADHOC)

    def channel(self, kind="developer-id", **changes):
        channel = op._channel(kind, self.cli, sample(kind))
        channel.update(changes)
        return channel

    def write(self, channels=None, **changes):
        data = dict(schema_version=1, host=socket.gethostname(),
                    vendors={"claude": {"channels": [self.channel()] if channels is None else channels}})
        data.update(changes)
        self.path.write_text(json.dumps(data))
        self.path.chmod(0o600)
        return data

    def gate(self, current=None, cli=None, vendor="claude", recorded=None):
        return op.gate(vendor, recorded, str(self.cli), current or self.current, cli or self.cli, self.path)

    def test_case03_unapproved_team_stops(self):
        self.write()
        self.assertEqual(self.gate(sample(signer="TEST000002")), (False, "team-changed"))

    def test_case04_same_team_different_location_stops(self):
        self.write()
        self.assertEqual(self.gate(cli=self.root / "other" / "cli"), (False, "location-changed"))

    def test_case05_different_identifier_stops(self):
        self.write()
        self.assertEqual(self.gate(sample(identifier="different.1.2.3")), (False, "identifier-changed"))
        self.assertTrue(self.gate(sample(identifier="vendor.2.3.4"))[0])

    def test_case11_observed_team_does_not_register_itself(self):
        self.assertFalse(self.gate()[0])
        self.assertFalse(self.path.exists())

    def test_case12_no_builtin_team_approval(self):
        self.assertEqual(op.FIRST_USE_KINDS, {"developer-id"} | op.LOCATION_KINDS)
        self.assertFalse(self.gate()[0])
        # Production code/docs must not embed a literal ten-character team.
        import re
        files = [ROOT / "scripts/lib/cli_provenance.py", ROOT / "scripts/lib/origin_policy.py", ROOT / "scripts/lib/resign.py"]
        files += list(ROOT.glob("README*.md")) + [ROOT / "skills/omnilane/SKILL.md"]
        for path in files:
            matches = re.findall(r'(?:--team\s+|TeamIdentifier=|team_id["\x27]?\s*[:=]\s*["\x27])([A-Z0-9]{10})\b', path.read_text())
            self.assertEqual(matches, [], path)

    def test_case13_no_adhoc_channel_stops(self):
        self.assertFalse(self.gate(sample("adhoc"))[0])

    def test_case14_factory_to_adhoc_requires_separate_channel(self):
        self.write()
        self.assertFalse(self.gate(sample("adhoc"))[0])

    def test_case15_adhoc_different_directory_stops(self):
        self.write([self.channel("adhoc")])
        self.assertFalse(self.gate(sample("adhoc"), self.root / "other" / "cli")[0])

    def test_case16_adhoc_version_only_path_allows_probe(self):
        self.write([self.channel("adhoc")])
        self.assertTrue(self.gate(sample("adhoc"), self.cli.with_name("2.3.4"))[0])

    def test_case17_resolved_paths_prevent_dot_symlink_case_tricks(self):
        self.write([self.channel("adhoc")])
        outside = self.root / "outside"
        outside.mkdir()
        link = self.cli.parent / "link"
        link.symlink_to(outside, target_is_directory=True)
        for path in (link / "cli", self.cli.parent / ".." / ".." / "outside" / "cli",
                     self.root / "VENDOR" / "1.2.3" / "cli"):
            with self.subTest(path=path):
                self.assertFalse(self.gate(sample("adhoc"), path)[0])

    def test_case18_vendor_channels_do_not_cross(self):
        self.write([self.channel("adhoc")])
        self.assertFalse(self.gate(sample("adhoc"), vendor="codex")[0])

    def test_case19_adhoc_channel_cannot_accept_unsigned(self):
        self.write([self.channel("adhoc")])
        self.assertFalse(self.gate(sample("unsigned"))[0])

    def test_case20_patchtmp_or_version_cannot_replace_a_channel(self):
        self.write([])
        self.assertFalse(self.gate(sample("adhoc", identifier="1.2.3.patchtmp."), recorded=self.trusted)[0])

    def test_case21_legacy_adhoc_without_trust_does_not_migrate(self):
        self.assertFalse(self.gate(sample("adhoc"), recorded={"signer": "adhoc"})[0])
        self.assertEqual(op.channels_for("claude", {"signer": "unsigned"}, str(self.cli), self.path), [])

    def test_case22_legacy_operator_trust_only_adds_adhoc(self):
        self.assertTrue(self.gate(sample("adhoc"), recorded=self.trusted)[0])
        self.assertFalse(self.gate(recorded=self.trusted)[0])
        self.assertFalse(self.path.exists())

    def test_case23_legacy_team_still_requires_valid_chain(self):
        self.assertTrue(self.gate(recorded=self.recorded)[0])
        self.assertFalse(self.gate(sample("invalid", signer=TEAM), recorded=self.recorded)[0])

    def test_case24_script_content_may_change_but_location_may_not(self):
        self.write([self.channel("script")])
        self.cli.write_bytes(b"#!/bin/sh\nchanged\n")
        self.assertTrue(self.gate(sample("script"))[0])
        self.assertFalse(self.gate(sample("script"), self.root / "elsewhere")[0])

    def test_case26_factory_to_script_stops(self):
        self.write()
        self.assertFalse(self.gate(sample("script"))[0])

    def test_case27_script_channel_cannot_accept_native_bad_signatures(self):
        self.write([self.channel("script")])
        for kind in ("unsigned", "invalid", "adhoc", "developer-id"):
            self.assertFalse(self.gate(sample(kind))[0])

    def test_case28_non_macos_location_change_stops(self):
        self.write([self.channel("no-platform-signature")])
        self.assertTrue(self.gate(sample("no-platform-signature"))[0])
        self.assertFalse(self.gate(sample("no-platform-signature"), self.root / "elsewhere")[0])

    def test_case29_first_use_only_on_a_pristine_vendor(self):
        for recorded, recorded_path in (({}, None), (self.recorded, None), (None, str(self.cli))):
            self.assertFalse(op.record_first_use("claude", self.current, self.cli, recorded, recorded_path, self.path))
        self.assertTrue(op.record_first_use("claude", self.current, self.cli, path=self.path))
        self.assertEqual(op.load(self.path)["vendors"]["claude"]["channels"][0]["approved_via"], "first-use")
        before = self.path.read_bytes()
        self.assertFalse(op.record_first_use("claude", self.current, self.cli, path=self.path))
        self.assertEqual(before, self.path.read_bytes())
        self.write([])
        self.assertFalse(op.record_first_use("claude", self.current, self.cli, path=self.path))

    def test_case30_first_use_unsafe_kinds_never_record(self):
        for kind in ("invalid", "unverifiable", "unsigned"):
            self.assertFalse(op.record_first_use("claude", sample(kind), self.cli, path=self.path))
        self.assertFalse(self.path.exists())
        with patch.object(op, "FIRST_USE_KINDS", {"script", "no-platform-signature", "invalid"}):
            self.assertFalse(op.record_first_use("claude", sample("invalid"), self.cli, path=self.path))
            self.assertTrue(op.record_first_use("claude", sample("script"), self.cli, path=self.path))
        path = self.root / "unsafe.json"
        with patch.object(op, "FIRST_USE_KINDS", op.FIRST_USE_KINDS | op.UNSAFE_KINDS):
            for kind in sorted(op.UNSAFE_KINDS):
                with self.subTest(kind=kind):
                    self.assertFalse(op.record_first_use("claude", sample(kind), self.cli, path=path))
        self.assertFalse(path.exists())

    def test_first_use_location_channels_round_trip(self):
        for kind in sorted(op.LOCATION_KINDS):
            with self.subTest(kind=kind):
                self.path = self.root / (kind + ".json")
                current = sample(kind)
                self.assertTrue(op.record_first_use("claude", current, self.cli, path=self.path))
                channels = op.load(self.path)["vendors"]["claude"]["channels"]
                self.assertEqual(len(channels), 1)
                self.assertEqual(channels, [{
                    "kind": "location", "accepts": kind, "approved_via": "first-use",
                    "install_family": str(self.cli.with_name("<version>")),
                    "approved_at": channels[0]["approved_at"],
                }])
                before = self.path.read_bytes()
                self.assertFalse(op.record_first_use("claude", current, self.cli, path=self.path))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.gate(current), (True, "origin-approved"))
                self.assertEqual(self.gate(current, self.cli.with_name("2.3.4")),
                                 (True, "origin-approved"))
                self.assertEqual(self.gate(current, self.root / "other" / "cli"),
                                 (False, "location-changed"))
                for other in sorted(({"developer-id"} | op.LOCATION_KINDS) - {kind}):
                    with self.subTest(other=other):
                        self.assertEqual(self.gate(sample(other)), (False, "origin-unapproved"))

    def test_first_use_developer_id_still_rejects_location_kinds(self):
        self.assertTrue(op.record_first_use("claude", self.current, self.cli, path=self.path))
        self.assertEqual(self.gate(), (True, "origin-approved"))
        for kind in sorted(op.LOCATION_KINDS):
            with self.subTest(kind=kind):
                self.assertEqual(self.gate(sample(kind)), (False, "origin-unapproved"))

    def test_first_use_adhoc_requires_valid_signature_and_signer(self):
        for valid in (False, None, 0, 1, "true"):
            with self.subTest(valid=valid):
                self.assertFalse(op.record_first_use("claude", sample("adhoc", valid=valid),
                                                    self.cli, path=self.path))
        for signer in (None, "unsigned", TEAM, False, []):
            with self.subTest(signer=signer):
                self.assertFalse(op.record_first_use("claude", sample("adhoc", signer=signer),
                                                    self.cli, path=self.path))
        current = sample("adhoc")
        del current["signer"]
        self.assertFalse(op.record_first_use("claude", current, self.cli, path=self.path))
        self.assertFalse(self.path.exists())

    def test_first_use_existing_overlay_blocks_every_kind(self):
        for kind in sorted(op.FIRST_USE_KINDS):
            for recorded, recorded_path in (({}, None), (self.recorded, None), (self.trusted, None),
                                            (None, str(self.cli)), (self.recorded, str(self.cli))):
                with self.subTest(kind=kind, recorded=recorded, recorded_path=recorded_path):
                    self.assertFalse(op.record_first_use("claude", sample(kind), self.cli,
                                                        recorded, recorded_path, self.path))
        self.assertFalse(self.path.exists())

    def test_first_use_existing_policy_blocks_every_kind(self):
        for channels in ([], [self.channel()]):
            self.write(channels)
            before = self.path.read_bytes()
            for kind in sorted(op.FIRST_USE_KINDS):
                with self.subTest(kind=kind, channels=channels):
                    self.assertFalse(op.record_first_use("claude", sample(kind), self.cli, path=self.path))
                    self.assertEqual(self.path.read_bytes(), before)

    def test_first_use_invalid_path_returns_false(self):
        for kind in sorted(op.FIRST_USE_KINDS):
            for index, cli in enumerate((None, "", "relative/cli")):
                with self.subTest(kind=kind, cli=cli):
                    path = self.root / (kind + "-" + str(index) + ".json")
                    self.assertFalse(op.record_first_use("claude", sample(kind), cli, path=path))
                    self.assertFalse(path.exists())
                    self.assertTrue(op.record_first_use("claude", sample(kind), self.cli, path=path))

    def test_first_use_invalid_path_preserves_other_vendor_policy(self):
        for kind in sorted(op.FIRST_USE_KINDS):
            with self.subTest(kind=kind):
                original = self.write(vendors={"codex": {"channels": [self.channel()]}})
                before = self.path.read_bytes()
                for cli in (None, "", "relative/cli"):
                    with self.subTest(cli=cli):
                        self.assertFalse(op.record_first_use("claude", sample(kind), cli, path=self.path))
                        self.assertEqual(self.path.read_bytes(), before)
                self.assertTrue(op.record_first_use("claude", sample(kind), self.cli, path=self.path))
                self.assertEqual(op.load(self.path)["vendors"]["codex"], original["vendors"]["codex"])

    def test_first_use_path_resolution_errors_return_false(self):
        for kind in sorted(op.FIRST_USE_KINDS):
            for error in (OSError, RuntimeError):
                with self.subTest(kind=kind, error=error.__name__):
                    with patch.object(op.Path, "resolve", side_effect=error("bad path")):
                        self.assertFalse(op.record_first_use("claude", sample(kind), self.cli, path=self.path))
                    self.assertFalse(self.path.exists())
        self.assertTrue(op.record_first_use("claude", self.current, self.cli, path=self.path))

    def test_first_use_invalid_policy_path_returns_false(self):
        before = self.cli.read_bytes()
        for kind in sorted(op.FIRST_USE_KINDS):
            with self.subTest(kind=kind):
                self.assertFalse(op.record_first_use("claude", sample(kind), self.cli,
                                                    path=self.cli / "origins.json"))
                self.assertEqual(self.cli.read_bytes(), before)
        self.assertTrue(op.record_first_use("claude", self.current, self.cli, path=self.path))

    def test_first_use_invalid_policy_returns_false(self):
        self.write(vendors={})
        foreign = dict(schema_version=1, host="another-host", vendors={})
        for raw in (b"{broken", json.dumps(foreign).encode()):
            self.path.write_bytes(raw)
            with self.assertRaises(op.OriginError):
                op.load(self.path)
            for kind in sorted(op.FIRST_USE_KINDS):
                with self.subTest(kind=kind, raw=raw):
                    self.assertFalse(op.record_first_use("codex", sample(kind), self.cli, path=self.path))
                    self.assertEqual(self.path.read_bytes(), raw)
        self.write(vendors={})
        self.assertTrue(op.record_first_use("codex", self.current, self.cli, path=self.path))

    def test_first_use_is_per_vendor_today(self):
        # The current rule is per vendor, pending the phase-2 decision.
        original = self.write()
        self.assertTrue(op.record_first_use("codex", self.current, self.cli, path=self.path))
        vendors = op.load(self.path)["vendors"]
        self.assertEqual(vendors["claude"], original["vendors"]["claude"])
        self.assertEqual(vendors["codex"]["channels"][0]["approved_via"], "first-use")

    def test_first_use_developer_id_malformed_identity_is_refused(self):
        for key, values in (("signer", (None, "adhoc", "short", [], {})),
                            ("identifier", (None, "", "bad\nidentifier", [], {}))):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertFalse(op.record_first_use("claude", sample(**{key: value}),
                                                        self.cli, path=self.path))
        self.assertFalse(self.path.exists())

    def test_case31_host_mismatch_rejects_entire_file(self):
        self.write(host="another-host")
        self.assertEqual(self.gate(recorded=self.recorded), (False, "policy-invalid"))

    def test_case32_duplicate_unknown_missing_and_malformed_fields_stop(self):
        original = self.write()
        for key in ("schema_version", "host", "vendors"):
            broken = copy.deepcopy(original)
            del broken[key]
            self.path.write_text(json.dumps(broken))
            self.assertFalse(self.gate(recorded=self.recorded)[0], key)
        for kind in ("developer-id", "adhoc"):
            channel = self.channel(kind)
            for key in channel:
                for change in ("missing", None, [], {}):
                    broken = copy.deepcopy(channel)
                    if change == "missing":
                        del broken[key]
                    else:
                        broken[key] = change
                    self.write([broken])
                    self.assertEqual(self.gate(sample(kind), recorded=self.recorded), (False, "policy-invalid"), (key, change))
        for channels in ([dict(self.channel(), surprise=1)], [dict(self.channel(), kind="unknown")],
                         [self.channel(), self.channel()]):
            self.write(channels)
            self.assertFalse(self.gate()[0])
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        self.assertFalse(self.gate()[0])
        self.write(schema_version=True)
        self.assertFalse(self.gate()[0])
        self.path.write_text('{"schema_version":NaN}')
        self.assertFalse(self.gate()[0])
        for vendors in (None, [], {"unknown": {"channels": []}}, {"claude": {}},
                        {"claude": {"channels": None}}, {"claude": {"channels": [], "extra": True}}):
            self.write(vendors=vendors)
            self.assertFalse(self.gate()[0], vendors)
        for changes in ({"install_family": "relative/path"}, {"install_family": "/a/../b"},
                        {"approved_at": "2026-10-11"}, {"approved_via": "unknown"}):
            self.write([dict(self.channel(), **changes)])
            self.assertFalse(self.gate()[0], changes)

    def test_case33_symlink_size_type_and_permissions_rejected(self):
        target = self.root / "target"
        target.write_text("{}")
        self.path.symlink_to(target)
        self.assertFalse(self.gate(recorded=self.recorded)[0])
        self.path.unlink()
        self.path.mkdir()
        self.assertFalse(self.gate()[0])
        self.path.rmdir()
        os.mkfifo(self.path, 0o600)
        self.assertFalse(self.gate()[0])
        self.path.unlink()
        self.write()
        self.path.write_bytes(b" " * (op.MAX_BYTES + 1))
        self.assertFalse(self.gate()[0])
        self.write()
        self.path.chmod(0o644)
        self.assertFalse(self.gate()[0])

    def test_case34_empty_explicit_channels_override_legacy_trust(self):
        self.write([])
        self.assertFalse(self.gate(sample("adhoc"), recorded=self.trusted)[0])

    def test_case35_revocation_remains_authoritative(self):
        self.write([self.channel("adhoc"), self.channel()])
        op.revoke("claude", "adhoc", self.trusted, str(self.cli), self.path)
        self.assertFalse(self.gate(sample("adhoc"), recorded=self.trusted)[0])
        self.assertTrue(self.gate()[0])
        op.revoke("claude", "developer-id", path=self.path)
        self.assertEqual(op.load(self.path)["vendors"]["claude"]["channels"], [])

    def test_case36_interrupted_write_keeps_old_bytes_and_backup(self):
        self.write()
        before = self.path.read_bytes()
        with patch.object(op.os, "replace", side_effect=OSError("interrupted")), self.assertRaises(OSError):
            op.record("claude", self.channel("adhoc"), path=self.path)
        self.assertEqual(self.path.read_bytes(), before)
        backups = list(self.root.glob("origins.json.before-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertFalse(list(self.root.glob("origins.json.tmp-*")))
        op.record("claude", self.channel("adhoc"), path=self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        # Restore the saved bytes: a revoked or added channel is reversible.
        self.path.write_bytes(before)
        self.assertFalse(self.gate(sample("adhoc"))[0])

    def test_case37_adding_either_channel_preserves_the_other(self):
        op.record("claude", self.channel(), self.trusted, str(self.cli), self.path)
        self.assertTrue(self.gate(sample("adhoc"))[0])
        self.assertTrue(self.gate()[0])
        op.record("claude", self.channel("adhoc"), path=self.path)
        self.assertTrue(self.gate()[0])

    def test_case38_copied_host_policy_is_not_portable(self):
        self.write()
        with patch.object(op.socket, "gethostname", return_value="second-host"):
            self.assertFalse(self.gate(recorded=self.recorded)[0])

    def test_missing_current_fields_never_grant_trust(self):
        self.write([self.channel(), self.channel("adhoc")])
        for key in ("kind", "valid", "signer", "identifier"):
            for value in (None, [], {}):
                broken = sample()
                broken[key] = value
                self.assertFalse(self.gate(broken)[0], (key, value))
            broken = sample()
            del broken[key]
            self.assertFalse(self.gate(broken)[0], key)
        self.assertFalse(op.gate("claude", self.recorded, str(self.cli), sample(), None, self.path)[0])

    def test_invalid_observed_team_is_not_a_legacy_approval(self):
        for record in ({"signer": TEAM}, dict(self.recorded, valid=False), dict(self.recorded, identifier=None),
                       dict(self.recorded, valid="true"), dict(self.recorded, kind="invalid"),
                       dict(self.recorded, kind="unverifiable")):
            self.assertFalse(self.gate(recorded=record)[0], record)
        for key in ("kind", "valid", "signer", "identifier"):
            current = sample()
            del current[key]
            self.assertFalse(op.record_first_use("claude", current, self.cli, path=self.path), key)

    def test_oversized_writes_leave_existing_policy_unchanged(self):
        self.write()
        before = self.path.read_bytes()
        channels = [self.channel(install_family="/" + "a" * 4000 + str(i)) for i in range(40)]
        with self.assertRaises(op.OriginError):
            op.record_many([("claude", c, None, None) for c in channels], self.path)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
