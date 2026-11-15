"""Deterministic child-process regression for SIGTERM during WSGI requests."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import unittest
from urllib.request import urlopen
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.application_ui.tests.synthetic_shutdown import serve_synthetic


def fixture():
    class Quiet(WSGIRequestHandler):
        def log_message(self, *_):
            pass

    def app(environ, start):
        # This was swallowed as SystemExit by WSGI under the old handler. Two
        # signals also verify that repeated shutdown does not spawn more work.
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGTERM)
        start('200 OK', [('Content-Type', 'text/plain'), ('Content-Length', '8')])
        return [b'finished']

    server = make_server('127.0.0.1', 0, app, handler_class=Quiet)
    serve_synthetic(server, on_ready=lambda: print(server.server_port, flush=True))
    print('stopped', flush=True)


class SyntheticShutdownTests(unittest.TestCase):
    def _exercise(self, active):
        child = subprocess.Popen(
            [sys.executable, '-m', 'packages.application_ui.tests.test_synthetic_shutdown', '--fixture'],
            cwd=Path(__file__).resolve().parents[3],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=5), 'fixture did not become ready')
            port = int(child.stdout.readline().strip())
            if active:
                with urlopen(f'http://127.0.0.1:{port}/', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'finished')
            else:
                child.terminate()
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            self.assertEqual(out, 'stopped\n')
            self.assertEqual(err, '')
        finally:
            # An unsuccessful assertion remains a failure, not a retry. Reap
            # this test's owned process so a regression cannot hang the suite.
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_sigterm_during_active_wsgi_request_finishes_and_reaps(self):
        self._exercise(active=True)

    def test_sigterm_while_idle_finishes_and_reaps(self):
        self._exercise(active=False)


if __name__ == '__main__':
    if sys.argv[1:] == ['--fixture']:
        fixture()
    else:
        unittest.main()
