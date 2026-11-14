"""Offline explicit-only proxy bounds, including the real dispatch dry-run surface."""
import offline_env
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tests import test_native_hydrogen_regression as hydrogen
from scripts import aa_rebaseline as build

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'gemini-pro-orchestrator-g1-pro'


class ExplicitProxyTests(unittest.TestCase):
    def setUp(self):
        self.f = hydrogen.VendorPinSkipsUnprovenRowsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.registry = self.f.registry

    def overlay(self, model):
        env = self.f.overlay(model)
        # Orchestrator has no encoded effort: its complete selector is the model id.
        if model == MODEL:
            path = Path(env['OMNILANE_AA_TRANSPORT_OVERLAY'])
            value = json.loads(path.read_text())
            value['mappings'][0]['selector_type'] = 'model_and_effort'
            path.write_text(json.dumps(value))
        return env

    def dry(self, model, ceiling, proof=True):
        env = self.overlay(model) if proof else self.f.fixture.env.copy()
        env, args = self.f.model_caller(env, 'codex/gpt-6-astra')
        path = Path(args[1]); caller = json.loads(path.read_text())
        caller['inherited_ceiling'] = ceiling
        path.write_text(json.dumps(caller))
        return self.f.fixture._run('--dry-run', '--vendor', 'gemini', '--model', model,
                                   *args, 'consult', 'offline fixture', env=env)

    def test_a_g_all_lanes_exclude_derived_rows_even_with_proof(self):
        derived = [r for r in self.registry['scored_configs'] if r.get('score_source')]
        selectors = {m for r in derived for m in r['transport_mapping']['candidate_model_ids']}
        chain_models = {spec.split()[1] for chain in self.f.lanes.values()
                        for spec in chain if len(spec.split()) > 1}
        self.assertFalse(selectors & chain_models)
        env = self.overlay(MODEL)
        for lane in self.f.lanes:
            result = self.f.fixture._run('--dry-run', lane, 'fixture', env=env)
            self.assertEqual(3 if lane == 'arbitrate' else 0, result.returncode, result.stdout + result.stderr)
            self.assertFalse(any('model=' + m + '\n' in result.stdout for m in selectors))
        self.assertFalse(any(r.get('score_source') for r in build.value_pool(self.registry['scored_configs'])))

    def test_b_c_explicit_proxy_ceiling(self):
        for ceiling, expected in [(54, 0), (57, 0), (53, 3)]:
            result = self.dry(MODEL, ceiling)
            self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
            self.assertIn('model=' + MODEL if expected == 0 else 'target-above-effective-ceiling',
                          result.stdout + result.stderr)

    def test_d_no_proof_including_human(self):
        result = self.dry(MODEL, 57, proof=False)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn('target-transport', result.stderr)
        result = self.f.fixture._run('--dry-run', '--vendor', 'gemini', '--model', MODEL,
                                    'consult', 'fixture')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn('target-transport', result.stderr)

    def test_f_argon_medium_bound(self):
        for ceiling, expected in [(52, 0), (49, 3)]:
            result = self.dry('gemini-4-argon-medium', ceiling)
            self.assertEqual(expected, result.returncode, result.stdout + result.stderr)

    def test_decision_records_proxy_provenance(self):
        from tests.test_aa_policy import aa_policy
        row = next(r for r in self.registry['scored_configs'] if r['id'] == build.PROXY_ID)
        row['transport_mapping'].update(status='verified', runtime_verified=True,
                                        runtime_model=MODEL, runtime_effort=None)
        caller_row = next(r for r in self.registry['scored_configs'] if r['id'] == 'codex/gpt-6-astra')
        caller = {'caller': {k: caller_row[k] for k in aa_policy.IDENTITY_FIELDS}, 'inherited_ceiling': 57}
        result = aa_policy.decide(self.registry, 'fixture', vendor='gemini', model=MODEL,
                                  effort=None, caller=caller, caller_sha256='fixture')
        self.assertTrue(result['allowed'])
        self.assertEqual('proxy', result['target_score_source'])
        self.assertEqual(list(build.PROXY_OF), result['target_proxy_of'])
        self.assertEqual(MODEL, result['target']['model'])

    def test_h_unknown_stays_denied(self):
        result = self.dry('gemini-no-higher-scored-row', 57, proof=False)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn('unknown-model-name', result.stderr)


