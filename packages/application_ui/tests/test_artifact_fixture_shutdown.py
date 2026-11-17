# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Real Artifact HTTPS fixture shutdown, not a product signal handler or route."""
import json
import os
from pathlib import Path
import selectors
import ssl
import subprocess
import sys
import tempfile
import unittest
from urllib.request import build_opener, HTTPSHandler, ProxyHandler

# Inject only inside this disposable subprocess's actual ApplicationUI stack.
# The former signal handler raised SystemExit here; wsgiref swallowed it and
# kept serving, so idle-only or generic helper tests cannot cover this fixture.
_PROBE = '''import os,signal
from packages.application_ui.tests import artifact_browser_server as fixture
from packages.application_ui import ApplicationUI
original=ApplicationUI.__call__
def active_request(self,environ,start):
 if environ.get('PATH_INFO')=='/synthetic-cleanup-proof':
  os.kill(os.getpid(),signal.SIGTERM)
  start('200 OK',[('Content-Length','8'),('Content-Type','text/plain')])
  return [b'finished']
 return original(self,environ,start)
ApplicationUI.__call__=active_request
fixture.main()
'''


class ArtifactFixtureShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='artifact-shutdown-test-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.cert = Path(cls.temp.name) / 'cert.pem'
        cls.key = Path(cls.temp.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-keyout', str(cls.key), '-out', str(cls.cert), '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    def _exercise(self, *, active, discovery):
        command = [sys.executable, '-c', _PROBE, str(self.cert), str(self.key)]
        child = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[3],
            env={**os.environ, 'MS_COLLAB_SYNTHETIC_ARTIFACT_SESSION': '1',
                 'MS_COLLAB_SYNTHETIC_ARTIFACT_DISCOVERY': '1' if discovery else '0'},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=5), 'artifact fixture did not become ready')
            line = child.stdout.readline()
            if not line:
                _, error = child.communicate(timeout=5)
                self.fail('artifact fixture failed during startup: ' + error[-8192:])
            origin = json.loads(line)['origin']
            if active:
                # Trust only the temporary certificate, and bypass ambient proxy
                # configuration for this loopback-only test request.
                context = ssl.create_default_context(cafile=str(self.cert))
                opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
                with opener.open(origin + '/synthetic-cleanup-proof', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'finished')
            else:
                child.terminate()
            out, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, error[-8192:])
            self.assertEqual(out, '')
            self.assertEqual(error, '')
        finally:
            # Reaping a regression does not make it pass. Every expectation and
            # timeout above remains a failure; this protects the rest of CI.
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_real_artifact_fixture_sigterm_inside_https_request_exits_cleanly(self):
        for discovery in (False, True):
            with self.subTest(discovery=discovery):
                self._exercise(active=True, discovery=discovery)

    def test_real_artifact_fixture_sigterm_while_idle_exits_cleanly(self):
        for discovery in (False, True):
            with self.subTest(discovery=discovery):
                self._exercise(active=False, discovery=discovery)


if __name__ == '__main__':
    unittest.main()
