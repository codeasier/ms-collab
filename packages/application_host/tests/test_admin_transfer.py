# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Host mounting/delegation/security only; semantic matrix lives at direct API."""
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests.fixtures import setup, session, ORIGIN, NOW
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import call, cookie_header
from packages.workspace_admin_api.tests.transfer_fixtures import BROWSER_ACTOR, BROWSER_SUBJECT, key
from packages.workspace_admin_api.transfer_store import TransferReceiptStore

BASE = '/api/workspaces/alpha/admin-transfers'


class HostTransferTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app, self.policy, self.login, self.exchange = setup(directory.name,
            clock=lambda: NOW, admin_transfers=True)
        self.session = session(self.login, self.exchange, BROWSER_SUBJECT)
        self.cookies = cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})

    def request(self, path=BASE + '/capabilities', **kwargs):
        return call(self.app, path, cookies=kwargs.pop('cookies', self.cookies), **kwargs)

    def post(self, reviewed, **kwargs):
        values = dict(method='POST', origin=ORIGIN, csrf=self.session.csrf.value,
            revision='"' + str(reviewed['actor_revision']) + '"',
            body={'replacement_id': reviewed['replacement_id'],
                  'replacement_revision': str(reviewed['replacement_revision'])},
            extra={'HTTP_IDEMPOTENCY_KEY': key(), 'HTTP_IF_ADMIN_TRANSFER_MATCH': reviewed['precondition']})
        values.update(kwargs)
        return self.request(BASE, **values)

    def test_invitation_delegate_preserves_transfer_and_cookie_response_boundary(self):
        capability = self.request()
        self.assertEqual(capability['status'], 200, capability)
        self.assertEqual(capability['body'], {'data': {'available': True}})
        selected = self.request(BASE + '/selections/reader')
        self.assertEqual(selected['status'], 200, selected)
        self.assertEqual(selected['body']['data']['actor_id'], BROWSER_ACTOR)
        result = self.post(selected['body']['data'])
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(result['body']['data']['state'], 'committed')
        self.assertEqual(self.request('/api/invitations')['body'], {'data': []})
        for response in (capability, selected, result):
            headers = dict(response['headers'])
            self.assertEqual(headers['Vary'], 'Cookie, Origin')
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['Pragma'], 'no-cache')
            self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
            self.assertNotIn('WWW-Authenticate', headers)

    def test_default_host_and_fixture_remain_unmounted_without_schema_probe(self):
        default = ApplicationHost(config=HostConfig(ORIGIN), sessions=self.app._boundary._sessions)
        with patch.object(TransferReceiptStore, '_admit_schema', side_effect=AssertionError('unmounted')):
            for path in (BASE + '/capabilities', BASE + '/selections/reader'):
                self.assertEqual(call(default, path, cookies=self.cookies)['status'], 404)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        ordinary, _, login, exchange = setup(directory.name, clock=lambda: NOW)
        current = session(login, exchange)
        cookies = cookie_header({_SESSION: current.token.value, _CSRF: current.csrf.value})
        self.assertEqual(call(ordinary, BASE + '/capabilities', cookies=cookies)['status'], 404)

    def test_host_requires_same_policy_exact_handle_without_configuration_flag(self):
        for invalid in (True, '/tmp/synthetic', object(), lambda db: None):
            with self.assertRaises(ValueError):
                ApplicationHost(config=HostConfig(ORIGIN), sessions=self.app._boundary._sessions, admin_transfers=invalid)
        with self.assertRaises(TypeError): HostConfig(ORIGIN, admin_transfers=True)

    def test_cookie_origin_csrf_and_no_bearer_or_header_identity_bypass(self):
        selected = self.request(BASE + '/selections/reader')['body']['data']
        for override in ({'origin': None}, {'csrf': None}, {'origin': 'https://other.example.invalid'}):
            self.assertEqual(self.post(selected, **override)['status'], 401)
        for extra in ({'HTTP_AUTHORIZATION': 'Bearer test-admin'}, {'HTTP_X_USER_ID': BROWSER_ACTOR}):
            self.assertEqual(self.request(cookies='', extra=extra)['status'], 401)
        self.assertEqual(self.request(extra={'HTTP_AUTHORIZATION': 'Bearer test-admin'})['status'], 401)
        self.assertEqual(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'})['status'], 400)


if __name__ == '__main__': unittest.main()
