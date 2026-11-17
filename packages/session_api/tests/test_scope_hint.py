"""The W05 rejection runs after the real SQLite session/current-identity check."""
import sqlite3
import unittest
from unittest.mock import Mock, patch

from packages.identity import AuthenticationError
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests import test_application as fixture
from packages.workspace_api.application import _ConflictingWorkspaceHint
from packages.workspaces.tests.identity_fixture import simulate_identity


class SessionScopeHintTests(unittest.TestCase):
    setUp = fixture.SessionHTTPTests.setUp
    make_session = fixture.SessionHTTPTests.make_session
    request = fixture.SessionHTTPTests.request
    write = fixture.SessionHTTPTests.write

    def rejected(self, response, status=400, code='invalid_request'):
        self.assertEqual((response['status'], response['body']), (status, {'error': code}))

    def test_matching_and_conflicting_hint_preserve_cookie_csrf_and_write_atomicity(self):
        self.assertEqual(self.request(extra={'HTTP_X_WORKSPACE_ID': 'alpha'})['status'], 200)
        self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}))
        self.rejected(self.write(extra={'HTTP_X_WORKSPACE_ID': 'beta'}))
        self.assertEqual(self.policy.read_resource('admin', 'alpha', 'board', 'same').text, 'Alpha')
        self.assertEqual(self.policy.audit_events('admin', 'alpha'), ())
        self.rejected(self.write(csrf=None, extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')
        self.rejected(self.write(csrf='B' * 43, extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')
        self.assertEqual(self.write(extra={'HTTP_X_WORKSPACE_ID': 'alpha'})['status'], 200)

    def test_well_shaped_forged_expired_and_revoked_cookie_do_not_become_400(self):
        forged = fixture.cookie_header({_SESSION: 'A' * 43, _CSRF: self.session.csrf.value})
        for cookies in ('', forged):
            self.rejected(self.request(cookies=cookies, extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')
        self.login.logout(self.session.token, self.session.csrf)
        self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')
        self.session = self.make_session()
        self.cookies = fixture.cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})
        self.clock.now = self.session.expires_at
        self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')

    def test_revocation_between_request_shape_and_transaction_is_checked(self):
        original = self.app._verify_request
        def revoke(environ):
            request = original(environ)
            self.login.logout(self.session.token, self.session.csrf)
            return request
        with patch.object(self.app, '_verify_request', side_effect=revoke):
            self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')

    def test_true_identity_aba_and_inactive_binding_precede_rejection(self):
        with sqlite3.connect(self.policy_path) as db:
            simulate_identity(db, "UPDATE principals SET subject='other-binding' WHERE id='admin'")
            simulate_identity(db, "UPDATE principals SET subject='admin-sub' WHERE id='admin'")
            self.assertEqual(db.execute("SELECT generation FROM principals WHERE id='admin'").fetchone()[0], 3)
        self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')
        with sqlite3.connect(self.policy_path) as db:
            simulate_identity(db, "UPDATE principals SET active=0 WHERE id='admin'")
        self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')

    def test_unscoped_auth_and_workspace_list_do_not_interpret_hint(self):
        for path in ('/auth/session', '/api/workspaces'):
            self.assertEqual(self.request(path=path, extra={'HTTP_X_WORKSPACE_ID': None})['status'], 200)
        result = self.request(path='/auth/logout', method='POST', body={}, origin=fixture.ORIGIN,
                              csrf=self.session.csrf.value, extra={'HTTP_X_WORKSPACE_ID': 'beta'})
        self.assertEqual(result['status'], 200)
        self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')
        with self.assertRaises(AuthenticationError):
            self.app._authenticated_transaction(_ConflictingWorkspaceHint(object(), 'alpha', False), Mock())
