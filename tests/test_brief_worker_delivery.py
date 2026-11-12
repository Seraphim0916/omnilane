"""Exercise the real worker/Claude runner with a recording CLI, never a vendor.

Only dispatch policy and cwd-lock inspection are fixture doubles. This tests
worker delivery and EXIT cleanup, not the dispatch controller's process oracle.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from offline_env import isolated_environment
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import prompt_transport as transport

ROOT=Path(__file__).resolve().parents[1]
FAKE=r'''#!/usr/bin/env python3
import hashlib,json,os,pathlib,re,sys,time
text=sys.argv[-1]
match=re.search(r'^Absolute path: (.+)$',text,re.M)
path=pathlib.Path(json.loads(match.group(1))) if match else None
data=path.read_bytes() if path else text.encode()
pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps({'text':text,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'path':str(path) if path else None}))
if os.environ.get('SLEEP')=='1':time.sleep(60)
print('fixture complete',flush=True)
sys.exit(int(os.environ.get('FAKE_EXIT','0')))
'''


class WorkerDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.env,violations=isolated_environment(self.root/'isolated',os.environ['PATH'])
        self.addCleanup(lambda:self.assertFalse(violations.exists()))
        self.runtime=self.root/'runtime'
        for name in ('lib','runners'):shutil.copytree(ROOT/'scripts'/name,self.runtime/'scripts'/name)
        shutil.copytree(ROOT/'config',self.runtime/'config')
        with (self.runtime/'scripts/lib/common.sh').open('a') as f:
            f.write('\nacquire_cwd_lock() { :; }\naa_policy_gate() { :; }\n')
        self.fake=self.root/'claude';self.fake.write_text(FAKE);self.fake.chmod(0o755)
        self.work=self.root/'work';self.work.mkdir()
        self.job=self.root/'job';self.job.mkdir()
        self.capture=self.root/'capture.json'
        self.env.update(CLAUDE_BIN=str(self.fake),CAPTURE=str(self.capture),OMNILANE_HOME=str(self.root/'home'),OMNILANE_SESSION_MODE='single-shot',OMNILANE_TIMEOUT='10')

    def prepare(self,size):
        data=b'x'*(size-4)+b'\r\n\n\n'
        (self.job/'task.txt').write_bytes(data)
        receipt=transport.plan(data,'claude','advise','single-shot',self.work,stage=True)
        (self.job/'brief.json').write_text(json.dumps(receipt))
        if receipt['delivery']=='file':(self.job/'transport-task.txt').write_text(receipt['pointer'])
        args=['bash',str(self.runtime/'scripts/lib/job-worker.sh'),'claude','advise',str(self.work),'fixture','low',str(self.job/'task.txt'),str(self.job/'out.txt')]
        return data,receipt,args

    def test_exact_transport_limit_and_staged_bytes_success_failure(self):
        for size,code in ((102400-transport.rules_bytes(),0),(102401-transport.rules_bytes(),0),(1048576,23)):
            with self.subTest(size=size,code=code):
                data,p,args=self.prepare(size)
                r=subprocess.run(args,env=dict(self.env,FAKE_EXIT=str(code)),capture_output=True,timeout=20)
                self.assertEqual(r.returncode,code,r.stderr)
                capture=json.loads(self.capture.read_text())
                if p['delivery']=='file':
                    self.assertEqual(capture['sha256'],hashlib.sha256(data).hexdigest())
                    self.assertEqual(capture['bytes'],len(data))
                    self.assertFalse(Path(p['path']).exists())
                # Inline argv delivery keeps main's $(cat): trailing newlines are dropped.
                else:self.assertEqual(capture['text'].encode(),(transport.worker_rules().encode()+data).rstrip(b'\n'))
                self.assertEqual((self.job/'task.txt').read_bytes(),data)
                self.assertFalse((self.work/'.omnilane-tmp').exists())

    def test_timeout_removes_staged_brief(self):
        data,p,args=self.prepare(200000)
        r=subprocess.run(args,env=dict(self.env,SLEEP='1',OMNILANE_TIMEOUT='1'),capture_output=True,timeout=20)
        self.assertIn(r.returncode,(124,142),r.stderr)
        self.assertEqual(json.loads(self.capture.read_text())['sha256'],hashlib.sha256(data).hexdigest())
        self.assertFalse(Path(p['path']).exists())

    def test_cancel_removes_staged_brief(self):
        data,p,args=self.prepare(200000)
        process=subprocess.Popen(args,env=dict(self.env,SLEEP='1'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:
            deadline=time.monotonic()+8
            while not self.capture.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(self.capture.exists())
            os.killpg(process.pid,signal.SIGTERM)
            out,err=process.communicate(timeout=10)
            self.assertEqual(process.returncode,143,err)
            self.assertFalse(Path(p['path']).exists())
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL);process.communicate(timeout=5)


if __name__=='__main__':unittest.main()
