"""A product suffix never shares a family with the version annotation it resembles."""
import errno
import offline_env  # noqa: F401
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import cli_provenance as provenance
import origin_policy as op
import verification_status

TEAM = "TEST000001"
CLI = "/opt/vendor/cli"
# What doctor's channels show on both hosts; a change here would need a new approval.
APPROVED = {"claude": "com.anthropic.claude-code", "codex": "codex",
            "gemini": "cli", "grok": "xai-grok-pager"}


def signed(identifier):
    return dict(kind="developer-id", signer=TEAM, identifier=identifier, valid=True)


class GateCollisionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.policy = Path(directory.name) / "origins.json"

    def approve(self, identifier, vendor="codex"):
        op.record(vendor, op._channel("developer-id", CLI, signed(identifier)), path=self.policy)

    def gate(self, identifier, vendor="codex"):
        return op.gate(vendor, None, None, signed(identifier), CLI, path=self.policy)

    def test_a_product_suffix_after_an_annotation_is_a_different_program(self):
        self.approve("vendor.1.2.3-beta")
        self.assertEqual(self.gate("vendor.1.2.3-beta.bridge.4.5"), (False, "identifier-changed"))

    def test_no_annotation_can_prove_it_is_the_same_product(self):
        # "+decade1" is valid hex and "-dev" a release word, yet either could name a program.
        for grant in ("vendor.1.2.3-beta", "vendor.1.2.3"):
            for identifier in ("vendor.1.2.3+decade1", "vendor.1.2.3+cafe123", "vendor.1.2.3-dev",
                               "vendor.1.2.3+build", "vendor.1.2.3-bridge", "vendor.1.2.3-beta-bridge",
                               "vendor.1.2.3-beta.1.bridge", "vendor.1.2.3+bridge", "vendor.1.2.3+bridge.4.5",
                               "vendor.1.2.4-beta", "vendor.2.0.0-rc.1", "vendor.2.0.0+build.7", "vendor.2.0.0-1"):
                with self.subTest(grant=grant, identifier=identifier):
                    self.approve(grant)
                    self.assertEqual(self.gate(identifier), (False, "identifier-changed"))

    def test_an_annotated_grant_does_not_cover_the_plain_version(self):
        self.approve("vendor.1.2.3")
        self.assertEqual(self.gate("vendor.1.2.3-beta"), (False, "identifier-changed"))
        self.approve("vendor.1.2.3-beta")
        self.assertEqual(self.gate("vendor.1.2.3"), (False, "identifier-changed"))
        self.assertEqual(self.gate("vendor.1.2.3-beta"), (True, "origin-approved"))

    def test_a_grant_stored_before_this_rule_does_not_cover_the_suffix(self):
        channel = op._channel("developer-id", CLI, signed("vendor.1.2.3"))
        self.assertEqual(channel["identifier_family"], "vendor.<version>")
        op.record("codex", channel, path=self.policy)
        self.assertEqual(self.gate("vendor.9.0.0-beta.bridge.4.5"), (False, "identifier-changed"))

    def test_plain_version_updates_stay_approved(self):
        self.approve("vendor.1.2.3")
        self.assertEqual(provenance.identifier_family("vendor.1.2.3"), provenance.identifier_family("vendor.1.2.4"))
        for identifier in ("vendor.1.2.4", "vendor.2.0", "vendor.10.20.30.40"):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.gate(identifier), (True, "origin-approved"))

    def test_an_ambiguous_identifier_needs_and_can_get_its_own_approval(self):
        self.approve("vendor.1.2.3-beta.bridge.4.5")
        self.assertEqual(self.gate("vendor.1.2.3-beta.bridge.4.5"), (True, "origin-approved"))
        self.assertEqual(self.gate("vendor.1.2.3-beta.bridge.4.6"), (False, "identifier-changed"))
        self.assertEqual(self.gate("vendor.1.2.3-beta"), (False, "identifier-changed"))

    def test_identifiers_approved_on_both_hosts_keep_their_families(self):
        for vendor, identifier in APPROVED.items():
            with self.subTest(vendor=vendor):
                self.assertEqual(provenance.identifier_family(identifier), identifier)
                self.approve(identifier, vendor)
                self.assertEqual(self.gate(identifier, vendor), (True, "origin-approved"))


class FamilyTests(unittest.TestCase):
    def test_an_ambiguous_identifier_is_kept_whole_and_stays_stable(self):
        for identifier in ("vendor.1.2.3-beta.bridge.4.5", "a.1.2-x.b.3.4", "vendor-1.2.3-bridge", "vendor.1.2.3-beta",
                           "vendor.1.2.3-rc.1", "vendor.1.2.3+build.7", "vendor.1.2.3+decade1", "vendor-1.2.3-4"):
            with self.subTest(identifier=identifier):
                self.assertEqual(provenance.identifier_family(identifier), identifier)
                self.assertEqual(provenance.identifier_family(provenance.identifier_family(identifier)), identifier)

    def test_install_locations_keep_their_version_rule(self):
        # Stored install families must not change, or every approved channel would stop matching.
        self.assertEqual(provenance.family("/h/.local/share/claude/versions/2.1.276-beta.1"),
                         "/h/.local/share/claude/versions/<version>")
        self.assertEqual(provenance.family("/h/.codex/packages/standalone/releases/0.165.0-alpha.1/bin/codex"),
                         "/h/.codex/packages/standalone/releases/<version>/bin/codex")


