# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Selection-specific WSGI regressions. Existing retry tests own general CAS.

All directories, bearer assertions, sessions and database files are disposable
synthetic fixtures. Request helpers NEVER automatically obtain a selection.
"""
from contextlib import closing
from datetime import timedelta
from hashlib import sha256
import json
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from packages.workspace_admin_api import WorkspaceAdministrationApplication, initialize_administration
from packages.workspace_admin_api import requests
from packages.workspace_admin_api.tests import test_application as administration
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.tests import test_application as bearer
from packages.session_api.tests import test_application as cookie
from packages.workspaces import SQLiteWorkspaceService, WorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity, simulate_identity_with_core_regrant
from packages.workspaces.tests.test_authority import snapshot

CONTROL = '/api/control/workspaces'
SELECTIONS = '/api/control/initial-admin-selections'
HEADER = 'HTTP_IF_INITIAL_ADMIN_MATCH'
BODY = {'name': 'Explicit synthetic selection', 'kind': 'product', 'initial_admin': 'other'}
ABSENT = object()


class InitialAdminSelectionHTTPTests(unittest.TestCase):
    # Reuse only setup, not the parent TestCase's test methods or create helper.
    setUp = administration.AdministrationTests.setUp

    def request(self, path=CONTROL, **kwargs):
        return bearer.call(self.app, path, **kwargs)

    def preview(self, target='other', **kwargs):
        return self.request(SELECTIONS + '/' + target, **kwargs)

    def proof(self, target='other'):
        result = self.preview(target)
        self.assertEqual(result['status'], 200, result)
        return result['body']['data']['precondition']

    def create(self, *, proof=ABSENT, key=None, body=None, **kwargs):
        extra = dict(kwargs.pop('extra', {}))
        extra['HTTP_IDEMPOTENCY_KEY'] = key or str(uuid4())
        if proof is not ABSENT:
            extra[HEADER] = proof
        return self.request(method='POST', body=BODY.copy() if body is None else body, extra=extra, **kwargs)

    def reject(self, status, code, operation):
        before = snapshot(self.path)
        result = operation()
        self.assertEqual((result['status'], result['body']), (status, {'error': code}), result)
        self.assertEqual(snapshot(self.path), before)

    def sql(self, statement, values=()):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            return db.execute(statement, values).fetchall()

    def aba(self, actor, field):
        helper = simulate_identity_with_core_regrant if actor == 'admin' else simulate_identity
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            original = db.execute(f'SELECT {field} FROM principals WHERE id=?', (actor,)).fetchone()[0]
            transient = 0 if field == 'active' else 'synthetic-selection-transient'
            helper(db, f'UPDATE principals SET {field}=? WHERE id=?', (transient, actor))
            helper(db, f'UPDATE principals SET {field}=? WHERE id=?', (original, actor))

    def test_preview_returns_only_one_explicit_id_and_no_policy_or_retry_writes(self):
        before = snapshot(self.path)
        with patch.object(self.store, '_persist', side_effect=AssertionError('GET persisted policy')):
            first = self.preview()
            second = self.preview()
        self.assertEqual(first['status'], 200)
        self.assertEqual(first['body'], second['body'])
        self.assertEqual(first['headers']['Cache-Control'], 'no-store')
        result = first['body']['data']
        self.assertEqual(set(result), {'initial_admin', 'precondition'})
        self.assertEqual(result['initial_admin'], 'other')
        self.assertRegex(result['precondition'], r'\Aias1-[0-9a-f]{64}\Z')
        for private in ('issuer', 'subject', 'generation', 'realm'):
            self.assertNotIn(private, result)
        self.assertEqual(snapshot(self.path), before)
        for path in (SELECTIONS, SELECTIONS + '/other/extra'):
            self.assertEqual(self.request(path)['status'], 404)
        self.assertEqual(self.preview(query='search=other')['status'], 400)
        self.assertEqual(self.preview(method='POST', body={})['status'], 404)
        for target in ('missing', 'disabled'):
            self.reject(404, 'initial_admin_unavailable', lambda: self.preview(target))

    def test_current_authentication_and_operator_are_required_before_selection(self):
        proof = self.proof()
        for token, status, code in ((None, 401, 'authentication_required'), ('bad', 401, 'authentication_required'),
                                   ('test-disabled', 401, 'authentication_required'), ('test-viewer', 403, 'forbidden')):
            self.reject(status, code, lambda: self.preview(token=token))
            self.reject(status, code, lambda: self.create(proof=proof, token=token))
        key = str(uuid4())
        self.assertEqual(self.create(proof=proof, key=key)['status'], 200)
        self.sql('DELETE FROM operator_authority')
        self.sql('DELETE FROM operators')
        self.reject(403, 'forbidden', lambda: self.preview())
        self.reject(403, 'forbidden', lambda: self.create(key=key))

    def test_new_key_requires_header_and_rejects_bad_types_without_silent_capture(self):
        proof = self.proof()
        with patch.object(WorkspaceService, 'initial_admin_selection', side_effect=AssertionError('POST silently prepared')):
            self.reject(428, 'initial_admin_selection_required', lambda: self.create())
            self.reject(400, 'invalid_initial_admin_selection', lambda: self.create(proof=None))
            for value in ('', 'ias1-' + 'a' * 63, 'ias1-' + 'A' * 64, 'ias2-' + '0' * 64,
                          ' ' + proof, proof + ' ', proof + '\t', proof + '\n', proof + ',' + proof,
                          '"' + proof + '"', True, 7, [], {}, proof.encode()):
                with self.subTest(value=repr(value)):
                    self.reject(400, 'invalid_initial_admin_selection', lambda: self.create(proof=value))
            self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof='ias1-' + '0' * 64))
            self.reject(412, 'initial_admin_selection_changed', lambda: self.create(
                proof=proof, body=BODY | {'initial_admin': 'viewer'}))

    def test_preselection_v2_record_replays_without_new_header_or_digest(self):
        # Seed the exact old v2 seven-column record, with only the original three
        # body fields in its digest. The durable record has NO selection field.
        key = str(uuid4())
        proof = self.proof()
        digest = sha256(json.dumps(BODY, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
        def seed(policy, connection):
            created = policy.create_workspace('admin', **BODY, expected_initial_admin_selection=proof)
            requests.remember(policy, connection, ('admin', 'workspace.create', 'platform', key), digest, created.id, BODY)
            return created.id
        created = self.store._transaction(seed, write=True, with_connection=True)
        self.base_api._store = SQLiteWorkspaceService(self.path)
        before = snapshot(self.path)
        with patch.object(WorkspaceService, 'create_workspace', side_effect=AssertionError('replay created again')):
            for header in (ABSENT, 'wrong', self.proof('viewer'), 'bad,header', [], None):
                result = self.create(proof=header, key=key)
                self.assertEqual(result['status'], 200, result)
                self.assertEqual(result['body']['data']['id'], created)
        self.assertEqual(snapshot(self.path), before)
        self.assertEqual(self.sql('SELECT digest FROM ' + requests._TABLE), [(digest,)])
        self.assertEqual(len(self.sql('PRAGMA table_info(' + requests._TABLE + ')')), 7)
        self.assertEqual(self.sql("SELECT count(*) FROM audit WHERE action='workspace.create'"), [(1,)])
        self.reject(409, 'idempotency_conflict', lambda: self.create(key=key, body=BODY | {'name': 'Different'}))

    def test_actor_and_target_aba_keep_old_key_conflict_and_fresh_key_selection_failure(self):
        for actor in ('admin', 'other'):
            for field in ('issuer', 'subject', 'active'):
                with self.subTest(actor=actor, field=field):
                    proof, key = self.proof(), str(uuid4())
                    self.assertEqual(self.create(proof=proof, key=key)['status'], 200)
                    self.aba(actor, field)
                    self.assertTrue(self.store._transaction(lambda p: p._operator_current('admin'), write=False))
                    self.reject(409, 'idempotency_conflict', lambda: self.create(proof=proof, key=key))
                    self.reject(409, 'idempotency_conflict', lambda: self.create(key=key))
                    self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof=proof))
                    self.reject(428, 'initial_admin_selection_required', lambda: self.create())

    def test_foreign_realm_header_cannot_create_with_same_ids(self):
        other_path = self.path.with_name('other-realm.sqlite')
        other = bearer.initialize(other_path)
        proof = other.initial_admin_selection('admin', 'other')['precondition']
        self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof=proof))

    def test_expiry_after_real_persist_rolls_back_workspace_pins_retry_and_audit(self):
        proof = self.proof()
        original = self.store._persist
        def expire_after(*args, **kwargs):
            original(*args, **kwargs)
            self.now = bearer.NOW + timedelta(minutes=2)
        with patch.object(self.store, '_persist', side_effect=expire_after):
            self.reject(401, 'authentication_required', lambda: self.create(proof=proof))
        self.assertEqual(self.sql('SELECT count(*) FROM ' + requests._TABLE), [(0,)])


class InitialAdminSelectionCookieTests(unittest.TestCase):
    make_session = cookie.SessionHTTPTests.make_session

    def setUp(self):
        cookie.SessionHTTPTests.setUp(self)
        initialize_administration(self.policy)
        self.boundary = self.app
        self.app = WorkspaceAdministrationApplication(api=WorkspaceApplication(session_boundary=self.boundary))

    def preview(self):
        return cookie.call(self.app, SELECTIONS + '/other', cookies=self.cookies)

    def create(self, proof, **changes):
        values = dict(method='POST', cookies=self.cookies, csrf=self.session.csrf.value,
                      origin=cookie.ORIGIN, body=BODY.copy(),
                      extra={'HTTP_IDEMPOTENCY_KEY': str(uuid4()), HEADER: proof})
        values.update(changes)
        return cookie.call(self.app, CONTROL, **values)

    def test_cookie_preview_does_not_write_policy_even_if_login_clock_advances(self):
        before = snapshot(self.policy_path)
        self.clock.now += timedelta(seconds=1)
        result = self.preview()
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(dict(result['headers'])['Cache-Control'], 'no-store')
        self.assertEqual(snapshot(self.policy_path), before)
        self.assertEqual(set(result['body']['data']), {'initial_admin', 'precondition'})
        # Do not compare login DB bytes: advancing its security clock is valid.

    def test_selection_does_not_replace_cookie_origin_csrf_or_live_session(self):
        proof = self.preview()['body']['data']['precondition']
        for changes in ({'origin': None}, {'origin': 'https://other.example.invalid'},
                        {'csrf': None}, {'csrf': 'x' * 43}, {'cookies': None}):
            before = snapshot(self.policy_path)
            with self.subTest(changes=tuple(changes)):
                self.assertEqual(self.create(proof, **changes)['status'], 401)
                self.assertEqual(snapshot(self.policy_path), before)
        self.assertEqual(self.create(proof)['status'], 200)
        self.login.logout(self.session.token, self.session.csrf)
        before = snapshot(self.policy_path)
        self.assertEqual(self.preview()['status'], 401)
        self.assertEqual(self.create(proof)['status'], 401)
        self.assertEqual(snapshot(self.policy_path), before)
