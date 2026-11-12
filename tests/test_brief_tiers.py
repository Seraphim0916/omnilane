"""Three-tier CLI/native transactions, using isolated fixtures only."""
import offline_env  # Activate suite isolation for direct file execution.

import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import unittest

import test_brief_too_long_reject as dispatch_fixture
import test_native_executor as native_fixture
import test_goal_dispatch_journal as goal_fixture

ROOT=Path(__file__).resolve().parents[1]


class DispatchTierPaths(unittest.TestCase):
    def setUp(self):
        self.f=dispatch_fixture.DispatchPreflightTests()
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_all_dispatch_paths_boundaries_no_state_on_dry_run(self):
        for lane,cap in (('consult',140000),('review',102400)):
            for flags in ((),('--background',),('--thread','large-brief'),('--background','--live','--idle-timeout','0')):
                actual_cap=102400 if '--live' in flags else cap
                for size in (actual_cap-self.f.rules,actual_cap-self.f.rules+1,1048576,1048577):
                    with self.subTest(lane=lane,flags=flags,size=size):
                        r=self.f.dispatch(b'x'*size,*flags,'--dry-run',lane=lane)
                        self.assertEqual(r.returncode,2 if size>1048576 else 0,r.stderr)
                        if r.returncode==0 and size+self.f.rules>actual_cap:
                            self.assertIn(b'task_tier=2',r.stdout)
                            self.assertIn(b'task_delivery=file',r.stdout)
                        elif r.returncode==0:
                            # Inline previews keep their earlier bytes: no transport lines.
                            self.assertNotIn(b'task_tier=',r.stdout)
                            self.assertNotIn(b'task_delivery=',r.stdout)
                        self.assertEqual(self.f.jobs(),[])
                        self.assertFalse((self.f.home/'threads').exists())
                        self.assertEqual(list(self.f.work.iterdir()),[])

    def test_tier_three_refusal_no_state_on_foreground_background_thread(self):
        config=self.f.home/'routing.local.yaml'
        config.write_text(config.read_text()+'safe-exec: exec /bin/echo -\n')
        for flags in ((),('--background',),('--dry-run',)):
            r=self.f.dispatch(b'x'*140001,*flags,lane='safe-exec')
            self.assertEqual(r.returncode,2,r.stderr)
            self.assertIn(b'tier 3 refusal',r.stderr)
            self.assertEqual(r.stderr.count(b'omnilane:'),1,r.stderr)
            self.assertEqual(self.f.jobs(),[])
        # Unsupported thread/vendor combinations retain their own mode refusal.

    def test_remaining_vendors_dry_run_boundaries(self):
        api=('cerebras','deepseek','groq','mistral','openrouter','zai')
        config=self.f.home/'routing.local.yaml'
        rows=[]
        for vendor in api+('gemini','grok','opencode','kimi','qwen','vote'):
            model='codex,claude' if vendor=='vote' else 'fixture-model'
            rows.append(f'limit-{vendor}: {vendor} {model} '+('2' if vendor=='vote' else '-')+'\n')
            if vendor in api:self.f.env[vendor.upper()+'_API_KEY']='offline-fixture'
            elif vendor!='vote':
                fake=self.f.home/('fake-'+vendor);fake.write_text('#!/bin/sh\nexit 99\n');fake.chmod(0o755)
                self.f.env[('AGY' if vendor=='gemini' else vendor.upper())+'_BIN']=str(fake)
        config.write_text(config.read_text()+''.join(rows))
        for vendor in api+('gemini','grok','opencode','kimi','qwen','vote'):
            cap=(130000 if platform.system()=='Linux' else 180000) if vendor=='gemini' else 100000 if vendor=='vote' else 140000
            if platform.system()=='Linux' and vendor in ('opencode','kimi','qwen'):cap=131071
            prefix=self.f.rules if vendor in ('gemini','grok') else 0
            for size in (cap-prefix,cap-prefix+1,1048576,1048577):
                with self.subTest(vendor=vendor,size=size):
                    r=self.f.dispatch(b'x'*size,'--dry-run',lane='limit-'+vendor)
                    refusal=size>1048576 or (vendor in ('opencode','kimi','qwen') and size>cap)
                    self.assertEqual(r.returncode,2 if refusal else 0,r.stderr)
                    if not refusal and vendor not in api and size+prefix>cap:
                        self.assertIn(b'task_tier=2',r.stdout)
                        self.assertIn(b'task_delivery=file',r.stdout)
                    elif not refusal:
                        self.assertNotIn(b'task_tier=',r.stdout)
                    self.assertEqual(self.f.jobs(),[])
                    self.assertEqual(list(self.f.work.iterdir()),[])


