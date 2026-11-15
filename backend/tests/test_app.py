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
            self.assertEqual([item['id'] for item in modules['items']], ['overview', 'community-board'])
            status, _, body = self.json(f'/api/workspaces/{workspace}/overview')
            self.assertEqual(status, 200)
            self.assertEqual(body['workspace_id'], workspace)
            self.assertEqual(body['data']['open_items'], count)

    def test_inaccessible_and_unknown_spaces_have_identical_response(self):
        for resource in ['modules', 'overview', 'community-board']:
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
        for path in ['/', '/app.mjs', '/navigation.mjs', '/board.mjs', '/style.css']:
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

    def test_board_is_scoped_and_observed_not_live(self):
        for workspace, total in [('product-alpha', 2), ('commons', 1)]:
            status, headers, body = self.json(f'/api/workspaces/{workspace}/community-board')
            self.assertEqual(status, 200)
            self.assertEqual(body['mode'], 'synthetic-demo')
            self.assertEqual(body['coverage'], 'observed_subset')
            self.assertEqual(body['total'], total)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            for item in body['items'] + body['sources']:
                self.assertEqual(item['workspace_id'], workspace)
            self.assertTrue(all(source['observed_at'].endswith('Z') for source in body['sources']))

    def test_board_filters_and_pagination(self):
        base = '/api/workspaces/product-alpha/community-board'
        first = self.json(base + '?limit=1&offset=0')[2]
        second = self.json(base + '?limit=1&offset=1')[2]
        self.assertEqual(first['total'], 2)
        self.assertEqual(len(first['items']), 1)
        self.assertNotEqual(first['items'], second['items'])
        filtered = self.json(base + '?kind=pull&state=merged&q=synthetic')[2]
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['items'][0]['kind'], 'pull')
        self.assertEqual(self.json(base + '?q=not-present')[2]['items'], [])

    def test_board_gate_precedes_query_and_projection(self):
        with patch('ms_collab.app.BOARD') as board:
            for workspace in ['product-beta', 'not-present']:
                self.assertEqual(self.json(f'/api/workspaces/{workspace}/community-board?identity=admin')[0], 404)
            with patch('ms_collab.app.MEMBERSHIPS', {'product-alpha': frozenset({'overview.read'})}):
                self.assertEqual(self.json('/api/workspaces/product-alpha/community-board')[0], 404)
            spaces = dict(WORKSPACES)
            spaces['product-alpha'] = Workspace('product-alpha', 'Alpha', 'private', ('overview',))
            with patch('ms_collab.app.WORKSPACES', spaces):
                self.assertEqual(self.json('/api/workspaces/product-alpha/community-board')[0], 404)
            with patch('ms_collab.app.MEMBERSHIPS', {'product-alpha': MEMBERSHIPS['product-alpha']}):
                self.assertEqual(self.json('/api/workspaces/commons/community-board')[0], 404)
            board.list_items.assert_not_called()

    def test_board_rejects_unknown_duplicate_and_unbounded_filters(self):
        for query in ['workspace_id=product-beta', 'connection_id=other', 'token=example',
                      'q=a&q=b', 'kind=unknown', 'state=unknown', 'limit=0', 'limit=101',
                      'offset=-1', 'offset=99999999', 'limit=1.5', 'limit=', 'q=' + 'a' * 201,
                      'q=' + 'a' * 2048, 'bare', 'q=1&kind=issue&state=open&limit=1&offset=0&x=1&y=2']:
            self.assertEqual(self.json('/api/workspaces/product-alpha/community-board?' + query)[0], 400, query[:80])

    def test_head_returns_no_body(self):
        status, _, body = self.request('/healthz', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')


if __name__ == '__main__':
    unittest.main()
