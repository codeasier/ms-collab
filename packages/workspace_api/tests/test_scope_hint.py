"""W05: a redundant workspace hint cannot reroute or bypass current authority."""
from dataclasses import FrozenInstanceError
from datetime import timedelta
import sqlite3
import unittest
from unittest.mock import Mock

from packages.identity import AuthenticationError
from packages.workspace_api.application import _ConflictingWorkspaceHint, _workspace_hint
from packages.workspace_api.tests import test_application as fixture
from packages.workspaces import PolicyError
from packages.workspaces.tests.identity_fixture import simulate_identity


class ScopeHintTests(unittest.TestCase):
    setUp = fixture.ApplicationTests.setUp
    request = fixture.ApplicationTests.request

    def rejected(self, response, status=400, code='invalid_request'):
        self.assertEqual((response['status'], response['body']), (status, {'error': code}))

    def test_absent_and_exact_hint_keep_route_result_without_normalization(self):
        expected = self.request()
        self.assertEqual(self.request(extra={'HTTP_X_WORKSPACE_ID': 'alpha'}), expected)
        for value in ('beta', 'commons', 'missing', '', ' alpha', 'alpha ', 'ALPHA', 'alpha,beta',
                      'alpha,alpha', 'alpha\x00', 'alpha\n', 'alph\u00e1', 'a' * 201, None, b'alpha', ['alpha']):
            with self.subTest(value=repr(value)):
                self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': value}))
        self.assertEqual(self.store.audit_events('admin', 'alpha'), ())

    def test_current_authentication_and_route_membership_precede_rejection(self):
        for token in (None, 'forged', 'test-disabled'):
            self.rejected(self.request(token=token, extra={'HTTP_X_WORKSPACE_ID': 'beta'}),
                          401, 'authentication_required')
        for token, workspace in (('test-outsider', 'alpha'), ('test-admin', 'beta'),
                                 ('test-admin', 'commons'), ('test-admin', 'missing')):
            self.rejected(self.request(path=fixture.BASE.replace('/alpha/', '/' + workspace + '/') + '/same',
                token=token, extra={'HTTP_X_WORKSPACE_ID': 'alpha'}), 404, 'not_found')
        self.store.set_membership('admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=1)
        self.rejected(self.request(token='test-viewer', extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 404, 'not_found')

    def test_no_business_write_or_audit_and_unknown_route_stays_unknown(self):
        before = self.store.read_resource('admin', 'alpha', 'board', 'same')
        self.rejected(self.request(method='PUT', body={'text': 'must not persist'}, revision='"1"',
                                   extra={'HTTP_X_WORKSPACE_ID': 'beta'}))
        self.assertEqual(self.store.read_resource('admin', 'alpha', 'board', 'same'), before)
        self.assertEqual(self.store.audit_events('admin', 'alpha'), ())
        for path in ('/api/workspaces/alpha', '/api/workspaces/alpha/unknown',
                     '/api/workspaces/alpha/modules/board/resources/same/extra'):
            self.rejected(self.request(path=path, extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 404, 'not_found')
        self.assertEqual(self.request(path='/api/workspaces', extra={'HTTP_X_WORKSPACE_ID': ['not a scope']})['status'], 200)

    def test_local_frozen_rejection_preserves_original_assertion_and_skips_callbacks(self):
        env = {'HTTP_AUTHORIZATION': 'Bearer test-admin', 'PATH_INFO': fixture.BASE + '/same',
               'HTTP_X_WORKSPACE_ID': 'private-conflicting-value'}
        assertion = self.app._verify_request({'HTTP_AUTHORIZATION': 'Bearer test-admin'})
        marked = _workspace_hint(assertion, env)
        self.assertIs(marked.assertion, assertion)
        self.assertNotIn('private-conflicting-value', repr(marked))
        self.assertNotIn('admin-sub', repr(marked))
        self.assertFalse(hasattr(marked, 'issuer'))
        with self.assertRaises(FrozenInstanceError): marked.workspace = 'beta'
        # Two outstanding requests on the same adapter, evaluated out of arrival
        # order: a later matching request must not inherit the earlier rejection.
        ordinary = self.app._verify_request({'HTTP_AUTHORIZATION': 'Bearer test-admin',
            'PATH_INFO': fixture.BASE + '/same', 'HTTP_X_WORKSPACE_ID': 'alpha'})
        self.assertEqual(self.app._authenticated_transaction(ordinary, lambda policy, actor: actor), 'admin')
        env['HTTP_X_WORKSPACE_ID'] = 'alpha'
        env['PATH_INFO'] = fixture.BASE.replace('/alpha/', '/beta/') + '/same'
        for connected in (False, True):
            business, guard = Mock(), Mock()
            with self.assertRaises(PolicyError) as caught:
                self.app._authenticated_transaction(marked, business, write=True,
                    with_connection=connected, before_commit=guard)
            self.assertEqual((caught.exception.status, caught.exception.code), (400, 'invalid_request'))
            business.assert_not_called(); guard.assert_not_called()
        self.assertEqual(self.app._authenticated_transaction(ordinary, lambda policy, actor: actor), 'admin')
        # A rejection wrapper is not a new type of credential or assertion.
        with self.assertRaises(AuthenticationError):
            self.app._authenticated_transaction(_ConflictingWorkspaceHint(object(), 'alpha', False), Mock())
        for path in (None, '/auth/session', '/api/workspaces', '/api/control/workspaces',
                     '/api/control/initial-admin-selections/admin', '/api/invitations/one'):
            self.assertIs(_workspace_hint(assertion, {'PATH_INFO': path, 'HTTP_X_WORKSPACE_ID': None}), assertion)
        self.assertIs(_workspace_hint(assertion, {'PATH_INFO': fixture.BASE, 'HTTP_X_WORKSPACE_ID': 'alpha'}), assertion)

    def test_identity_or_expiry_changed_after_verification_still_precedes_hint(self):
        env = {'HTTP_AUTHORIZATION': 'Bearer test-viewer', 'PATH_INFO': fixture.BASE + '/same',
               'HTTP_X_WORKSPACE_ID': 'beta'}
        marked = self.app._verify_request(env)
        with sqlite3.connect(self.path) as db:
            simulate_identity(db, "UPDATE principals SET active=0 WHERE id='viewer'")
        with self.assertRaises(AuthenticationError): self.app._authenticated_transaction(marked, Mock())
        self.now = fixture.NOW
        env['HTTP_AUTHORIZATION'] = 'Bearer test-admin'
        marked = self.app._verify_request(env)
        self.now += timedelta(minutes=2)
        with self.assertRaises(AuthenticationError): self.app._authenticated_transaction(marked, Mock())
