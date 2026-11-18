# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic transfer boundaries. No provider, runtime enrollment or recovery API.

Tests retain an explicit reviewed tuple. No helper refreshes proof or revisions
when retrying. Cookie requests use the real Session response boundary.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.login_state import LoginBinding, LoginCoordinator, SQLiteLoginStore, s256_challenge
from packages.session_api import SessionPolicyStore, SessionWorkspaceApplication
from packages.session_api.application import _CSRF, _SESSION
from packages.workspace_api import WorkspaceApplication
from packages.workspace_admin_api import WorkspaceAdministrationApplication
from packages.workspace_admin_api.transfer_store import TransferReceiptStore, initialize_transfers
from packages.workspaces import Membership, Principal, SQLiteWorkspaceService, Workspace

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
ORIGIN = 'https://app.example.invalid'
ISSUER = 'https://identity.example.invalid'
BASE = '/api/workspaces/alpha/admin-transfers'
TABLE = 'workspace_admin_transfers_v1'
BROWSER_ACTOR = 'actor-' + '\U0001f680' * 188 + '\x00\x7f\\\ufeffXY'
BROWSER_SUBJECT = 'transfer-actor-sub'


def key(number=1):
    return str(UUID(int=number, version=4))


def initialize_policy(path, *, actor='admin'):
    subjects = {actor: 'admin-sub', 'member': 'member-sub', 'second': 'second-sub',
                'viewer': 'viewer-sub', 'outsider': 'outsider-sub', 'operator': 'operator-sub'}
    return SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p, ISSUER, subject) for p, subject in subjects.items()),
        workspaces=(Workspace('alpha', 'Alpha'), Workspace('beta', 'Beta')),
        memberships=tuple(Membership(w, p, role) for w in ('alpha', 'beta')
            for p, role in ((actor, 'admin'), ('member', 'developer'),
                            ('second', 'admin'), ('viewer', 'viewer'))),
        modules=(), operators=frozenset({'operator', actor}))


