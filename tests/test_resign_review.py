"""Offline regressions for the P1C adversarial review findings."""
from __future__ import annotations

import offline_env  # noqa: F401
import copy
import hashlib
import json
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from test_probe_retry import RetryFixture
import aa_policy
import build_overlay
import probe_sweep
import resign


def rewrite(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def complete_success(root, vendor, entry):
    """A complete synthetic historical probe using the unchanged verdict engine."""
    from probe_fixture import write_probe
    write_probe(root / 'evidence', vendor, entry)


def reseal(overlay):
    """Construct a consistently pinned fixture; never used to hide test mutations."""
    for anchor in overlay['evidence']:
        if 'vendor' in anchor:
            continue
        from pathlib import Path
        path = Path(anchor['path'])
        manifest = json.loads(path.read_text())
        for files in manifest['probe_runs'].values():
            for file in files.values():
                file['sha256'] = hashlib.sha256(Path(file['path']).read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest))
        anchor['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        for mapping in overlay['mappings']:
            if 'evidence_refs' in mapping:
                mapping['evidence_refs'] = manifest['probe_runs'][mapping['config_id']]


class ReviewTests(RetryFixture, unittest.TestCase):
    def probe_path(self, entry):
        return self.sweep_root / 'evidence' / (entry['name'] + '.json')

    def install_built(self):
        self.build(self.sweep_root)
        self.live.write_bytes((self.sweep_root / 'transport-contracts.local.json').read_bytes())

    def test_missing_verdict_cannot_launder_negative_raw_evidence(self):
        for kind in ('wrong-model', 'wrong-effort', 'timeout', 'api'):
            with self.subTest(kind=kind):
                entry = self.fail_row(kind)
                rewrite(self.probe_path(entry), lambda d: (d.pop('verdict'), d.pop('evidence_tier')))
                self.install_built()
                self.assert_unverified(entry)
                registry = copy.deepcopy(build_overlay.REGISTRY)
                registry['snapshot']['id'] = 'p1c-next-snapshot'
                aa_policy.validate_transport_overlay(registry, self.overlay(), 'offline')
                row = next(r for r in registry['scored_configs'] if r['id'] == entry['config_id'])
                self.assertFalse(row['transport_mapping'].get('runtime_verified', False))

    def test_missing_verdict_requires_source_model_and_effort(self):
        entry = probe_sweep.plan('claude')[-1]
        self.write_probe(self.sweep_root, entry, 'pass')
        for field in ('model', 'effort'):
            with self.subTest(field=field):
                other = dict(entry, **{field: 'other-' + str(entry[field])})
                self.write_probe(self.sweep_root, other, 'pass')
                rewrite(self.probe_path(entry), lambda d: (d.pop('verdict'), d.pop('evidence_tier')))
                self.install_built()
                self.assert_unverified(entry)

    def test_missing_verdict_requires_complete_raw_evidence(self):
        entry = probe_sweep.plan('claude')[-1]
        for field in ('command', 'exit_code', 'timed_out'):
            with self.subTest(field=field):
                self.write_probe(self.sweep_root, entry, 'pass')
                rewrite(self.probe_path(entry), lambda d: (d.pop('verdict'), d.pop(field)))
                self.install_built()
                self.assert_unverified(entry)

    def test_explicit_pass_cannot_override_negative_raw_evidence(self):
        for kind in ('wrong-model', 'wrong-effort', 'timeout', 'api'):
            with self.subTest(kind=kind):
                entry = self.fail_row(kind)
                rewrite(self.probe_path(entry), lambda d: d.update(verdict='pass'))
                self.install_built()
                self.assert_unverified(entry)

    def test_explicit_pass_cannot_override_a_missing_request(self):
        entry = probe_sweep.plan('claude')[-1]
        self.write_probe(self.sweep_root, entry, 'pass')
        rewrite(self.probe_path(entry), lambda d: d.pop('command'))
        self.install_built()
        self.assert_unverified(entry)

    def test_explicit_pass_cannot_override_contradictory_observed_model(self):
        entry = probe_sweep.plan('claude')[-1]
        self.write_probe(self.sweep_root, entry, 'pass')
        rewrite(self.probe_path(entry), lambda d: d.update(observed_model='another-model'))
        self.install_built()
        self.assert_unverified(entry)

    def test_cooldown_rejects_two_matching_tampered_timestamps(self):
        for action in ('old', 'removed'):
            with self.subTest(action=action):
                entry = self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
                change = (lambda d: d.update(last_failed_at='2000-01-01T00:00:00+00:00')) if action == 'old' else (lambda d: d.pop('last_failed_at'))
                rewrite(self.probe_path(entry), change)
                self.change_overlay(lambda o: change(next(u for u in o['unproven'] if u['config_id'] == entry['config_id'])))
                before = self.live.read_bytes()
                self.assertEqual(self.run_retry(), resign.EXIT_HOST_CONFIG, self.lines)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.live.read_bytes(), before)

    def test_expired_mapping_reprobes_only_that_row(self):
        entry = probe_sweep.plan('claude')[-1]
        self.change_overlay(lambda o: next(m for m in o['mappings'] if m['config_id'] == entry['config_id']).update(expires_at='2000-01-01T00:00:00+00:00'))
        self.assertFalse(self.decision(entry)['allowed'])
        self.assertEqual(self.run_retry(), 0, self.lines)
        self.assertEqual(self.calls, [entry['config_id']])
        self.assertTrue(self.decision(entry)['allowed'])

    def test_conclusive_failure_guidance_does_not_offer_an_ineffective_retry(self):
        for kind in ('wrong-model', 'wrong-effort'):
            with self.subTest(kind=kind):
                entry = self.fail_row(kind)
                result = self.decision(entry)
                self.assertFalse(result['allowed'])
                self.assertEqual(result['next_command'], 'omnilane doctor')
                self.assertIn('will not retry automatically', result['message'])
                self.assertIn('不會自動重試', result['message_zh_tw'])
                self.assertEqual(self.run_retry(retry_failed=True), 0, self.lines)
                self.assertEqual(self.calls, [])
                self.assert_unverified(entry)

    def test_verified_and_unproven_row_is_rejected(self):
        entry = probe_sweep.plan('claude')[-1]
        self.change_overlay(lambda o: o['unproven'].append(dict(config_id=entry['config_id'], verdict_reason='model-mismatch')))
        with self.assertRaisesRegex(aa_policy.PolicyError, 'contradictory'):
            self.decision(entry)

    def test_all_complete_legacy_rows_keep_their_verified_status(self):
        entries = [(vendor, entry) for vendor in probe_sweep.VENDORS for entry in probe_sweep.plan(vendor)]
        for vendor, entry in entries:
            complete_success(self.sweep_root, vendor, entry)
        self.install_built()
        old = self.overlay()
        expected = {m['config_id'] for m in old['mappings']}
        for _, entry in entries:
            rewrite(self.probe_path(entry), lambda d: (d.pop('verdict'), d.pop('evidence_tier')))
        reseal(old)
        for schema in (1, 2):
            for cross in (False, True):
                with self.subTest(schema=schema, cross_snapshot=cross):
                    overlay = copy.deepcopy(old)
                    overlay['schema_version'] = schema
                    if schema == 1:
                        for mapping in overlay['mappings']:
                            for key in ('source_snapshot_id', 'source_row_digest', 'transport_contract_digest', 'evidence_refs', 'verified_at'):
                                mapping.pop(key)
                    registry = copy.deepcopy(build_overlay.REGISTRY)
                    if cross:
                        registry['snapshot']['id'] = 'p1c-complete-legacy-upgrade'
                    aa_policy.validate_transport_overlay(registry, overlay, 'offline')
                    self.assertEqual(set(registry['_transport_evidence_tiers']), expected)
        self.install_built()
        self.assertEqual({m['config_id'] for m in self.overlay()['mappings']}, expected)

    def test_legacy_missing_verdict_is_rejudged_at_carry_validation(self):
        entry = probe_sweep.plan('claude')[-1]
        complete_success(self.sweep_root, 'claude', entry)
        self.install_built()
        old = self.overlay()
        for kind in ('wrong-model', 'wrong-effort', 'timeout', 'api'):
            with self.subTest(kind=kind):
                self.write_probe(self.sweep_root, entry, kind)
                rewrite(self.probe_path(entry), lambda d: (d.pop('verdict'), d.pop('evidence_tier')))
                reseal(old)
                registry = copy.deepcopy(build_overlay.REGISTRY)
                registry['snapshot']['id'] = 'p1c-negative-legacy-upgrade'
                with self.assertRaisesRegex(aa_policy.PolicyError, 'transport proof did not pass'):
                    aa_policy.validate_transport_overlay(registry, old, 'offline')

    def test_retry_override_cannot_bypass_failure_integrity(self):
        entry = self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
        rewrite(self.probe_path(entry), lambda d: d.pop('last_failed_at'))
        self.change_overlay(lambda o: next(u for u in o['unproven'] if u['config_id'] == entry['config_id']).pop('last_failed_at'))
        self.assertEqual(self.run_retry(interactive=True, retry_failed=True), 40, self.lines)
        self.assertEqual(self.calls, [])

    def test_deleted_failure_manifest_metadata_is_not_legacy(self):
        self.fail_row(stamp=datetime.now(timezone.utc).isoformat())
        manifest = self.sweep_root / 'probe-manifest.json'
        rewrite(manifest, lambda d: (d.pop('failure_metadata_version'), d.pop('failed_probe_runs')))
        self.assertEqual(self.run_retry(), 40, self.lines)
        self.assertEqual(self.calls, [])

    def test_genuine_legacy_failure_without_time_still_retries(self):
        entry = self.fail_row()
        manifest = self.sweep_root / 'probe-manifest.json'
        rewrite(manifest, lambda d: (d.pop('failure_metadata_version'), d.pop('failed_probe_runs')))
        self.change_overlay(lambda o: next(e for e in o['evidence'] if e['path'] == str(manifest)).update(
            sha256=hashlib.sha256(manifest.read_bytes()).hexdigest()))
        self.assertEqual(self.run_retry(), 0, self.lines)
        self.assertEqual(self.calls, [entry['config_id']])

    def test_expired_descriptor_reprobes_only_that_row(self):
        entry = probe_sweep.plan('claude')[-1]
        rewrite(self.probe_path(entry), lambda d: d.update(expires_at='2000-01-01T00:00:00+00:00'))
        self.install_built()
        self.assertFalse(self.decision(entry)['allowed'])
        self.assertEqual(self.run_retry(), 0, self.lines)
        self.assertEqual(self.calls, [entry['config_id']])
        self.assertTrue(self.decision(entry)['allowed'])

    def test_expired_environment_failure_starts_cooldown(self):
        entry = probe_sweep.plan('claude')[-1]
        self.change_overlay(lambda o: next(m for m in o['mappings'] if m['config_id'] == entry['config_id']).update(expires_at='2000-01-01T00:00:00+00:00'))
        self.behavior[entry['config_id']] = 'api'
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assert_unverified(entry)
        self.assertIn('last_failed_at', self.failure(entry))
        self.assertEqual(self.run_retry(), 20, self.lines)
        self.assertEqual(self.calls, [entry['config_id']])

    def test_expired_refresh_waits_for_smoke_before_installing(self):
        entry = probe_sweep.plan('claude')[-1]
        self.change_overlay(lambda o: next(m for m in o['mappings'] if m['config_id'] == entry['config_id']).update(expires_at='2000-01-01T00:00:00+00:00'))
        before = self.live.read_bytes()
        with self.assertRaises(KeyboardInterrupt):
            self.run_retry(smoke=Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(self.live.read_bytes(), before)
        self.assertFalse(self.decision(entry)['allowed'])

    def test_cross_snapshot_expiry_is_only_missing_drift(self):
        entry = probe_sweep.plan('claude')[-1]
        self.change_overlay(lambda o: next(m for m in o['mappings'] if m['config_id'] == entry['config_id']).update(expires_at='2000-01-01T00:00:00+00:00'))
        from unittest.mock import patch
        with patch.dict(build_overlay.REGISTRY['snapshot'], id='p1c-expiry-upgrade'):
            report = resign.detect(self.overlay(), resign.current_anchors())['claude']
            self.assertTrue(report['only_missing'])
            self.assertEqual(report['expired'], [entry['config_id']])


if __name__ == '__main__':
    unittest.main()
