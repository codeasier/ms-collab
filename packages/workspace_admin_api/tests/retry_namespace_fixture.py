# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Disposable W13 fixtures, not an authentication or provisioning adapter.

No TestCase lives here: importing this support module cannot rediscover tests.
Selection is an explicit request, never obtained implicitly by a POST/retry.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from packages.identity import Provider
from packages.login_state import LoginCoordinator, SQLiteLoginStore
from packages.session_api import SessionPolicyStore, SessionWorkspaceApplication
from packages.session_api.application import _CSRF, _SESSION
from packages.session_api.tests import test_application as cookie
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.tests import test_application as bearer
from packages.workspace_admin_api import (ModuleTemplate, WorkspaceAdministrationApplication,
                                          initialize_administration)
from packages.workspace_invitations import WorkspaceInvitationApplication, initialize_invitations
from packages.workspaces import Membership, Principal, SQLiteWorkspaceService, Workspace
from packages.workspaces.tests.test_authority import snapshot


BOUNDARIES = ('bearer', 'cookie')
SPACES = ('alpha', 'beta', 'commons', 'platform')
ACTORS = ('admin', 'other')
CREATE_BODY = {'name': 'Namespace fixture', 'kind': 'product', 'initial_admin': 'viewer'}
ADMIN_TABLE = 'workspace_admin_requests_v2'
INVITATION_TABLE = 'workspace_invitations_v1'
ABSENT = object()