def provision_browser(policy):
    """Fixed fresh-fixture provisioning, before handles, sessions or serving.

    The production directory transition is deliberately closed. Like the other
    synthetic identity fixtures this inserts only a fixed fictional principal
    under a temporary guard removal and restores the exact guard before commit.
    No HTTP input, arbitrary principal, profile conversion or real data is used.
    """
    from packages.workspaces.sqlite import _PRINCIPAL_GUARDS
    with closing(policy._connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('DROP TRIGGER principal_replace_closed')
            db.execute('INSERT INTO principals VALUES (?,?,?,?,?,?)',
                       (BROWSER_ACTOR, ISSUER, BROWSER_SUBJECT, 1, 1, 1))
            db.execute(_PRINCIPAL_GUARDS[2])
            realm = db.execute('SELECT realm FROM metadata WHERE id=1').fetchone()[0]
            for workspace in ('alpha', 'beta', 'commons'):
                db.execute('INSERT INTO memberships VALUES (?,?,?,?,?)',
                           (workspace, BROWSER_ACTOR, 'admin', 'active', 1))
                db.execute('INSERT INTO membership_authority VALUES (?,?,?,?,?,?)',
                           (workspace, BROWSER_ACTOR, realm, ISSUER, BROWSER_SUBJECT, 1))
            db.execute('INSERT INTO memberships VALUES (?,?,?,?,?)',
                       ('beta', 'reader', 'viewer', 'active', 1))
            db.execute('INSERT INTO membership_authority VALUES (?,?,?,?,?,?)',
                       ('beta', 'reader', realm, ISSUER, 'reader-sub', 1))
            policy._load(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise


class Clock:
    def __init__(self, now=NOW): self.now = now
    def __call__(self): return self.now


class SyntheticVerifier:
    """Opaque test credentials map to configured subjects, never raw actor IDs."""
    def __init__(self, clock):
        self.clock, self.hook = clock, lambda: None
    def verify(self, credential):
        if type(credential) is not Credential or credential.value not in {
                'test-admin', 'test-member', 'test-second', 'test-viewer',
                'test-outsider', 'test-operator'}:
            raise AuthenticationError()
        self.hook()
        now = self.clock()
        return VerifiedIdentity(ISSUER, credential.value[5:] + '-sub',
                                'synthetic-transfer', now, now + timedelta(hours=1))


class SyntheticExchange:
    def __init__(self, binding, clock): self.binding, self.clock, self.codes = binding, clock, {}
    def authorize(self, challenge, subject):
        code = 'synthetic-code-' + str(len(self.codes))
        self.codes[code] = (challenge, subject)
        return code
    def exchange_bound_code(self, *, code, verifier, callback_uri):
        saved = self.codes.pop(code.value, None)
        if saved is None or saved[0] != s256_challenge(verifier) or callback_uri != self.binding.callback_uri:
            raise AuthenticationError()
        return VerifiedIdentity(self.binding.issuer, saved[1], self.binding.audience,
                                self.clock(), self.clock() + timedelta(minutes=5))


def issue_session(login, subject, clock):
    exchange = SyntheticExchange(login.binding, clock)
    coordinator = LoginCoordinator(login, exchange=exchange)
    attempt = coordinator.begin()
    return coordinator.complete(parameters=(('state', attempt.state.value),
        ('code', exchange.authorize(attempt.code_challenge, subject))),
        browser_binding=attempt.browser_binding, callback_uri=login.binding.callback_uri)


def call(app, path, *, method='GET', body=None, extra=None, query=''):
    raw = json.dumps(body, ensure_ascii=True, separators=(',', ':')).encode('ascii') if body is not None else b''
    env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
        'wsgi.url_scheme': 'https', 'HTTP_HOST': 'app.example.invalid',
        'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
        'wsgi.input': io.BytesIO(raw)}
    env.update(extra or {})
    result = {}
    def start(status, headers):
        result['status'], result['headers'] = int(status.split()[0]), dict(headers)
    payload = b''.join(app(env, start))
    result['body'] = json.loads(payload)
    if 'Content-Length' in result['headers']:
        assert int(result['headers']['Content-Length']) == len(payload)
    return result


def snapshot(path):
    """Full logical synthetic DB snapshot; clock intentionally NOT excluded."""
    with closing(sqlite3.connect(path)) as db:
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        contents = []
        for kind, name, _, _ in schema:
            if kind == 'table' and not name.startswith('sqlite_'):
                escaped = name.replace('"', '""')
                contents.append((name, sorted(db.execute('SELECT * FROM "' + escaped + '"').fetchall(), key=repr)))
        return schema, contents


class TransferFixture:
    def __init__(self, directory, mode, *, actor='admin', mounted=True):
        self.directory, self.mode, self.actor = Path(directory), mode, actor
        self.path = self.directory / 'policy.sqlite'
        self.clock = Clock()
        self.policy = initialize_policy(self.path, actor=actor)
        self.handle = initialize_transfers(self.policy)
        self.login = self.sessions = self.boundary = None
        self.credentials = {}
        if mode == 'bearer':
            self.verifier = SyntheticVerifier(self.clock)
            provider = Provider('synthetic', ISSUER, 'synthetic-transfer', self.verifier,
                                max_assertion_age=timedelta(hours=1))
            self.base = WorkspaceApplication(store=self.policy, providers=(provider,),
                                            provider_key='synthetic', clock=self.clock)
        elif mode == 'cookie':
            binding = LoginBinding(ISSUER, 'synthetic-transfer', ORIGIN + '/auth/callback')
            self.login = SQLiteLoginStore.initialize(self.directory / 'login.sqlite', binding=binding, clock=self.clock)
            self.sessions = SessionPolicyStore(policy=self.policy, login=self.login)
            self.boundary = SessionWorkspaceApplication(sessions=self.sessions, origin=ORIGIN)
            self.base = WorkspaceApplication(session_boundary=self.boundary)
            for name in ('admin', 'member', 'second', 'viewer', 'outsider', 'operator'):
                self.credentials[name] = issue_session(self.login, name + '-sub', self.clock)
        else:
            raise ValueError('fixed test transport required')
        self.mount(mounted)

    def mount(self, mounted=True):
        self.admin = WorkspaceAdministrationApplication(api=self.base, transfers=self.handle if mounted else None)
        if self.mode == 'cookie':
            def application(environ, start_response):
                return self.boundary._respond(environ, start_response, dispatch=self.admin._dispatch)
            self.app = application
        else:
            self.app = self.admin

    def headers(self, actor='admin', *, write=False):
        if self.mode == 'bearer':
            return {'HTTP_AUTHORIZATION': 'Bearer test-' + actor}
        current = self.credentials[actor]
        result = {'HTTP_COOKIE': f'{_SESSION}={current.token.value}; {_CSRF}={current.csrf.value}'}
        if write:
            result.update(HTTP_ORIGIN=ORIGIN, HTTP_X_CSRF_TOKEN=current.csrf.value)
        return result

    def request(self, path=BASE + '/capabilities', *, actor='admin', method='GET', body=None, extra=None, query=''):
        headers = self.headers(actor, write=method not in {'GET', 'HEAD'})
        headers.update(extra or {})
        return call(self.app, path, method=method, body=body, extra=headers, query=query)

    def review(self, *, actor='admin', replacement='member', workspace='alpha'):
        return self.request(f'/api/workspaces/{workspace}/admin-transfers/selections/{replacement}', actor=actor)

    def submit(self, reviewed, *, actor='admin', request_key=None, extra=None, body=None):
        headers = {'HTTP_IF_MATCH': '"' + str(reviewed['actor_revision']) + '"',
            'HTTP_IDEMPOTENCY_KEY': key() if request_key is None else request_key,
            'HTTP_IF_ADMIN_TRANSFER_MATCH': reviewed['precondition']}
        headers.update(extra or {})
        payload = {'replacement_id': reviewed['replacement_id'],
                   'replacement_revision': str(reviewed['replacement_revision'])} if body is None else body
        return self.request(f"/api/workspaces/{reviewed['workspace_id']}/admin-transfers",
                            actor=actor, method='POST', body=payload, extra=headers)

    def promote_back(self, *, workspace='alpha'):
        # An independent legitimate current administrator, not a replay repair.
        member = next(m for m in self.policy.list_memberships('member', workspace) if m.principal_id == self.actor)
        return self.policy.set_membership('member', workspace, self.actor,
            role='admin', state='active', expected_revision=member.revision)

    def counts(self):
        with closing(sqlite3.connect(self.path)) as db:
            return (db.execute('SELECT count(*) FROM ' + TABLE).fetchone()[0],
                    db.execute('SELECT count(*) FROM audit').fetchone()[0],
                    db.execute('SELECT epoch FROM metadata WHERE id=1').fetchone()[0])

    def current(self, *, workspace='alpha'):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT principal_id,role,state,revision FROM memberships WHERE workspace_id=? ORDER BY principal_id', (workspace,)).fetchall()


class ConnectionProxy:
    """Delegating fault injector. Real rollback/commit always reach SQLite."""
    def __init__(self, connection, before=None, after=None):
        self.connection, self.before, self.after = connection, before, after
    def __getattr__(self, name): return getattr(self.connection, name)
    def execute(self, sql, parameters=()):
        if self.before is not None: self.before(self.connection, sql, parameters)
        result = self.connection.execute(sql, parameters)
        if self.after is not None: self.after(self.connection, sql, parameters)
        return result
