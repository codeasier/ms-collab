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
from urllib.request import build_opener, HTTPSHandler, ProxyHandler


class ConnectionFixtureStartupTests(unittest.TestCase):
    def test_bound_seed_initializes_templates_and_serves_https_then_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            cert=Path(folder)/'cert.pem';key=Path(folder)/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                '-keyout',str(key),'-out',str(cert),'-subj','/CN=localhost',
                '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            child=subprocess.Popen([sys.executable,'-m','packages.application_ui.tests.connections_browser_server',str(cert),str(key)],
                cwd=Path(__file__).resolve().parents[3],env={**os.environ,'MS_COLLAB_SYNTHETIC_CONNECTIONS_UI':'1'},
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
                with opener.open(startup['origin']+'/app/bootstrap',timeout=5) as response:
                    self.assertEqual(response.status,200)
                    data=json.loads(response.read(65536))['data']
                    self.assertIn('community',data['ui_modules'])
                    self.assertIn('connections',data['ui_modules'])
                child.terminate();out,error=child.communicate(timeout=5)
                self.assertEqual(child.returncode,0,error[-8192:])
                self.assertEqual(out,'');self.assertEqual(error,'')
            finally:
                if child.poll() is None:child.kill()
                child.communicate(timeout=5)


if __name__=='__main__':unittest.main()
