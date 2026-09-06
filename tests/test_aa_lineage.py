#!/usr/bin/env python3
"""Offline CLI/native lineage acceptance. Provider spies are not live models."""
import copy
import importlib.util
import json
import os
import hashlib
import socket
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import aa_policy
import aa_retry

class LineageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aa-lineage-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home'; self.home.mkdir()
        self.registry = json.loads((ROOT/'config/aa-model-policy.json').read_text())
        # CLI tests use the exact approved bytes; only selector contracts are synthetic.
        self.policy = self.base/'registry.json'
        self.policy.write_bytes((ROOT/'config/aa-model-policy.json').read_bytes())
        caller = next(r for r in self.registry['scored_configs'] if r['id']=='codex/gpt-6-astra-medium')
        self.context_value = {'schema_version':1,'snapshot_id':self.registry['snapshot']['id'],
            'kind':'model','caller':{k:caller[k] for k in aa_policy.IDENTITY_FIELDS}, 'inherited_ceiling':52}
        self.context = self.base/'caller.json'; self.context.write_text(json.dumps(self.context_value))
        self.marker = self.base/'spy.jsonl'; self.bins = self.base/'bin'; self.bins.mkdir()
        spy = self.bins/'codex'
        spy.write_text('#!'+sys.executable+'\n'+'''import json,os,sys
from pathlib import Path
p=os.environ.get('OMNILANE_AA_CALLER_CONTEXT')
with open(os.environ['AA_SPY'],'a') as f:
 f.write(json.dumps({'args':sys.argv[1:],'context':json.loads(Path(p).read_text()) if p else None,'human':os.environ.get('OMNILANE_AA_OPERATOR_ASSERTED_HUMAN')})+'\\n')
if '-o' in sys.argv:
 Path(sys.argv[sys.argv.index('-o')+1]).write_text('SPY_OK\\n')
'''); spy.chmod(0o755)
        self.runtime_overlay=self.base/'runtime-overlay.json'
        mappings=[]
        for row in self.registry['scored_configs']:
            if row['vendor']=='codex':
                mappings.append({'config_id':row['id'],'identity':{k:row[k] for k in aa_policy.IDENTITY_FIELDS},
                    'runtime_model':row['model'],'runtime_effort':row['effort'],'verification':'request-selector-contract'})
        overlay={'schema_version':1,'snapshot_id':self.registry['snapshot']['id'],'host':socket.gethostname(),
            'evidence':[{'path':str(spy),'sha256':hashlib.sha256(spy.read_bytes()).hexdigest()}],'mappings':mappings}
        self.runtime_overlay.write_text(json.dumps(overlay))
        self.env={k:v for k,v in os.environ.items() if not k.startswith('OMNILANE_') and k not in ('CODEX_BIN','CLAUDE_BIN')}
        self.env.update(OMNILANE_HOME=str(self.home), CODEX_BIN=str(spy), AA_SPY=str(self.marker), PATH=str(self.bins)+os.pathsep+self.env['PATH'], OMNILANE_INBOX='0', OMNILANE_AA_TRANSPORT_OVERLAY=str(self.runtime_overlay))
        (self.home/'routing.local.yaml').write_text('aa-unit: codex gpt-6-astra xhigh | codex gpt-5.6-sol high\naa-vote: vote codex,claude 2\n')

    def run_dispatch(self, *args, lane='aa-unit'):
        return subprocess.run(['bash',str(ROOT/'scripts/dispatch.sh'),'--caller-context',str(self.context),'--aa-policy',str(self.policy),'--timeout','10',*args,lane,'Bounded offline test'],env=self.env,cwd=ROOT,text=True,capture_output=True,timeout=30)

    def test_cli_allowed_propagates_exact_child_not_parent(self):
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-5.6-sol','--effort','high')
        self.assertEqual(r.returncode,0,r.stderr)
        called=json.loads(self.marker.read_text().splitlines()[-1])
        self.assertEqual(called['context']['caller']['model'],'gpt-5.6-sol')
        self.assertEqual(called['context']['caller']['effort'],'high')
        self.assertEqual(called['context']['inherited_ceiling'],52)
        self.assertIsNone(called['human'])
        job=next((self.home/'jobs').iterdir())
        self.assertEqual(json.loads((job/'aa-authorizer.json').read_text())['caller']['model'],'gpt-6-astra')
        self.assertEqual((job/'aa-child-context.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(hashlib.sha256((job/'aa-registry.json').read_bytes()).hexdigest(),aa_policy.APPROVED_REGISTRY_SHA256)

    def test_explicit_upward_denied_without_provider(self):
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-6-astra','--effort','xhigh')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('target-above-effective-ceiling',r.stderr)
        self.assertFalse(self.marker.exists())

    def test_automatic_chain_skips_upward_candidate(self):
        r=self.run_dispatch('--executor','cli')
        self.assertEqual(r.returncode,0,r.stderr)
        called=json.loads(self.marker.read_text().splitlines()[-1])
        self.assertIn('gpt-5.6-sol',called['args'])
        self.assertNotIn('gpt-6-astra',called['args'])

    def test_retry_preserves_authorizer_target_and_ceiling(self):
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-5.6-sol','--effort','high','--target-config','codex/gpt-5-6-sol-high')
        self.assertEqual(r.returncode,0,r.stderr)
        job=next((self.home/'jobs').iterdir())
        with patch.dict(os.environ, {'OMNILANE_AA_CALLER_CONTEXT':str(self.context),'OMNILANE_AA_OPERATOR_ASSERTED_HUMAN':'0'}):
            self.assertIn('codex/gpt-5-6-sol-high',aa_retry.retry_args(job))
        r=subprocess.run(['bash',str(ROOT/'scripts/jobs.sh'),'retry',job.name,'--caller-context',str(self.context)],env=self.env,cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(len(self.marker.read_text().splitlines()),2)
        self.assertEqual(json.loads(self.marker.read_text().splitlines()[-1])['context']['inherited_ceiling'],52)

    def test_retry_tamper_fails_before_provider(self):
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-5.6-sol','--effort','high')
        self.assertEqual(r.returncode,0,r.stderr)
        job=next((self.home/'jobs').iterdir())
        (job/'aa-authorizer.json').write_text(json.dumps({**self.context_value,'inherited_ceiling':100}))
        r=subprocess.run(['bash',str(ROOT/'scripts/jobs.sh'),'retry',job.name,'--caller-context',str(self.context)],env=self.env,cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len(self.marker.read_text().splitlines()),1)

    def test_native_model_caller_handoff_has_atomic_child_context(self):
        cap={'schema_version':1,'harness':'codex','vendor':'codex', 'requirements':{'tools':[],'isolation':'shared-inherited','lifecycle':'single-shot'},'capabilities':[{'model':'gpt-5.6-sol','efforts':['high'],'modes':['advise'],'workdirs':[str(ROOT)],'tools':[],'isolations':['shared-inherited'],'lifecycles':['single-shot']}]}
        path=self.base/'native-cap.json';path.write_text(json.dumps(cap))
        r=self.run_dispatch('--executor','native','--native-context',str(path),'--vendor','codex','--model','gpt-5.6-sol','--effort','high')
        self.assertEqual(r.returncode,0,r.stderr)
        plan=json.loads(r.stdout)
        child=Path(plan['worker_contract']['caller_context_path'])
        self.assertEqual(json.loads(child.read_text())['caller']['model'],'gpt-5.6-sol')
        self.assertFalse(self.marker.exists())
        self.assertEqual(plan['aa_policy']['target_score'],48)

    def test_native_upward_denied_before_provider_or_job(self):
        r=self.run_dispatch('--executor','native','--vendor','codex','--model','gpt-6-astra','--effort','xhigh')
        self.assertNotEqual(r.returncode,0)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.home/'jobs').exists())

    def test_vote_rejects_upward_child_before_any_provider(self):
        r=self.run_dispatch('--executor','cli',lane='aa-vote')
        self.assertNotEqual(r.returncode,0)
        self.assertFalse(self.marker.exists())
        self.assertIn('target-above-effective-ceiling',r.stderr)

    def configure_gemini_spy(self):
        row=next(r for r in self.registry['scored_configs'] if r['id']=='gemini/gemini-3-8-flash')
        spy=self.bins/'agy';spy.write_text((self.bins/'codex').read_text()+"\nprint(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'SPY_GEMINI_OK'}}))\n");spy.chmod(0o755)
        self.env['AGY_BIN']=str(spy)
        overlay=json.loads(self.runtime_overlay.read_text())
        overlay['evidence'].append({'path':str(spy),'sha256':hashlib.sha256(spy.read_bytes()).hexdigest()})
        overlay['mappings'].append({'config_id':row['id'],'identity':{k:row[k] for k in aa_policy.IDENTITY_FIELDS},
            'runtime_model':'gemini-3.8-flash-high','runtime_effort':'high',
            'selector_type':'model_id_encoded_effort','verification':'request-selector-contract'})
        self.runtime_overlay.write_text(json.dumps(overlay))
        with (self.home/'routing.local.yaml').open('a') as f:
            f.write('aa-gemini: gemini gemini-3.8-flash-high -\n')

    def test_cross_vendor_encoded_effort_provider_receives_exact_selector(self):
        self.configure_gemini_spy()
        r=self.run_dispatch('--executor','cli','--vendor','gemini','--model','gemini-3.8-flash-high','--effort','-',lane='aa-gemini')
        self.assertEqual(r.returncode,0,r.stderr)
        called=json.loads(self.marker.read_text().splitlines()[-1])
        self.assertIn('gemini-3.8-flash-high',called['args'])
        self.assertEqual(called['context']['caller']['vendor'],'gemini')
        self.assertEqual(called['context']['caller']['model'],'gemini-3.8-flash')
        self.assertEqual(called['context']['caller']['effort'],'high')

    def test_cross_vendor_encoded_effort_conflict_never_invokes_provider(self):
        self.configure_gemini_spy()
        r=self.run_dispatch('--executor','cli','--vendor','gemini','--model','gemini-3.8-flash-high','--effort','medium',lane='aa-gemini')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('encoded-effort-conflict',r.stderr)
        self.assertFalse(self.marker.exists())

    def test_retry_current_lower_caller_cannot_replay_original_higher_target(self):
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-5.6-sol','--effort','high')
        self.assertEqual(r.returncode,0,r.stderr)
        job=next((self.home/'jobs').iterdir())
        row=next(r for r in self.registry['scored_configs'] if r['id']=='codex/gpt-5-6-luna-high')
        lower={**self.context_value,'caller':{k:row[k] for k in aa_policy.IDENTITY_FIELDS},'inherited_ceiling':37}
        self.context.write_text(json.dumps(lower))
        r=subprocess.run(['bash',str(ROOT/'scripts/jobs.sh'),'retry',job.name,'--caller-context',str(self.context)],env=self.env,cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(r.returncode,0)
        self.assertIn('target-above-effective-ceiling',r.stderr)
        self.assertEqual(len(self.marker.read_text().splitlines()),1)

    def test_retry_without_current_caller_fails_closed(self):
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-5.6-sol','--effort','high')
        self.assertEqual(r.returncode,0,r.stderr)
        job=next((self.home/'jobs').iterdir())
        r=subprocess.run(['bash',str(ROOT/'scripts/jobs.sh'),'retry',job.name],env=self.env,cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len(self.marker.read_text().splitlines()),1)

    def test_model_retry_does_not_inherit_original_human_exemption(self):
        command=['bash',str(ROOT/'scripts/dispatch.sh'),'--operator-asserted-human','--aa-policy',str(self.policy),'--timeout','10','--executor','cli','--vendor','codex','--model','gpt-5.6-sol','--effort','high','aa-unit','Human fixture launch']
        r=subprocess.run(command,env=self.env,cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr)
        job=next((self.home/'jobs').iterdir())
        row=next(r for r in self.registry['scored_configs'] if r['id']=='codex/gpt-5-6-luna-high')
        self.context.write_text(json.dumps({**self.context_value,'caller':{k:row[k] for k in aa_policy.IDENTITY_FIELDS},'inherited_ceiling':37}))
        r=subprocess.run(['bash',str(ROOT/'scripts/jobs.sh'),'retry',job.name,'--caller-context',str(self.context)],env=self.env,cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(r.returncode,0)
        self.assertIn('target-above-effective-ceiling',r.stderr)
        self.assertEqual(len(self.marker.read_text().splitlines()),1)

    def test_encoded_effort_matching_explicit_value_is_allowed(self):
        self.configure_gemini_spy()
        r=self.run_dispatch('--dry-run','--executor','cli','--vendor','gemini','--model','gemini-3.8-flash-high','--effort','high',lane='aa-gemini')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('downward-allowed',r.stdout)
        self.assertFalse(self.marker.exists())

    def counterfeit_luna_context(self, ceiling):
        row=next(r for r in self.registry['scored_configs'] if r['id']=='codex/gpt-5-6-luna')
        self.context.write_text(json.dumps({**self.context_value,'caller':{k:row[k] for k in aa_policy.IDENTITY_FIELDS},'inherited_ceiling':ceiling}))
        return row

    def test_public_registry_caller_score_inflation_is_rejected(self):
        row=self.counterfeit_luna_context(60)
        row['score']=60
        self.policy.write_text(json.dumps(self.registry))
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-6-astra','--effort','max')
        self.assertNotEqual(r.returncode,0,r.stderr)
        self.assertFalse(self.marker.exists())
        self.assertIn('unapproved AA registry',r.stderr)

    def test_public_registry_target_score_deflation_is_rejected(self):
        self.counterfeit_luna_context(52)
        row=next(r for r in self.registry['scored_configs'] if r['model']=='gpt-6-astra' and r['effort']=='max')
        row['score']=40
        self.policy.write_text(json.dumps(self.registry))
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-6-astra','--effort','max')
        self.assertNotEqual(r.returncode,0,r.stderr)
        self.assertFalse(self.marker.exists())
        self.assertIn('unapproved AA registry',r.stderr)

    def test_public_hash_override_does_not_replace_approval_anchor(self):
        self.policy.write_bytes(self.policy.read_bytes()+b' ')
        self.env['OMNILANE_AA_REGISTRY_SHA256']=hashlib.sha256(self.policy.read_bytes()).hexdigest()
        r=self.run_dispatch('--executor','cli','--vendor','codex','--model','gpt-5.6-sol','--effort','high')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('unapproved AA registry',r.stderr)
        self.assertFalse(self.marker.exists())

    def overlay(self):
        evidence=self.base/'evidence.txt';evidence.write_text('exact selector fixture')
        row=next(r for r in self.registry['scored_configs'] if r['id']=='codex/gpt-5-6-sol-high')
        overlay={'schema_version':1,'snapshot_id':self.registry['snapshot']['id'],'host':socket.gethostname(),
            'evidence':[{'path':str(evidence),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}],
            'mappings':[{'config_id':row['id'],'identity':{k:row[k] for k in aa_policy.IDENTITY_FIELDS},
              'runtime_model':row['model'],'runtime_effort':row['effort'],'verification':'request-selector-contract'}]}
        path=self.base/'overlay.json';path.write_text(json.dumps(overlay));return path,overlay,evidence

    def test_overlay_preserves_scores_and_records_selector_only_evidence(self):
        path,_,_=self.overlay()
        before=hashlib.sha256(self.policy.read_bytes()).hexdigest()
        with patch.dict(os.environ,{'OMNILANE_AA_TRANSPORT_OVERLAY':str(path),'OMNILANE_AA_OVERLAY_SHA256':''}):
            registry,digest=aa_policy.load_registry(self.policy)
        self.assertEqual(before,digest)
        self.assertEqual(before,hashlib.sha256(self.policy.read_bytes()).hexdigest())
        row=next(r for r in registry['scored_configs'] if r['id']=='codex/gpt-5-6-sol-high')
        self.assertEqual(row['score'],48)
        self.assertFalse(row['transport_mapping']['upstream_identity_verified'])

    def test_overlay_host_mismatch_fails_closed(self):
        path,overlay,_=self.overlay();overlay['host']='other-host';path.write_text(json.dumps(overlay))
        with patch.dict(os.environ,{'OMNILANE_AA_TRANSPORT_OVERLAY':str(path),'OMNILANE_AA_OVERLAY_SHA256':''}):
            with self.assertRaisesRegex(aa_policy.PolicyError,'host mismatch'):
                aa_policy.load_registry(self.policy)

    def test_overlay_evidence_change_fails_closed(self):
        path,_,evidence=self.overlay();evidence.write_text('changed selector contract')
        with patch.dict(os.environ,{'OMNILANE_AA_TRANSPORT_OVERLAY':str(path),'OMNILANE_AA_OVERLAY_SHA256':''}):
            with self.assertRaisesRegex(aa_policy.PolicyError,'evidence changed'):
                aa_policy.load_registry(self.policy)

    def test_overlay_effort_identity_mismatch_fails_closed(self):
        path,overlay,_=self.overlay();overlay['mappings'][0]['runtime_effort']='xhigh';path.write_text(json.dumps(overlay))
        with patch.dict(os.environ,{'OMNILANE_AA_TRANSPORT_OVERLAY':str(path),'OMNILANE_AA_OVERLAY_SHA256':''}):
            with self.assertRaisesRegex(aa_policy.PolicyError,'effort mismatch'):
                aa_policy.load_registry(self.policy)

    def test_overlay_changed_after_decision_fails_closed(self):
        path,overlay,_=self.overlay();digest=hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text(json.dumps(overlay)+' ')
        with patch.dict(os.environ,{'OMNILANE_AA_TRANSPORT_OVERLAY':str(path),'OMNILANE_AA_OVERLAY_SHA256':digest}):
            with self.assertRaisesRegex(aa_policy.PolicyError,'overlay changed'):
                aa_policy.load_registry(self.policy)

if __name__=='__main__':
    unittest.main()
