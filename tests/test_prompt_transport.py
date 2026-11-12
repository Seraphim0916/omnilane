"""Offline transport contract: bytes, limits, staging ownership and cleanup."""
import offline_env  # Activate suite isolation for direct file execution.

import concurrent.futures
import contextlib
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prompt_transport', ROOT / 'scripts/lib/prompt_transport.py')
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


class TransportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)

    def test_tier_boundaries_all_modes_and_paths(self):
        for vendor in transport.API_VENDORS | transport.FILE_VENDORS | {'exec', 'kimi', 'opencode', 'qwen'}:
            for mode in ('advise', 'work', 'sysops'):
                for session in ('single-shot', 'live', 'thread', 'native'):
                    with self.subTest(vendor=vendor, mode=mode, session=session):
                        prefix = transport.rules_bytes() if vendor in transport.FILE_VENDORS and vendor != 'vote' else 0
                        cap = transport.cap_bytes(vendor, session)
                        native = session == 'native'
                        capable = vendor in transport.FILE_VENDORS or native
                        api = vendor in transport.API_VENDORS and not native
                        for size in (cap-prefix, cap-prefix+1, transport.MAX_TASK_BYTES, transport.MAX_TASK_BYTES+1):
                            if size > transport.MAX_TASK_BYTES or (not capable and not api and size+prefix > cap):
                                with self.assertRaises(transport.Refused):
                                    transport.plan(b'x'*size, vendor, mode, session, self.work, prefix=prefix, native=native)
                            else:
                                p = transport.plan(b'x'*size, vendor, mode, session, self.work, prefix=prefix, native=native)
                                self.assertEqual(p['tier'], 1 if api else 2 if capable else 3)
                                self.assertEqual(p['delivery'], 'file' if capable and size+prefix>cap else 'inline')
                        self.assertEqual(list(self.work.iterdir()), [])

    def test_stage_bytes_modes_git_visibility_cleanup(self):
        subprocess.run(['git','init','-q',str(self.work)],check=True)
        data = ('完整\n' * 20000 + '\n\n').encode()
        p = transport.plan(data,'claude','work','single-shot',self.work,stage=True)
        f = Path(p['path'])
        self.assertEqual(f.read_bytes(),data)
        self.assertEqual(p['sha256'],hashlib.sha256(data).hexdigest())
        self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode),0o700)
        for value in (str(f),str(len(data)),p['sha256']): self.assertIn(value,p['pointer'])
        self.assertLess(len(p['pointer'].encode())+transport.rules_bytes(),100000)
        self.assertEqual(subprocess.check_output(['git','-C',str(self.work),'status','--short']),b'')
        transport.cleanup(p)
        self.assertFalse((self.work/'.omnilane-tmp').exists())

    def test_existing_base_visibility_and_concurrent_jobs(self):
        subprocess.run(['git','init','-q',str(self.work)],check=True)
        base=self.work/'.omnilane-tmp';base.mkdir(mode=0o755)
        (base/'user.txt').write_text('keep')
        before=subprocess.check_output(['git','-C',str(self.work),'status','--short','--untracked-files=all'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            plans=list(pool.map(lambda _:transport.plan(b'x'*200000,'codex','advise','thread',self.work,stage=True),range(2)))
        self.assertNotEqual(plans[0]['path'],plans[1]['path'])
        self.assertEqual(subprocess.check_output(['git','-C',str(self.work),'status','--short','--untracked-files=all']),before)
        for p in plans:transport.cleanup(p)
        self.assertEqual([p.name for p in base.iterdir()],['user.txt'])
        self.assertEqual(stat.S_IMODE(base.stat().st_mode),0o755)

    def test_failed_write_and_unwritable_workdir_leave_exact_tree(self):
        for fail in ('write','verify'):
            with self.subTest(fail=fail):
                with mock.patch.object(transport, '_write_verified', side_effect=OSError(fail)):
                    with self.assertRaises(transport.Refused):
                        transport.plan(b'x'*200000,'claude','work','single-shot',self.work,stage=True)
                self.assertEqual(list(self.work.iterdir()),[])
        self.work.chmod(0o500)
        try:
            with self.assertRaises(transport.Refused):
                transport.plan(b'x'*200000,'claude','work','single-shot',self.work,stage=True)
        finally:self.work.chmod(0o700)
        self.assertEqual(list(self.work.iterdir()),[])

    def test_linux_single_argument_limit_and_prefix(self):
        with mock.patch.object(transport.platform,'system',return_value='Linux'):
            self.assertEqual(transport.cap_bytes('gemini'),130000)
            for vendor in ('opencode','kimi','qwen'):
                self.assertEqual(transport.cap_bytes(vendor),131071)
                with self.assertRaises(transport.Refused):
                    transport.plan(b'x'*131072,vendor,'advise','single-shot',self.work)
            p=transport.plan(b'x'*(130000-transport.rules_bytes()+1),'gemini','advise','live',self.work)
            self.assertEqual(p['delivery'],'file')

    def test_live_followup_boundaries_without_repeated_worker_prefix(self):
        for vendor in ('claude','codex','grok','gemini'):
            cap=transport.cap_bytes(vendor,'live')
            for size in (cap,cap+1,1048576,1048577):
                with self.subTest(vendor=vendor,size=size):
                    if size>1048576:
                        with self.assertRaises(transport.Refused):transport.plan(b'x'*size,vendor,'sysops','live',self.work,prefix=0)
                    else:
                        p=transport.plan(b'x'*size,vendor,'sysops','live',self.work,prefix=0,stage=True)
                        self.assertEqual(p['delivery'],'inline' if size<=cap else 'file')
                        transport.cleanup(p)

    def test_cleanup_refuses_replaced_file(self):
        p=transport.plan(b'x'*200000,'claude','work','single-shot',self.work,stage=True)
        f=Path(p['path']);f.rename(f.with_suffix('.saved'));f.write_text('foreign')
        transport.cleanup(p)
        self.assertEqual(f.read_text(),'foreign')

    def test_shared_new_base_and_recoverable_orphan_receipt(self):
        plans=[transport.plan(b'x'*200000,'claude','advise','single-shot',self.work,stage=True) for _ in range(2)]
        transport.cleanup(plans[0])
        self.assertTrue(Path(plans[1]['path']).is_file())
        receipt=json.loads(Path(plans[1]['path']).with_name('receipt.json').read_text())
        transport.cleanup(receipt)
        self.assertEqual(list(self.work.iterdir()),[])

    def test_staged_codex_runtime_keeps_existing_base_and_git_exclude(self):
        spec=importlib.util.spec_from_file_location('codex_worker_setup',ROOT/'scripts/lib/codex_worker_setup.py')
        setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
        subprocess.run(['git','init','-q',str(self.work)],check=True)
        base=self.work/'.omnilane-tmp';base.mkdir(mode=0o755)
        (base/'user.txt').write_text('keep visible')
        exclude=self.work/'.git/info/exclude';original=exclude.read_bytes()
        before=subprocess.check_output(['git','-C',str(self.work),'status','--short','--untracked-files=all'])
        p=transport.plan(b'x'*200000,'codex','work','single-shot',self.work,stage=True)
        capture=io.StringIO()
        with mock.patch.dict(os.environ,OMNILANE_TASK_FILE_STAGED='1'),contextlib.redirect_stdout(capture):
            setup.tmpdir(str(self.work))
        runtime=Path(capture.getvalue().strip())
        self.assertEqual(subprocess.check_output(['git','-C',str(self.work),'status','--short','--untracked-files=all']),before)
        self.assertEqual(exclude.read_bytes(),original)
        self.assertEqual(stat.S_IMODE(base.stat().st_mode),0o755)
        setup.cleanup(str(runtime));transport.cleanup(p)
        self.assertEqual([f.name for f in base.iterdir()],['user.txt'])

    def test_vote_second_round_keeps_original_only_opinions_shortened(self):
        for size in (99999,100000,100001,1048576):
            data=b'x'*(size-2)+b'\n\n'
            payload,p=transport.vote_round(data,b'o'*200000,self.work)
            self.assertEqual(Path(p['path']).read_bytes(),data)
            self.assertLessEqual(len(payload)+transport.rules_bytes(),100000)
            self.assertIn(p['sha256'].encode(),payload)
            self.assertIn(b'model opinions shortened',payload)
            transport.cleanup(p)
        with self.assertRaises(transport.Refused):
            transport.vote_round(b'x'*1048577,b'',self.work)

    def test_job_cleanup_removes_first_task_and_followup_receipts(self):
        job=self.work/'record';job.mkdir()
        for name in ('brief.json','brief-message-test.json'):
            p=transport.plan(b'x'*200000,'claude','advise','live',self.work,stage=True)
            (job/name).write_text(json.dumps(p))
        transport.cleanup_job(job)
        self.assertFalse((self.work/'.omnilane-tmp').exists())

    def test_base_removed_between_preflight_and_stage_is_recreated(self):
        data=b'z'*150000
        previous=transport.plan(data,'codex','advise','single-shot',self.work,stage=True)
        base=self.work/'.omnilane-tmp'
        access=transport.os.access
        stage=transport._stage
        removed=[]

        def remove_base():
            if not removed:
                transport.cleanup(previous)
                self.assertFalse(base.exists())
                removed.append(True)

        def access_after_cleanup(path, mode):
            # Reproduce cleanup after the old preflight's lstat, before access.
            if Path(path)==base:
                remove_base()
            return access(path,mode)

        def stage_after_cleanup(*args):
            # With no unlocked base probe, remove it just before staging.
            remove_base()
            return stage(*args)

        with mock.patch.object(transport.os,'access',side_effect=access_after_cleanup), \
                mock.patch.object(transport,'_stage',side_effect=stage_after_cleanup):
            current=transport.plan(data,'codex','advise','single-shot',self.work,stage=True)
        self.addCleanup(transport.cleanup,current)
        self.assertEqual(removed,[True])
        self.assertEqual(current['delivery'],'file')
        self.assertEqual(Path(current['path']).read_bytes(),data)
        transport.cleanup(current)
        self.assertEqual(list(self.work.iterdir()),[])

    def test_concurrent_stage_and_cleanup_never_strand_the_base(self):
        # One job removing the shared base while another creates its job
        # directory used to leave an empty, unmarked base behind.
        refused=[]
        def loop():
            for _ in range(300):
                try:transport.cleanup(transport.plan(b'z'*150000,'codex','advise','single-shot',self.work,stage=True))
                except transport.Refused as error:refused.append(str(error))
        errors=io.StringIO()
        with contextlib.redirect_stderr(errors):
            workers=[threading.Thread(target=loop) for _ in range(2)]
            for worker in workers:worker.start()
            for worker in workers:worker.join()
        self.assertEqual(refused,[])
        self.assertNotIn('retained',errors.getvalue())
        self.assertFalse((self.work/'.omnilane-tmp').exists())
        self.assertEqual(list(self.work.iterdir()),[])

    def test_reused_goal_stage_matches_bytes_and_rejects_drift(self):
        data=b'x'*200000+b'\n\n'
        p=transport.plan(data,'claude','work','single-shot',self.work,stage=True)
        expected=transport.plan(data,'claude','work','single-shot',self.work)
        self.assertEqual(transport.reuse(p,expected,data,self.work)['path'],p['path'])
        with self.assertRaises(transport.Refused):
            transport.reuse(p,expected,data+b'\n',self.work)
        transport.cleanup(p)


if __name__ == '__main__':unittest.main()
