# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Real opt-in fixture startup: no browser, external provider or production key."""
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
from urllib.request import build_opener, HTTPSHandler, ProxyHandler, Request


class GuiFixtureStartupTests(unittest.TestCase):
    def test_both_https_fixtures_enroll_and_regrant_without_implicit_rebinding(self):
        with tempfile.TemporaryDirectory() as folder:
            cert=Path(folder)/'cert.pem';key=Path(folder)/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                '-keyout',str(key),'-out',str(cert),'-subj','/CN=localhost',
                '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for module,opt in (('packages.gui_smoke_ui.tests.browser_server','MS_COLLAB_SYNTHETIC_GUI_UI_TEST'),('packages.application_ui.tests.gui_browser_server','MS_COLLAB_SYNTHETIC_GUI_ENTRY_TEST')):
                self.check_fixture(module,opt,cert,key)

    def check_fixture(self,module,opt,cert,key):
        child=subprocess.Popen([sys.executable,'-m',module,str(cert),str(key)],
            cwd=Path(__file__).resolve().parents[3],env={**os.environ,opt:'1'},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout,selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=10),'synthetic fixture startup timed out')
            line=child.stdout.readline()
            if not line:
                _,error=child.communicate(timeout=5)
                self.fail('synthetic fixture failed during startup: '+error[-8192:])
            startup=json.loads(line);origin=urlsplit(startup['origin'])
            self.assertEqual((origin.scheme,origin.hostname),('https','127.0.0.1'))
            self.assertIsNotNone(origin.port)
            context=ssl.create_default_context(cafile=str(cert))
            opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context))
            for action in ('/synthetic-regrant','/synthetic-republish','/synthetic-regrant','/synthetic-republish'):
                request=Request(startup['origin']+action,data=b'',headers={'Origin':startup['origin']},method='POST')
                with opener.open(request,timeout=5) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.read(65536),b'')
            child.terminate();out,error=child.communicate(timeout=5)
            self.assertEqual(child.returncode,0,error[-8192:])
            self.assertEqual(out,'');self.assertEqual(error,'')
        finally:
            if child.poll() is None:child.kill()
            child.communicate(timeout=5)


if __name__=='__main__':unittest.main()
