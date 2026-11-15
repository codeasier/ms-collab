"""Actual responsibility HTTP/HTTPS entrypoints stop within active or idle WSGI.

The subprocess-only patch delivers SIGTERM inside the real page request. No
synthetic stop route or signal hook is added to either fixture or product code.
"""
import json
import os
from pathlib import Path
import selectors
import ssl
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPSHandler, ProxyHandler


class ResponsibilityFixtureShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='ms-collab-responsibility-shutdown-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.cert=Path(cls.temp.name)/'cert.pem';cls.key=Path(cls.temp.name)/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-keyout',str(cls.key),'-out',str(cls.cert),'-subj','/CN=localhost',
            '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def exercise(self,*,cookie,active):
        module='packages.responsibility_ui.tests.'+('cookie_browser_server' if cookie else 'browser_server')
        args=[str(self.cert),str(self.key)] if cookie else []
        command=[sys.executable,'-m',module,*args]
        if active:
            package,kind=('packages.application_ui','ApplicationUI') if cookie else ('packages.responsibility_ui','ResponsibilityUI')
            probe=f"""import os,signal,runpy,sys
from {package} import {kind}
original={kind}.__call__
def stopping(self,environ,start):
    if environ.get('PATH_INFO')=='/responsibility/':
        os.kill(os.getpid(),signal.SIGTERM)
        os.kill(os.getpid(),signal.SIGTERM)
    return original(self,environ,start)
{kind}.__call__=stopping
sys.argv=[{module!r},*{args!r}]
runpy.run_module({module!r},run_name='__main__')
"""
            command=[sys.executable,'-c',probe]
        child=subprocess.Popen(command,cwd=Path(__file__).resolve().parents[3],
            env={**os.environ,'MS_COLLAB_SYNTHETIC_RESPONSIBILITY_UI':'1'},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout,selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=10),'synthetic fixture startup timed out')
            line=child.stdout.readline()
            if not line:
                _,error=child.communicate(timeout=5)
                self.fail('synthetic fixture failed during startup: '+error[-8192:])
            origin=json.loads(line)['origin'] if cookie else line.strip()
            target=urlsplit(origin)
            self.assertEqual((target.scheme,target.hostname),('https' if cookie else 'http','127.0.0.1'))
            handlers=[ProxyHandler({})]
            if cookie:handlers.append(HTTPSHandler(context=ssl.create_default_context(cafile=str(self.cert))))
            with build_opener(*handlers).open(origin+'/responsibility/',timeout=5) as response:
                self.assertEqual(response.status,200)
                self.assertIn(b'Ownership, with clear boundaries.',response.read(65536))
            if not active:child.terminate()
            out,error=child.communicate(timeout=5)
            self.assertEqual(child.returncode,0,error[-8192:])
            self.assertEqual(out,'');self.assertEqual(error,'')
        finally:
            # Reap only this owned child on failure; forced cleanup does not turn
            # a timeout or failed response into a passing acceptance test.
            if child.poll() is None:child.kill()
            child.communicate(timeout=5)

    def test_bearer_fixture_sigterm_during_actual_http_page_finishes_and_reaps(self):
        self.exercise(cookie=False,active=True)

    def test_bearer_fixture_sigterm_while_idle_finishes_and_reaps(self):
        self.exercise(cookie=False,active=False)

    def test_cookie_fixture_sigterm_during_actual_https_page_finishes_and_reaps(self):
        self.exercise(cookie=True,active=True)

    def test_cookie_fixture_sigterm_while_idle_finishes_and_reaps(self):
        self.exercise(cookie=True,active=False)
