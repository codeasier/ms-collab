# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Composed real session-policy transaction, synthetic bound-code login only."""
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host.tests import fixtures
from packages.assistant_protocol import ExecutionPreflight, initialize
from packages.assistant_protocol.preflight import _audit
from packages.identity import AuthenticationError
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.workspace_api import WorkspaceApplication

class SessionPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup);self.now=fixtures.NOW
        self.host,self.policy,self.login,self.exchange=fixtures.setup(self.directory.name,clock=lambda:self.now)
        initialize(self.policy);self.session=fixtures.session(self.login,self.exchange)
        self.preflight=ExecutionPreflight(boundary=WorkspaceApplication(session_boundary=self.host._boundary))
        self.env={'REQUEST_METHOD':'POST','wsgi.url_scheme':'https','HTTP_HOST':'app.example.invalid',
                  'HTTP_ORIGIN':fixtures.ORIGIN,'HTTP_X_CSRF_TOKEN':self.session.csrf.value,
                  'HTTP_COOKIE':cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})}
    def assess(self,env=None):return self.preflight.assess(env or self.env,workspace='alpha',conversation='one',operation='synthetic-op',expected_revision=2)
    def count(self):
        with sqlite3.connect(Path(self.directory.name)/'policy.sqlite') as c:
            return c.execute('SELECT COUNT(*) FROM assistant_execution_preflights').fetchone()[0],c.execute("SELECT COUNT(*) FROM audit WHERE object_id LIKE 'assistant-preflight:%'").fetchone()[0]
    def test_cookie_scope_uses_no_synthetic_bearer_and_is_always_blocked(self):
        self.assertFalse(self.assess()['launch_permitted']);self.assertEqual(self.count(),(1,1))
        with self.assertRaises(AuthenticationError):self.assess(dict(self.env,HTTP_AUTHORIZATION='Bearer synthetic'))
    def test_csrf_and_origin_are_required_even_for_blocked_audit_write(self):
        for key,value in [('HTTP_X_CSRF_TOKEN',''),('HTTP_ORIGIN','https://other.invalid'),('wsgi.url_scheme','http')]:
            with self.assertRaises(AuthenticationError):self.assess(dict(self.env,**{key:value}))
        self.assertEqual(self.count(),(0,0))
    def test_current_logout_invalidates_even_known_receipt(self):
        self.assess();self.login.revoke_subject('admin-sub')
        with self.assertRaises(AuthenticationError):self.assess()
        self.assertEqual(self.count(),(1,1))
    def test_expiry_at_commit_rolls_back_audit_and_receipt(self):
        def expire(*args):_audit(*args);self.now+=timedelta(days=2)
        with patch('packages.assistant_protocol.preflight._audit',side_effect=expire):
            with self.assertRaises(AuthenticationError):self.assess()
        self.assertEqual(self.count(),(0,0))

if __name__=='__main__':unittest.main()