class RetryNamespaceFixture:
    def __init__(self, boundary):
        if boundary not in BOUNDARIES:
            raise ValueError('explicit synthetic boundary required')
        self.boundary = boundary

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        try:
            self.path = Path(self.temp.name) / 'policy.sqlite'
            # Fresh, explicit grants only. No identity mutator/regrant helper.
            self.store = SQLiteWorkspaceService.initialize(self.path,
                principals=tuple(Principal(actor, bearer.ISSUER, actor + '-sub')
                                 for actor in ('admin', 'other', 'viewer', 'outsider')),
                workspaces=tuple(Workspace(space, space.title(),
                    'shared' if space == 'commons' else 'product') for space in SPACES),
                memberships=tuple(Membership(space, actor, 'admin')
                                  for space in SPACES for actor in ACTORS),
                modules=(), resources=(), operators=frozenset(ACTORS))
            initialize_administration(self.store)
            initialize_invitations(self.store)
            if self.boundary == 'bearer':
                self.verifier = bearer.SyntheticVerifier()
                base = WorkspaceApplication(store=self.store,
                    providers=(Provider('test', bearer.ISSUER, 'test-api', self.verifier),),
                    provider_key='test', clock=lambda: bearer.NOW)
            else:
                self.clock = cookie.Clock()
                self.login = SQLiteLoginStore.initialize(Path(self.temp.name) / 'login.sqlite',
                    binding=cookie.BINDING, clock=self.clock)
                self.exchange = cookie.SyntheticExchange(self.clock)
                sessions = SessionPolicyStore(policy=self.store, login=self.login)
                boundary = SessionWorkspaceApplication(sessions=sessions, origin=cookie.ORIGIN)
                base = WorkspaceApplication(session_boundary=boundary)
                self.sessions = {}
                for actor in ('admin', 'other', 'viewer', 'outsider'):
                    coordinator = LoginCoordinator(self.login, exchange=self.exchange)
                    attempt = coordinator.begin()
                    code = self.exchange.authorize(attempt.code_challenge, actor + '-sub')
                    self.sessions[actor] = coordinator.complete(
                        parameters=(('state', attempt.state.value), ('code', code)),
                        browser_binding=attempt.browser_binding,
                        callback_uri=cookie.BINDING.callback_uri)
            administration = WorkspaceAdministrationApplication(api=base,
                module_catalog=(ModuleTemplate('pipeline'), ModuleTemplate('secondary')))
            routes = WorkspaceInvitationApplication(api=administration, clock=lambda: bearer.NOW)
            if self.boundary == 'cookie':
                # The real Cookie responder owns security/error headers just as
                # in the trusted host, not the inherited Bearer WSGI responder.
                self.app = lambda environ, start: boundary._respond(
                    environ, start, dispatch=routes._dispatch)
            else:
                self.app = routes
            return self
        except BaseException:
            self.temp.cleanup()
            raise

    def __exit__(self, *_):
        self.temp.cleanup()

    def call(self, path, *, actor='admin', method='GET', body=None, key=None,
             proof=None, revision=None, invitation_proof=ABSENT):
        extra = {}
        if key is not None:
            extra['HTTP_IDEMPOTENCY_KEY'] = key
        if proof is not None:
            extra['HTTP_IF_INITIAL_ADMIN_MATCH'] = proof
        if invitation_proof is not ABSENT:
            extra['HTTP_IF_INVITATION_RECIPIENT_MATCH'] = invitation_proof
        options = dict(method=method, body=body, extra=extra,
                       revision=None if revision is None else f'"{revision}"')
        if self.boundary == 'bearer':
            return bearer.call(self.app, path, token='test-' + actor, **options)
        session = self.sessions[actor]
        cookies = cookie.cookie_header({_SESSION: session.token.value, _CSRF: session.csrf.value})
        # All mutations explicitly retain the production Cookie Origin/CSRF
        # boundary; the key is never a credential or an exemption from it.
        return cookie.call(self.app, path, cookies=cookies, origin=cookie.ORIGIN,
                           csrf=session.csrf.value, **options)

    def selection(self, actor='admin', target='viewer'):
        response = self.call('/api/control/initial-admin-selections/' + target, actor=actor)
        if response['status'] != 200:
            raise AssertionError(response)
        return response['body']['data']['precondition']

    def create(self, *, key, actor='admin', proof=None, body=None):
        return self.call('/api/control/workspaces', method='POST', actor=actor,
                         key=key, proof=proof, body=CREATE_BODY.copy() if body is None else body)

    def install(self, workspace, *, key, actor='admin', module='pipeline'):
        return self.call(f'/api/workspaces/{workspace}/modules', method='POST', actor=actor,
                         key=key, body={'key': module})

    def invitation_selection(self, workspace, *, actor='admin', target='outsider', role='viewer'):
        response = self.call(f'/api/workspaces/{workspace}/invitations/selections/{target}/{role}', actor=actor)
        if response['status'] != 200:
            raise AssertionError(response)
        return response['body']['data']['precondition']

    def invite(self, workspace, *, key, actor='admin', target='outsider', role='viewer', proof=ABSENT):
        return self.call(f'/api/workspaces/{workspace}/invitations', method='POST', actor=actor,
                         key=key, invitation_proof=proof, body={'principal_id': target, 'role': role})

    def member(self, workspace, target, *, actor='other', role='viewer', state='revoked', revision=1):
        return self.call(f'/api/workspaces/{workspace}/members/{target}', method='PATCH',
                         actor=actor, body={'role': role, 'state': state}, revision=revision)

    def sql(self, query, params=()):
        # Tests use this only for readback; setup/mutations use actual services.
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            return db.execute(query, params).fetchall()

    def snapshot(self):
        # Policy, auxiliary records, schema and audit. Cookie login clock state
        # deliberately lives elsewhere and is not asserted byte-identical.
        return snapshot(self.path)

    def admin_rows(self):
        return self.sql(f'SELECT actor,action,scope,request_key,digest,result_id,authority '
                        f'FROM {ADMIN_TABLE} ORDER BY actor,action,scope,request_key')

    def invitation_rows(self):
        return self.sql(f'SELECT * FROM {INVITATION_TABLE} ORDER BY workspace_id,inviter,request_key')

    def audit(self):
        return self.sql('SELECT actor_id,workspace_id,action,object_id,revision FROM audit ORDER BY sequence')