class GuidanceTests(unittest.TestCase):
    def guidance(self, identifier):
        return verification_status.origin_guidance("codex", "identifier-changed", signed(identifier))

    def test_a_refused_annotated_identifier_explains_how_to_approve_it(self):
        for identifier in ("vendor.1.2.3-hotfix", "vendor.1.2.3-beta", "vendor.1.2.3+build.7"):
            with self.subTest(identifier=identifier):
                guidance = self.guidance(identifier)
                self.assertEqual(guidance["next_command"], "omnilane resign --trust-origin codex")
                self.assertIn("each new identifier needs its own approval", guidance["message"])
                self.assertIn("omnilane resign --trust-origin codex", guidance["message"])
                self.assertIn("每個新的識別名稱都要各自核准", guidance["message_zh_tw"])
                self.assertIn("omnilane resign --trust-origin codex", guidance["message_zh_tw"])

    def test_a_plainly_different_identifier_gets_no_such_note(self):
        for identifier in ("other.product", "vendor.1.2.3", "vendor.1.2.3.bridge.4.5"):
            with self.subTest(identifier=identifier):
                guidance = self.guidance(identifier)
                self.assertEqual(guidance["next_command"], "omnilane resign --trust-origin codex")
                self.assertNotIn("each new identifier", guidance["message"])
                self.assertNotIn("各自核准", guidance["message_zh_tw"])


class FailingStream:
    def __init__(self, stream, failure, error):
        self.stream, self.failure, self.error = stream, failure, error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, data):
        if self.failure == "write":
            raise self.error
        return self.stream.write(data)

    def flush(self):
        if self.failure == "flush":
            raise self.error
        return self.stream.flush()

    def fileno(self):
        return self.stream.fileno()


class BackupDescriptorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "policy.json"
        self.data = op.load(self.path)
        op._save(self.path, self.data)
        self.before = self.path.read_bytes()

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as caught:
            os.fstat(fd)
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def assert_policy_kept(self):
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(list(self.directory.glob("policy.json.tmp-*")), [])

    def test_backup_fdopen_failure_closes_descriptor_once_and_keeps_the_policy(self):
        error = OSError("FDOPEN_FAIL")
        real_fdopen, opened = os.fdopen, []

        def fdopen(fd, mode="r", *args, **kwargs):
            if mode == "wb":
                opened.append(fd)
                raise error
            return real_fdopen(fd, mode, *args, **kwargs)
        with patch.object(op.os, "fdopen", side_effect=fdopen), \
                patch.object(op.os, "close", wraps=os.close) as close:
            with self.assertRaises(OSError) as caught:
                op._save(self.path, self.data)
            self.assertIs(caught.exception, error)
            self.assertEqual(len(opened), 1)
            close.assert_called_once_with(opened[0])
        self.assert_closed(opened[0])
        self.assert_policy_kept()

    def test_a_failure_while_writing_the_backup_closes_descriptor_once(self):
        for failure in ("write", "flush", "fsync"):
            with self.subTest(failure=failure):
                error = OSError("BACKUP_FAIL")
                real_fdopen, opened = os.fdopen, []

                def fdopen(fd, mode="r", *args, **kwargs):
                    stream = real_fdopen(fd, mode, *args, **kwargs)
                    if mode != "wb":
                        return stream
                    opened.append(fd)
                    return FailingStream(stream, failure, error)
                with patch.object(op.os, "fdopen", side_effect=fdopen), \
                        patch.object(op.os, "fsync", side_effect=error if failure == "fsync" else None), \
                        patch.object(op.os, "close", wraps=os.close) as close:
                    with self.assertRaises(OSError) as caught:
                        op._save(self.path, self.data)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(len(opened), 1)
                    close.assert_called_once_with(opened[0])
                self.assert_closed(opened[0])
                self.assert_policy_kept()

    def test_a_successful_backup_closes_each_descriptor_once_and_keeps_the_previous_policy(self):
        real_fdopen, opened = os.fdopen, []

        def fdopen(fd, mode="r", *args, **kwargs):
            if mode == "wb":
                opened.append(fd)
            return real_fdopen(fd, mode, *args, **kwargs)
        with patch.object(op.os, "fdopen", side_effect=fdopen), \
                patch.object(op.os, "close", wraps=os.close) as close:
            op._save(self.path, self.data)
            self.assertEqual(len(opened), 2)
            self.assertEqual([c.args[0] for c in close.call_args_list], opened)
        for fd in opened:
            self.assert_closed(fd)
        backups = list(self.directory.glob("policy.json.before-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.before)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
