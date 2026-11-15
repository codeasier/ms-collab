"""Real disposable HTTPS fixture lifecycle; no browser or live provider needed."""
import json
import os
from pathlib import Path
import selectors
import ssl
import subprocess
import sys
import tempfile
import unittest
from urllib.request import urlopen

# Inject a signal inside the actual fixture WSGI stack. Its old SystemExit
# handler was caught by wsgiref and left the child alive with a 500 response.
_PROBE = '''import os,signal
from packages.application_ui.tests import assistant_browser_server as fixture
original=fixture.ApplicationUI.__call__
def active_request(self,environ,start):
 if environ.get('PATH_INFO')=='/synthetic-cleanup-proof':
  os.kill(os.getpid(),signal.SIGTERM)
  start('200 OK',[('Content-Length','8'),('Content-Type','text/plain')])
  return [b'finished']
 return original(self,environ,start)
fixture.ApplicationUI.__call__=active_request
fixture.main()
'''


class AssistantFixtureShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='assistant-shutdown-test-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.cert = Path(cls.temp.name) / 'cert.pem'
        cls.key = Path(cls.temp.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-keyout', str(cls.key), '-out', str(cls.cert), '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    def _exercise(self, active):
        child = subprocess.Popen([sys.executable, '-c', _PROBE, str(self.cert), str(self.key)],
            cwd=Path(__file__).resolve().parents[3],
            env={**os.environ, 'MS_COLLAB_SYNTHETIC_ASSISTANT_SESSION_UI': '1'},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=5), 'fixture did not become ready')
            origin = json.loads(child.stdout.readline())['origin']
            if active:
                # Trust only this temporary self-signed fixture in this client.
                context = ssl.create_default_context(cafile=str(self.cert))
                with urlopen(origin + '/synthetic-cleanup-proof', context=context, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'finished')
            else:
                child.terminate()
            _, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            self.assertEqual(err, '')
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_real_assistant_fixture_sigterm_inside_https_request_exits_cleanly(self):
        self._exercise(active=True)

    def test_real_assistant_fixture_sigterm_while_idle_exits_cleanly(self):
        self._exercise(active=False)


if __name__ == '__main__':
    unittest.main()