class DerivedCallerTests(unittest.TestCase):
    def setUp(self):
        from tests.test_aa_policy import aa_policy
        self.policy = aa_policy
        self.registry = json.loads((ROOT / 'config/aa-model-policy.json').read_text())

    def test_derived_callers_cannot_authorize_dispatch_or_inheritance(self):
        # F-02: Argon low's upper bound must not authorize measured Argon high.
        target = next(r for r in self.registry['scored_configs']
                      if r['id'] == 'gemini/gemini-4-argon')
        target['transport_mapping'].update(status='verified', runtime_verified=True,
                                          runtime_model='gemini-4-argon-high', runtime_effort=None)
        for row in self.registry['scored_configs']:
            if not row.get('score_source'):
                continue
            with self.subTest(caller=row['id']):
                caller = {'caller': self.policy._row_identity(row), 'inherited_ceiling': 100}
                decisions = [
                    self.policy.decide(self.registry, 'fixture', vendor='gemini',
                                       model='gemini-4-argon-high', effort=None,
                                       caller=caller, caller_sha256='fixture'),
                    self.policy.decide_inherited(self.registry, 'fixture', caller=caller,
                                                 caller_sha256='fixture')]
                for result in decisions:
                    self.assertFalse(result['allowed'])
                    self.assertEqual('ineligible-caller-config', result['code'])
                    self.assertIsNone(result['effective_ceiling'])
                    self.assertIsNone(result['child_context'])

    def test_context_entry_rejects_derived_authority(self):
        import caller_identity
        for row in self.registry['scored_configs']:
            if not row.get('score_source'):
                continue
            with self.subTest(caller=row['id']):
                resolved, reason = caller_identity.resolve(
                    self.registry, row['vendor'], row['model'], row['effort'])
                self.assertIsNone(resolved)
                self.assertIn('authority', reason)
                floor = caller_identity.floor_row(self.registry, row['vendor'], row['model'])
                self.assertTrue(floor is None or not floor.get('score_source'))
                with tempfile.TemporaryDirectory() as directory:
                    home = Path(directory) / 'not-created'
                    with self.assertRaises(ValueError):
                        caller_identity.write_context(row, self.registry, home)
                    self.assertFalse(home.exists())


class DerivedBuildTests(unittest.TestCase):
    def rebuild(self, extract, base):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            capture = d / 'extract.json'; capture.write_text(json.dumps(extract))
            source = d / 'base.json'; source.write_text(json.dumps(base))
            target = d / 'policy.json'
            args = SimpleNamespace(extract=str(capture), base=str(source), as_of='2026-11-13',
                                   revision=2, approval='approved')
            with patch.object(build, 'REGISTRY', target), patch.object(build, 'REPO', d), \
                    contextlib.redirect_stdout(io.StringIO()):
                build.cmd_build(args)
            return json.loads(target.read_text())

    def test_e_recompute_and_idempotence(self):
        extract = json.loads((ROOT / 'docs/reports/aa-v5.0.1-extract-2026-11-13.json').read_text())
        base = json.loads((ROOT / 'config/aa-model-policy.json').read_text())
        extract['records']['gemini-4-hydrogen']['intelligenceIndex'] = 60.2
        result = self.rebuild(extract, base)
        row = next(r for r in result['scored_configs'] if r['id'] == build.PROXY_ID)
        self.assertEqual(60, row['score'])
        again = self.rebuild(extract, result)
        self.assertEqual(result['scored_configs'], again['scored_configs'])
        self.assertEqual(result['unknown_configs'], again['unknown_configs'])

    def test_minimum_higher_bound_and_no_cross_model_guess(self):
        registry = json.loads((ROOT / 'config/aa-model-policy.json').read_text())
        measured = [r for r in registry['scored_configs'] if not r.get('score_source')]
        unknown = copy.deepcopy(next(r for r in registry['scored_configs'] if r.get('score_source') == 'upper-bound'))
        unknown.update(vendor='gemini', model='gemini-4-hydrogen', effort='low', id='fixture/low')
        mystery = dict(unknown, id='fixture/unknown', model='unknown')
        scored, remaining = build.derive_scores(measured, [unknown, mystery])
        bounded = next(r for r in scored if r['id'] == 'fixture/low')
        self.assertEqual(41, bounded['score'])
        self.assertEqual('gemini/gemini-4-hydrogen-medium', bounded['bound_from'])
        self.assertEqual([mystery], remaining)


if __name__ == '__main__':
    unittest.main()