class NativeTierPaths(unittest.TestCase):
    def test_native_and_inherit_boundaries_and_completion_cleanup(self):
        for fixture_type,inherit in ((native_fixture.NativeExecutorTests,False),(native_fixture.InheritedWorkerTests,True)):
            f=fixture_type();f.setUp()
            try:
                work=f.base/'work';work.mkdir()
                f.ctx['capabilities'][0]['workdirs']=[str(work)];f.save_context()
                flags=['--workdir',str(work),'--native-context',str(f.context)]
                if inherit:flags+=['--inherit','--caller-context',str(f.caller)]
                for size in (139496,139497,1048576,1048577):
                    for dry in (True,False):
                        with self.subTest(inherit=inherit,size=size,dry=dry):
                            data='x'*(size-2)+'\n\n'
                            args=[*flags,*(['--dry-run'] if dry else []),'native-unit','-']
                            r=f.run_cli('scripts/dispatch.sh',args,2 if size>1048576 else 0,input=data)
                            if size>1048576:
                                self.assertIn('runaway-input',r.stderr);continue
                            plan=json.loads(r.stdout);receipt=plan.get('task_transport')
                            if size==139496:
                                # Inline: no transport record, the task itself is handed over.
                                self.assertIsNone(receipt)
                                self.assertEqual(plan['task'],data)
                            else:
                                self.assertEqual(receipt['delivery'],'file')
                                self.assertEqual(receipt['bytes'],size)
                                self.assertEqual(receipt['sha256'],hashlib.sha256(data.encode()).hexdigest())
                            if dry:
                                self.assertFalse(receipt and Path(receipt['path']).exists())
                            else:
                                if receipt:self.assertEqual(Path(receipt['path']).read_bytes(),data.encode())
                                if size==139497: f.ingest(plan)
                                elif size==1048576:
                                    value=f.completion(plan);value['outcome']='failure';f.ingest(plan,value)
                                else: f.job('cancel',plan['job_id'])
                                self.assertFalse(receipt and Path(receipt['path']).exists())
                # The old 65536-character check admitted a 196608-byte task.
                data='苗'*65536
                r=f.run_cli('scripts/dispatch.sh',[*flags,'native-unit','-'],input=data)
                p=json.loads(r.stdout);receipt=p['task_transport']
                self.assertEqual(receipt['delivery'],'file')
                self.assertEqual(receipt['bytes'],196608)
                self.assertEqual(Path(receipt['path']).read_bytes(),data.encode())
                f.job('cancel',p['job_id'])
                self.assertEqual(list(work.iterdir()),[])
                self.assertFalse(f.marker.exists())
            finally:f.doCleanups()


class GoalTierPaths(unittest.TestCase):
    def test_rejection_precedes_lock_journal_and_error_files(self):
        f=goal_fixture.GoalDispatchJournalTests();f.setUp()
        try:
            before={str(p.relative_to(f.goal)):p.read_bytes() for p in f.goal.rglob('*') if p.is_file()}
            r=subprocess.run(['bash',str(ROOT/'scripts/lib/goal-loop.sh'),'dispatch',f.goal_id,'probe','-'],input='x'*140001,text=True,capture_output=True,env=f.env,timeout=30)
            self.assertEqual(r.returncode,2,r.stderr)
            self.assertIn('tier 3 refusal',r.stderr)
            after={str(p.relative_to(f.goal)):p.read_bytes() for p in f.goal.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertFalse((f.goal/'.lock').exists())
            self.assertFalse(f.calls.exists())
        finally:f.doCleanups()

    def test_unwritable_workdir_refusal_precedes_goal_state(self):
        f=goal_fixture.GoalDispatchJournalTests();f.setUp()
        try:
            fake=f.root/'claude';fake.write_text('#!/bin/sh\nexit 99\n');fake.chmod(0o755)
            f.env['CLAUDE_BIN']=str(fake)
            (f.home/'routing.local.yaml').write_text('probe: claude claude-sonnet-5-5-medium -\n')
            before={str(p.relative_to(f.goal)):p.read_bytes() for p in f.goal.rglob('*') if p.is_file()}
            f.root.chmod(0o500)
            try:
                r=subprocess.run(['bash',str(ROOT/'scripts/lib/goal-loop.sh'),'dispatch',f.goal_id,'probe','-'],input='x'*200000,text=True,capture_output=True,env=f.env,timeout=30)
            finally:f.root.chmod(0o700)
            self.assertEqual(r.returncode,2,r.stderr)
            self.assertIn('tier 3 refusal',r.stderr)
            self.assertEqual(before,{str(p.relative_to(f.goal)):p.read_bytes() for p in f.goal.rglob('*') if p.is_file()})
            self.assertFalse((f.goal/'.lock').exists())
            self.assertFalse((f.root/'.omnilane-tmp').exists())
        finally:f.doCleanups()


if __name__=='__main__':unittest.main()
