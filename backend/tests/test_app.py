from contextlib import closing
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ms_collab.app import create_server
from ms_collab.catalog import MEMBERSHIPS, WORKSPACES, Workspace


class DemoHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path, method='GET', headers=None):
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)) as conn:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body

    def json(self, path, **kwargs):
        status, headers, body = self.request(path, **kwargs)
        return status, headers, json.loads(body)

    def test_loopback_only_and_health(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, headers, body = self.json('/healthz')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'status': 'ok', 'mode': 'synthetic-demo'})
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_discovery_is_membership_scoped(self):
        status, _, body = self.json('/api/workspaces')
        self.assertEqual(status, 200)
        self.assertEqual(body['user'], 'demo-developer')
        self.assertEqual([item['id'] for item in body['items']], ['product-alpha', 'commons'])
        self.assertEqual(body['items'][1]['kind'], 'public')

    def test_each_workspace_gets_its_own_fixture(self):
        for workspace, count in [('product-alpha', 5), ('commons', 1)]:
            status, _, modules = self.json(f'/api/workspaces/{workspace}/modules')
            self.assertEqual(status, 200)
            self.assertEqual(modules['workspace_id'], workspace)
            self.assertEqual([item['id'] for item in modules['items']], ['overview'])
            status, _, body = self.json(f'/api/workspaces/{workspace}/overview')
            self.assertEqual(status, 200)
            self.assertEqual(body['workspace_id'], workspace)
            self.assertEqual(body['data']['open_items'], count)

    def test_inaccessible_and_unknown_spaces_have_identical_response(self):
        for resource in ['modules', 'overview']:
            denied = self.json(f'/api/workspaces/product-beta/{resource}')
            missing = self.json(f'/api/workspaces/no-such-workspace/{resource}')
            self.assertEqual(denied[0], 404)
            self.assertEqual(denied[2], missing[2])

    def test_public_workspace_does_not_bypass_membership(self):
        with patch('ms_collab.app.MEMBERSHIPS', {'product-alpha': MEMBERSHIPS['product-alpha']}):
            self.assertEqual(self.json('/api/workspaces/commons/overview')[0], 404)

    def test_enabled_module_does_not_grant_capability(self):
        with patch('ms_collab.app.MEMBERSHIPS', {'product-alpha': frozenset()}):
            self.assertEqual(self.json('/api/workspaces/product-alpha/overview')[0], 404)

    def test_capability_does_not_enable_module(self):
        spaces = dict(WORKSPACES)
        spaces['product-alpha'] = Workspace('product-alpha', 'Alpha', 'private', ())
        with patch('ms_collab.app.WORKSPACES', spaces):
            self.assertEqual(self.json('/api/workspaces/product-alpha/overview')[0], 404)

    def test_identity_spoof_headers_are_ignored(self):
        for header in ['X-User', 'Authorization', 'Cookie', 'X-Workspace-Id']:
            self.assertEqual(self.json('/api/workspaces/product-beta/overview', headers={header: 'admin'})[0], 404)

    def test_writes_are_rejected(self):
        for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']:
            self.assertEqual(self.json('/api/workspaces/product-alpha/overview', method=method)[0], 405)

    def test_static_allowlist_and_unknown_routes(self):
        for path in ['/', '/app.mjs', '/navigation.mjs', '/style.css']:
            self.assertEqual(self.request(path)[0], 200)
        for path in ['/LICENSE', '/backend/ms_collab/app.py', '/../LICENSE', '/%2e%2e/LICENSE', '/.env', '/api/missing']:
            self.assertEqual(self.json(path)[0], 404)
        self.assertEqual(self.json('/api/workspaces/product-alpha%2f../overview')[0], 404)

    def test_queries_and_absolute_targets_are_rejected(self):
        for path in ['/?token=example', '/api/workspaces?user=admin', 'http://example.invalid/healthz', 'http://[invalid']:
            self.assertEqual(self.json(path, headers={'Host': f'127.0.0.1:{self.server.server_port}'})[0], 400)

    def test_foreign_host_origin_and_cross_site_requests_are_rejected(self):
        for headers in [{'Host': 'example.invalid'}, {'Origin': 'https://example.invalid'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.json('/healthz', headers=headers)[0], 403)
        origin = f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.json('/healthz', headers={'Origin': origin})[0], 200)

    def test_head_returns_no_body(self):
        status, _, body = self.request('/healthz', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')


if __name__ == '__main__':
    unittest.main()
