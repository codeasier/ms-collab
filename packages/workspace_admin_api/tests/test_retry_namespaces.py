# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""W13 namespace coordinates through real WSGI/SQLite, synthetic identity only."""
from collections import Counter
from queue import Queue
from threading import Barrier, Thread
from time import monotonic
import unittest
from uuid import uuid4

from packages.session_api.application import _CSRF, _SESSION
from packages.session_api.tests import test_application as cookie
from packages.workspace_admin_api.tests import retry_namespace_fixture as fixtures


class RetryNamespaceTests(unittest.TestCase):
    def response(self, case, result, status=200, code=None):
        self.assertEqual(result['status'], status, result)
        headers = dict(result['headers'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Vary'], 'Cookie, Origin' if case.boundary == 'cookie' else 'Authorization')
        if case.boundary == 'cookie':
            self.assertNotIn('WWW-Authenticate', headers)
        if code is not None:
            self.assertEqual(result['body'], {'error': code})
            return None
        self.assertEqual(set(result['body']), {'data'})
        return result['body']['data']

    def unchanged(self, case, operation, *, status=200, code=None, expected=None):
        before = case.snapshot()
        result = self.response(case, operation(), status, code)
        if expected is not None:
            self.assertEqual(result, expected)
        self.assertEqual(case.snapshot(), before)
        return result

    def test_same_key_same_action_scope_body_changes_only_actor(self):
        for boundary in fixtures.BOUNDARIES:
            with self.subTest(boundary=boundary), fixtures.RetryNamespaceFixture(boundary) as case:
                key = str(uuid4())
                # The opaque selection is actor-bound, obtained independently
                # before writes, and retained unchanged for each actor's replay.
                proofs = {actor: case.selection(actor) for actor in fixtures.ACTORS}
                created = {actor: self.response(case, case.create(key=key, actor=actor,
                    proof=proofs[actor])) for actor in fixtures.ACTORS}
                self.assertNotEqual(created['admin']['id'], created['other']['id'])
                self.assertEqual({row[:4] for row in case.admin_rows()}, {
                    (actor, 'workspace.create', 'platform', key) for actor in fixtures.ACTORS})
                self.assertEqual(len(case.admin_rows()), 2)
                self.assertEqual(len({row[4] for row in case.admin_rows()}), 1)  # Same exact body digest.
                self.assertEqual({row[:4]: row[5] for row in case.admin_rows()}, {
                    (actor, 'workspace.create', 'platform', key): created[actor]['id']
                    for actor in fixtures.ACTORS})
                for actor in fixtures.ACTORS:
                    item = created[actor]
                    self.assertEqual((item['name'], item['kind'], item['state'], item['revision']),
                                     ('Namespace fixture', 'product', 'active', 1))
                    self.assertEqual(case.sql('SELECT principal_id,role,state,revision FROM memberships '
                                              'WHERE workspace_id=?', (item['id'],)),
                                     [('viewer', 'admin', 'active', 1)])
                    self.unchanged(case, lambda actor=actor: case.create(key=key, actor=actor,
                        proof=proofs[actor]), expected=item)
                self.assertCountEqual(case.audit(), [
                    (actor, created[actor]['id'], 'workspace.create', created[actor]['id'], 1)
                    for actor in fixtures.ACTORS])
                self.assertEqual(case.invitation_rows(), [])

    def test_same_key_actor_action_body_changes_only_workspace(self):
        for boundary in fixtures.BOUNDARIES:
            with self.subTest(boundary=boundary), fixtures.RetryNamespaceFixture(boundary) as case:
                key = str(uuid4())
                scopes = ('alpha', 'beta', 'commons')
                items = {scope: self.response(case, case.install(scope, key=key)) for scope in scopes}
                self.assertEqual({row[:4] for row in case.admin_rows()}, {
                    ('admin', 'module.install', scope, key) for scope in scopes})
                self.assertEqual(len(case.admin_rows()), 3)
                self.assertEqual(len({row[4] for row in case.admin_rows()}), 1)
                self.assertEqual({row[5] for row in case.admin_rows()}, {'pipeline'})
                for scope, item in items.items():
                    self.assertEqual(item, {'workspace_id': scope, 'key': 'pipeline',
                        'capabilities': ['read'], 'state': 'disabled', 'revision': 1})
                    self.unchanged(case, lambda scope=scope: case.install(scope, key=key), expected=item)
                self.assertCountEqual(case.sql('SELECT workspace_id,key,state,revision FROM modules'),
                                      [(scope, 'pipeline', 'disabled', 1) for scope in scopes])
                self.assertCountEqual(case.audit(), [
                    ('admin', scope, 'module.install', 'pipeline', 1) for scope in scopes])

    def test_same_key_actor_platform_scope_changes_only_action(self):
        for boundary in fixtures.BOUNDARIES:
            with self.subTest(boundary=boundary), fixtures.RetryNamespaceFixture(boundary) as case:
                key, proof = str(uuid4()), case.selection()
                created = self.response(case, case.create(key=key, proof=proof))
                installed = self.response(case, case.install('platform', key=key))
                # A real workspace named platform isolates the ACTION coordinate
                # from workspace.create's fixed platform scope. Route bodies are
                # necessarily different; both must remain independently legal.
                self.assertEqual({row[:4] for row in case.admin_rows()}, {
                    ('admin', 'workspace.create', 'platform', key),
                    ('admin', 'module.install', 'platform', key)})
                self.assertEqual(len(case.admin_rows()), 2)
                self.assertEqual({row[1]: row[5] for row in case.admin_rows()},
                                 {'workspace.create': created['id'], 'module.install': 'pipeline'})
                self.assertEqual(installed['workspace_id'], 'platform')
                self.assertNotEqual(created['id'], 'platform')
                self.unchanged(case, lambda: case.create(key=key, proof=proof), expected=created)
                self.unchanged(case, lambda: case.install('platform', key=key), expected=installed)
                self.assertCountEqual(case.audit(), [
                    ('admin', created['id'], 'workspace.create', created['id'], 1),
                    ('admin', 'platform', 'module.install', 'pipeline', 1)])

    def test_same_uuid_crosses_administration_and_invitation_tables_without_alias(self):
        for boundary in fixtures.BOUNDARIES:
            with self.subTest(boundary=boundary), fixtures.RetryNamespaceFixture(boundary) as case:
                key, proof = str(uuid4()), case.selection()
                created = self.response(case, case.create(key=key, proof=proof))
                installed = self.response(case, case.install('platform', key=key))
                invited = self.response(case, case.invite('platform', key=key,
                    proof=case.invitation_selection('platform')))
                self.assertEqual(len(case.admin_rows()), 2)
                self.assertEqual({row[:4] for row in case.admin_rows()}, {
                    ('admin', 'workspace.create', 'platform', key),
                    ('admin', 'module.install', 'platform', key)})
                self.assertEqual(case.sql(f'SELECT workspace_id,inviter,request_key,id FROM '
                    f'{fixtures.INVITATION_TABLE}'), [('platform', 'admin', key, invited['id'])])
                self.assertEqual((invited['workspace_id'], invited['principal_id'], invited['state']),
                                 ('platform', 'outsider', 'pending'))
                self.assertEqual(case.sql("SELECT * FROM memberships WHERE principal_id='outsider'"), [])
                self.assertEqual(len({created['id'], installed['key'], invited['id']}), 3)
                for operation, expected in (
                    (lambda: case.create(key=key, proof=proof), created),
                    (lambda: case.install('platform', key=key), installed),
                    (lambda: case.invite('platform', key=key), invited)):
                    self.unchanged(case, operation, expected=expected)
                self.assertCountEqual(case.audit(), [
                    ('admin', created['id'], 'workspace.create', created['id'], 1),
                    ('admin', 'platform', 'module.install', 'pipeline', 1),
                    ('admin', 'platform', 'invitation.create', invited['id'], 1)])

    def test_one_namespace_conflict_and_revocation_leave_other_replays_unchanged(self):
        for boundary in fixtures.BOUNDARIES:
            with self.subTest(boundary=boundary), fixtures.RetryNamespaceFixture(boundary) as case:
                key = str(uuid4())
                first = self.response(case, case.install('alpha', key=key))
                beta = self.response(case, case.install('beta', key=key))
                other = self.response(case, case.install('alpha', key=key, actor='other', module='secondary'))
                self.assertEqual(len(case.admin_rows()), 3)
                self.assertEqual({row[:4] for row in case.admin_rows()}, {
                    ('admin', 'module.install', 'alpha', key),
                    ('admin', 'module.install', 'beta', key),
                    ('other', 'module.install', 'alpha', key)})
                self.unchanged(case, lambda: case.install('alpha', key=key, module='secondary'),
                               status=409, code='idempotency_conflict')
                self.unchanged(case, lambda: case.install('beta', key=key), expected=beta)
                self.unchanged(case, lambda: case.install('alpha', key=key, actor='other',
                    module='secondary'), expected=other)
                rows = case.admin_rows()
                self.response(case, case.member('alpha', 'admin'))
                self.assertEqual(case.admin_rows(), rows)
                # The preceding explicit revoke legitimately emitted one audit;
                # take the denial/replay baseline AFTER that committed mutation.
                audits = case.audit()
                self.assertEqual(len(audits), 4)
                self.assertEqual(audits[-1], ('other', 'alpha', 'membership.update', 'admin', 2))
                self.unchanged(case, lambda: case.install('alpha', key=key), status=404, code='not_found')
                self.unchanged(case, lambda: case.install('beta', key=key), expected=beta)
                self.unchanged(case, lambda: case.install('alpha', key=key, actor='other',
                    module='secondary'), expected=other)
                self.assertEqual(case.admin_rows(), rows)
                self.assertEqual(case.audit(), audits)
                self.assertEqual(case.sql("SELECT state,revision FROM modules WHERE workspace_id='alpha' AND key='pipeline'"),
                                 [(first['state'], first['revision'])])

    def test_mixed_namespace_concurrent_duplicates_commit_once_per_namespace(self):
        for boundary in fixtures.BOUNDARIES:
            with self.subTest(boundary=boundary), fixtures.RetryNamespaceFixture(boundary) as case:
                key, proof = str(uuid4()), case.selection()
                # Sessions and selection are ready before workers. Barrier waits
                # are OUTSIDE the HTTP call and therefore before any SQLite lock.
                operations = {
                    'create': lambda: case.create(key=key, proof=proof),
                    'platform': lambda: case.install('platform', key=key),
                    'beta': lambda: case.install('beta', key=key, actor='other'),
                }
                gate, output = Barrier(6, timeout=5), Queue()

                def worker(name):
                    try:
                        gate.wait()
                        output.put((name, operations[name](), None))
                    except BaseException as error:
                        output.put((name, None, error))

                workers = [Thread(target=worker, args=(name,), daemon=True)
                           for name in operations for _ in range(2)]
                for worker_thread in workers:
                    worker_thread.start()
                deadline = monotonic() + 20
                for worker_thread in workers:
                    worker_thread.join(max(0, deadline - monotonic()))
                self.assertTrue(all(not worker_thread.is_alive() for worker_thread in workers),
                                'synthetic namespace workers did not finish')
                self.assertEqual(output.qsize(), 6)
                results = {name: [] for name in operations}
                for _ in range(6):
                    name, result, error = output.get_nowait()
                    self.assertIsNone(error, f'synthetic worker failed: {type(error).__name__}')
                    results[name].append(self.response(case, result))
                for name in operations:
                    self.assertEqual(len(results[name]), 2)
                    self.assertEqual(results[name][0], results[name][1])
                    self.unchanged(case, operations[name], expected=results[name][0])
                created = results['create'][0]['id']
                self.assertEqual({row[:4]: row[5] for row in case.admin_rows()}, {
                    ('admin', 'workspace.create', 'platform', key): created,
                    ('admin', 'module.install', 'platform', key): 'pipeline',
                    ('other', 'module.install', 'beta', key): 'pipeline'})
                self.assertEqual(len(case.admin_rows()), 3)
                self.assertEqual(case.sql('SELECT id FROM workspaces WHERE id NOT IN (?,?,?,?)', fixtures.SPACES),
                                 [(created,)])
                self.assertEqual(case.sql('SELECT principal_id,role,state,revision FROM memberships WHERE workspace_id=?',
                                         (created,)), [('viewer', 'admin', 'active', 1)])
                self.assertCountEqual(case.sql('SELECT workspace_id,key,state,revision FROM modules'),
                                      [('platform', 'pipeline', 'disabled', 1), ('beta', 'pipeline', 'disabled', 1)])
                self.assertEqual(Counter(case.audit()), Counter([
                    ('admin', created, 'workspace.create', created, 1),
                    ('admin', 'platform', 'module.install', 'pipeline', 1),
                    ('other', 'beta', 'module.install', 'pipeline', 1)]))
                self.assertEqual(case.invitation_rows(), [])

    def test_cookie_fixture_preserves_origin_csrf_and_real_response_boundary(self):
        with fixtures.RetryNamespaceFixture('cookie') as case:
            key = str(uuid4())
            session = case.sessions['admin']
            cookies = cookie.cookie_header({_SESSION: session.token.value, _CSRF: session.csrf.value})
            before = case.snapshot()
            for origin, csrf in ((None, session.csrf.value), (cookie.ORIGIN, None)):
                result = cookie.call(case.app, '/api/workspaces/alpha/modules', method='POST',
                    cookies=cookies, origin=origin, csrf=csrf, body={'key': 'pipeline'},
                    extra={'HTTP_IDEMPOTENCY_KEY': key})
                self.response(case, result, 401, 'authentication_required')
                self.assertEqual(case.snapshot(), before)
            self.response(case, case.install('alpha', key=key))
            self.assertEqual(len(case.admin_rows()), 1)


if __name__ == '__main__':
    unittest.main()
