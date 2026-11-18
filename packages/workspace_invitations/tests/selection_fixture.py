# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic in-process WSGI/SQLite fixtures, not browser HTTPS or live identity.

Selection and submission are intentionally separate. No request/retry helper
obtains or refreshes a proof. No TestCase is imported into discovery here.
"""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from packages.identity import Provider
from packages.login_state import LoginCoordinator, SQLiteLoginStore
from packages.session_api import SessionPolicyStore, SessionWorkspaceApplication
from packages.session_api.application import _CSRF, _SESSION
from packages.session_api.tests import test_application as cookie
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.tests import test_application as bearer
from packages.workspace_admin_api import WorkspaceAdministrationApplication, initialize_administration
from packages.workspace_invitations import WorkspaceInvitationApplication, initialize_invitations
from packages.workspaces import SQLiteWorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity, simulate_identity_with_core_regrant
from packages.workspaces.tests.test_authority import snapshot

BOUNDARIES = ('bearer', 'cookie')
ABSENT = object()
HEADER = 'HTTP_IF_INVITATION_RECIPIENT_MATCH'
COLLECTION = '/api/workspaces/alpha/invitations'


class SelectionFixture:
    def __init__(self, boundary):
        if boundary not in BOUNDARIES:
            raise ValueError('explicit synthetic boundary required')
        self.boundary = boundary

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        try:
            self.path = Path(self.temp.name) / 'policy.sqlite'
            self.store = bearer.initialize(self.path)
            initialize_administration(self.store)
            initialize_invitations(self.store)
            self.clock = cookie.Clock()
            self.verifier = bearer.SyntheticVerifier()
            if self.boundary == 'cookie':
                self.login_path = Path(self.temp.name) / 'login.sqlite'
                self.login = SQLiteLoginStore.initialize(self.login_path, binding=cookie.BINDING, clock=self.clock)
                self.exchange = cookie.SyntheticExchange(self.clock)
                self.credentials = {}
                self.mount()
                for actor in ('admin', 'viewer', 'other', 'outsider'):
                    self.sign_in(actor)
            else:
                self.mount()
            return self
        except BaseException:
            self.temp.cleanup()
            raise

    def __exit__(self, *_):
        self.temp.cleanup()

    def mount(self):
        if self.boundary == 'cookie':
            self.sessions = SessionPolicyStore(policy=self.store, login=self.login)
            self.responder = SessionWorkspaceApplication(sessions=self.sessions, origin=cookie.ORIGIN)
            self.base = WorkspaceApplication(session_boundary=self.responder)
        else:
            self.base = WorkspaceApplication(store=self.store,
                providers=(Provider('test', bearer.ISSUER, 'test-api', self.verifier),),
                provider_key='test', clock=self.clock)
        admin = WorkspaceAdministrationApplication(api=self.base)
        self.routes = WorkspaceInvitationApplication(api=admin, clock=self.clock)
        if self.boundary == 'cookie':
            self.app = lambda environ, start: self.responder._respond(
                environ, start, dispatch=self.routes._dispatch)
        else:
            self.app = self.routes

    def sign_in(self, actor):
        coordinator = LoginCoordinator(self.login, exchange=self.exchange)
        attempt = coordinator.begin()
        code = self.exchange.authorize(attempt.code_challenge, actor + '-sub')
        self.credentials[actor] = coordinator.complete(
            parameters=(('state', attempt.state.value), ('code', code)),
            browser_binding=attempt.browser_binding, callback_uri=cookie.BINDING.callback_uri)

    def expire(self, actor='admin'):
        self.clock.now = (self.credentials[actor].expires_at if self.boundary == 'cookie'
                          else bearer.NOW + timedelta(minutes=1))

    def request(self, path=COLLECTION, *, actor='admin', **kwargs):
        if self.boundary == 'bearer':
            return bearer.call(self.app, path, token=None if actor is None else 'test-' + actor, **kwargs)
        session = self.credentials.get(actor)
        cookies = None if session is None else cookie.cookie_header(
            {_SESSION: session.token.value, _CSRF: session.csrf.value})
        return cookie.call(self.app, path, cookies=kwargs.pop('cookies', cookies), **kwargs)

    def review(self, workspace='alpha', target='outsider', role='viewer', **kwargs):
        return self.request(f'/api/workspaces/{workspace}/invitations/selections/{target}/{role}', **kwargs)

    def selection(self, workspace='alpha', target='outsider', role='viewer', **kwargs):
        response = self.review(workspace, target, role, **kwargs)
        if response['status'] != 200:
            raise AssertionError(response)
        return response['body']['data']['precondition']

    def create(self, *, workspace='alpha', target='outsider', role='viewer', proof=ABSENT,
               key=None, actor='admin', body=None, **kwargs):
        extra = dict(kwargs.pop('extra', {}))
        extra['HTTP_IDEMPOTENCY_KEY'] = key or str(uuid4())
        if proof is not ABSENT:
            extra[HEADER] = proof
        options = dict(method='POST', body={'principal_id': target, 'role': role} if body is None else body,
                       extra=extra)
        if self.boundary == 'cookie':
            session = self.credentials.get(actor)
            options.update(origin=cookie.ORIGIN, csrf=None if session is None else session.csrf.value)
        options.update(kwargs)
        return self.request(f'/api/workspaces/{workspace}/invitations', actor=actor, **options)

    def sql(self, statement, values=()):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            return db.execute(statement, values).fetchall()

    def snapshot(self):
        # Login security-clock maintenance is deliberately excluded.
        return snapshot(self.path)

    def aba(self, actor, field):
        helper = simulate_identity_with_core_regrant if actor == 'admin' else simulate_identity
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            original = db.execute(f'SELECT {field} FROM principals WHERE id=?', (actor,)).fetchone()[0]
            transient = 0 if field == 'active' else 'synthetic-selection-transient'
            helper(db, f'UPDATE principals SET {field}=? WHERE id=?', (transient, actor))
            helper(db, f'UPDATE principals SET {field}=? WHERE id=?', (original, actor))

    def restart(self):
        self.store = SQLiteWorkspaceService(self.path)
        self.verifier = bearer.SyntheticVerifier()
        if self.boundary == 'cookie':
            self.login = SQLiteLoginStore(self.login_path, binding=cookie.BINDING, clock=self.clock)
        self.mount()
