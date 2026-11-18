# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""One semantic matrix through actual Bearer and Cookie response boundaries."""
from contextlib import closing
from datetime import timedelta
import sqlite3
import tempfile
from threading import Barrier, Event, Thread
import unittest
from unittest.mock import patch

from packages.identity import AuthenticationError
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError, WorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant
from packages.workspace_admin_api.transfer_store import TransferReceiptStore
from packages.workspace_admin_api.tests.transfer_fixtures import (
    BASE, NOW, TABLE, ConnectionProxy, TransferFixture, issue_session, key, snapshot)


class TransferContract:
    """Inherited only by two discovered TestCases, never an unexecuted helper."""
    def setUp(self): self.f = self.fresh()

    def fresh(self, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return TransferFixture(directory.name, self.mode, **kwargs)

    def review(self, f=None, **kwargs):
        response = (f or self.f).review(**kwargs)
        self.assertEqual(response['status'], 200, response)
        return response['body']['data']

    def assert_error(self, response, status, code):
        self.assertEqual(response['status'], status, response)
        self.assertEqual(response['body'], {'error': code})

    def test_three_routes_exact_wire_and_two_core_audits(self):
        f = self.f
        capabilities = f.request()
        self.assertEqual(capabilities['body'], {'data': {'available': True}})
        reviewed = self.review()
        self.assertEqual(set(reviewed), {'workspace_id', 'actor_id', 'replacement_id',
            'actor_revision', 'replacement_revision', 'precondition'})
        self.assertEqual({k: v for k, v in reviewed.items() if k != 'precondition'},
            {'workspace_id': 'alpha', 'actor_id': 'admin', 'replacement_id': 'member',
             'actor_revision': 1, 'replacement_revision': 1})
        self.assertRegex(reviewed['precondition'], r'^ats1-[0-9a-f]{64}$')
        result = f.submit(reviewed)
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(result['body'], {'data': {'workspace_id': 'alpha',
            'actor_id': 'admin', 'replacement_id': 'member', 'actor_role': 'developer',
            'replacement_role': 'admin', 'actor_revision': 2,
            'replacement_revision': 2, 'state': 'committed'}})
        self.assertEqual(f.counts()[:2], (1, 2))
        events = f.policy.audit_events('member', 'alpha')
        self.assertEqual([(e.action, e.actor_id, e.object_id, e.revision) for e in events],
            [('membership.promote', 'admin', 'member', 2),
             ('membership.demote', 'admin', 'admin', 2)])
        self.assertEqual(result['headers']['Vary'], 'Cookie, Origin' if self.mode == 'cookie' else 'Authorization')
        self.assertEqual(result['headers']['Cache-Control'], 'no-store')
        if self.mode == 'cookie': self.assertNotIn('WWW-Authenticate', result['headers'])

    def test_current_authority_precedes_target_or_receipt_diagnostics(self):
        reviewed = self.review()
        for actor, expected in (('viewer', 403), ('outsider', 404), ('operator', 404)):
            for path in (BASE + '/capabilities', BASE + '/selections/missing'):
                with self.subTest(actor=actor, path=path):
                    self.assertEqual(self.f.request(path, actor=actor)['status'], expected)
            changed = dict(reviewed, replacement_id='missing', precondition='ats1-' + '0' * 64)
            self.assertEqual(self.f.submit(changed, actor=actor)['status'], expected)
        self.assertEqual(self.f.counts()[:2], (0, 0))

    def test_missing_credentials_and_current_inactive_actor_never_reach_target_or_key(self):
        reviewed = self.review()
        missing = {'HTTP_AUTHORIZATION': None} if self.mode == 'bearer' else {'HTTP_COOKIE': ''}
        for response in (self.f.request(extra=missing),
                         self.f.request(BASE + '/selections/missing', extra=missing),
                         self.f.submit(reviewed, extra=missing)):
            self.assertEqual(response['status'], 401)
        from packages.workspaces.tests.identity_fixture import simulate_identity
        with sqlite3.connect(self.f.path) as db:
            simulate_identity(db, "UPDATE principals SET active=0 WHERE id='admin'")
        for response in (self.f.request(), self.f.review(replacement='missing'), self.f.submit(reviewed)):
            self.assertEqual(response['status'], 401)
        self.assertEqual(self.f.counts()[:2], (0, 0))

    def test_lifecycle_hidden_scope_and_current_member_gate(self):
        reviewed = self.review()
        for state, rev in (('suspended', 1), ('active', 2), ('archived', 3)):
            self.f.policy.set_workspace_state('operator', 'alpha', state=state, expected_revision=rev)
            if state == 'active': continue
            for response in (self.f.request(), self.f.review(replacement='missing'), self.f.submit(reviewed)):
                self.assert_error(response, 409, 'workspace_unavailable')
        self.assertEqual(self.f.request(BASE.replace('alpha', 'missing') + '/capabilities')['status'], 404)

    def test_self_missing_revoked_and_unowned_replacement(self):
        self.assert_error(self.f.review(replacement='admin'), 409, 'replacement_ineligible')
        self.assert_error(self.f.review(replacement='missing'), 404, 'not_found')
        self.f.policy.set_membership('admin', 'alpha', 'member', role='developer', state='revoked', expected_revision=1)
        self.assert_error(self.f.review(), 409, 'replacement_ineligible')
        f = self.fresh()
        with sqlite3.connect(f.path) as db:
            db.execute("UPDATE membership_authority SET realm=NULL,issuer=NULL,subject=NULL,generation=NULL WHERE workspace_id='alpha' AND principal_id='member'")
        self.assert_error(f.review(), 409, 'replacement_ineligible')

    def test_already_admin_target_remains_legal(self):
        reviewed = self.review(replacement='second')
        result = self.f.submit(reviewed)
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(result['body']['data']['replacement_revision'], 2)
        self.assertEqual(self.f.counts()[:2], (1, 2))

    def test_only_exact_routes_methods_queries_and_body_fields(self):
        for path, method in ((BASE, 'GET'), (BASE, 'HEAD'), (BASE, 'PUT'),
                (BASE + '/capabilities', 'HEAD'), (BASE + '/selections/member', 'HEAD'),
                (BASE + '/selections/member/extra', 'GET'), (BASE + '/receipts', 'GET')):
            with self.subTest(path=path, method=method):
                self.assertEqual(self.f.request(path, method=method)['status'], 404)
        for path in (BASE + '/capabilities', BASE + '/selections/member'):
            self.assertEqual(self.f.request(path, query='limit=1')['status'], 400)
            self.assertEqual(self.f.request(path, body={})['status'], 400)
        reviewed = self.review()
        good = {'replacement_id': 'member', 'replacement_revision': '1'}
        for body in ({'replacement_id': 'member'}, dict(good, actor='admin'),
                     dict(good, state='committed'), dict(good, issuer='synthetic'),
                     dict(good, workspace_id='beta')):
            self.assertEqual(self.f.submit(reviewed, body=body)['status'], 400)
        self.assertEqual(self.f.counts()[:2], (0, 0))

    def test_replacement_revision_is_canonical_safe_decimal_string(self):
        reviewed = self.review()
        for revision in (1, True, 1.0, None, '', '01', '+1', '1.0', ' 1', '0', '9007199254740992'):
            with self.subTest(revision=revision):
                self.assertEqual(self.f.submit(reviewed, body={'replacement_id': 'member',
                    'replacement_revision': revision})['status'], 400)
        self.assertEqual(self.f.counts()[:2], (0, 0))

    def test_idempotency_actor_cas_and_selection_header_shape(self):
        reviewed = self.review()
        for invalid in (None, '', 'not-a-key', key().upper().replace('0000', 'AAAA', 1), '00000000-0000-0000-0000-000000000001'):
            self.assertEqual(self.f.submit(reviewed, extra={'HTTP_IDEMPOTENCY_KEY': invalid})['status'], 400)
        self.assertEqual(self.f.submit(reviewed, extra={'HTTP_IF_MATCH': None})['status'], 428)
        for invalid in ('1', 'W/"1"', '"01"', '"0"', '"1", "2"'):
            self.assertEqual(self.f.submit(reviewed, extra={'HTTP_IF_MATCH': invalid})['status'], 400)
        # None denotes an actually omitted header below, not a header with a bad type.
        headers = {'HTTP_IF_MATCH': '"1"', 'HTTP_IDEMPOTENCY_KEY': key()}
        response = self.f.request(BASE, method='POST', body={'replacement_id': 'member', 'replacement_revision': '1'}, extra=headers)
        self.assert_error(response, 428, 'admin_transfer_selection_required')
        for invalid in ('', 'ats1-' + 'A' * 64, 'ats1-' + '0' * 63, 'ats1-' + '0' * 64 + '\x00'):
            self.assert_error(self.f.submit(reviewed, extra={'HTTP_IF_ADMIN_TRANSFER_MATCH': invalid}),
                              400, 'invalid_admin_transfer_selection')

    def test_both_membership_cas_are_checked_under_writer_lock(self):
        for target in ('admin', 'member'):
            f = self.fresh()
            reviewed = self.review(f)
            role = 'admin' if target == 'admin' else 'viewer'
            f.policy.set_membership('second', 'alpha', target, role=role, state='active', expected_revision=1)
            before = snapshot(f.path)
            self.assert_error(f.submit(reviewed), 412, 'stale_revision')
            self.assertEqual(snapshot(f.path), before)

    def test_selection_vector_changes_but_unrelated_scope_does_not(self):
        reviewed = self.review()
        self.f.policy.set_workspace_state('operator', 'beta', state='suspended', expected_revision=1)
        self.assertEqual(self.f.submit(reviewed)['status'], 200)
        f = self.fresh()
        reviewed = self.review(f)
        f.policy.set_workspace_state('operator', 'alpha', state='suspended', expected_revision=1)
        f.policy.set_workspace_state('operator', 'alpha', state='active', expected_revision=2)
        self.assert_error(f.submit(reviewed), 412, 'admin_transfer_selection_changed')

    def test_target_identity_aba_and_authority_epoch_stale_selection(self):
        for mutation in ('identity', 'epoch'):
            f = self.fresh()
            reviewed = self.review(f)
            with sqlite3.connect(f.path) as db:
                if mutation == 'identity':
                    simulate_identity_with_core_regrant(db, "UPDATE principals SET subject='changed' WHERE id='member'")
                    simulate_identity_with_core_regrant(db, "UPDATE principals SET subject='member-sub' WHERE id='member'")
                else:
                    from packages.workspaces.sqlite import _PRINCIPAL_GUARDS
                    db.execute('DROP TRIGGER principal_authority_closed')
                    db.execute('UPDATE metadata SET epoch=epoch+1 WHERE id=1')
                    db.execute("UPDATE principals SET authority_epoch=2 WHERE id='member'")
                    db.execute(_PRINCIPAL_GUARDS[0])
            self.assert_error(f.submit(reviewed), 412, 'admin_transfer_selection_changed')
            self.assertEqual(f.counts()[:2], (0, 0))

    def test_actor_identity_aba_requires_current_session_then_stale_proof_or_history(self):
        for consumed in (False, True):
            f = self.fresh()
            reviewed = self.review(f)
            if consumed:
                self.assertEqual(f.submit(reviewed)['status'], 200)
                f.promote_back()
            with sqlite3.connect(f.path) as db:
                simulate_identity_with_core_regrant(db, "UPDATE principals SET subject='changed' WHERE id='admin'")
                simulate_identity_with_core_regrant(db, "UPDATE principals SET subject='admin-sub' WHERE id='admin'")
            if self.mode == 'cookie':
                self.assertEqual(f.submit(reviewed)['status'], 401)
                # Explicit synthetic new current login, not bypass or repair of
                # the historical receipt/selection's old generation fence.
                f.credentials['admin'] = issue_session(f.login, 'admin-sub', f.clock)
            before = snapshot(f.path)
            response = f.submit(reviewed)
            self.assert_error(response, 409 if consumed else 412,
                'idempotency_conflict' if consumed else 'admin_transfer_selection_changed')
            self.assertEqual(snapshot(f.path), before)

    def test_membership_and_epoch_headroom_keep_core_counter_error_and_no_receipt(self):
        from packages.workspaces.model import MAX_REVISION
        for exhausted in ('admin', 'member', 'epoch'):
            f = self.fresh()
            with sqlite3.connect(f.path) as db:
                if exhausted == 'epoch': db.execute('UPDATE metadata SET epoch=?', (MAX_REVISION,))
                else: db.execute("UPDATE memberships SET revision=? WHERE workspace_id='alpha' AND principal_id=?", (MAX_REVISION, exhausted))
            reviewed = self.review(f)
            before = snapshot(f.path)
            self.assert_error(f.submit(reviewed), 409, 'counter_exhausted')
            self.assertEqual(snapshot(f.path), before)

    def test_receipts_have_no_ttl_after_fresh_current_authentication(self):
        reviewed = self.review()
        first = self.f.submit(reviewed)
        self.assertEqual(first['status'], 200)
        self.f.promote_back()
        self.f.clock.now = NOW + timedelta(days=400)
        if self.mode == 'cookie':
            self.f.credentials['admin'] = issue_session(self.f.login, 'admin-sub', self.f.clock)
        before = snapshot(self.f.path)
        self.assertEqual(self.f.submit(reviewed), first)
        self.assertEqual(snapshot(self.f.path), before)

    def test_success_self_demotion_immediate_retry_is_current_forbidden(self):
        reviewed = self.review()
        first = self.f.submit(reviewed)
        self.assertEqual(first['status'], 200)
        before = snapshot(self.f.path)
        self.assert_error(self.f.submit(reviewed), 403, 'forbidden')
        self.assertEqual(snapshot(self.f.path), before)
        self.assertEqual(self.f.request()['status'], 403)
        self.assertEqual(self.f.request(BASE.replace('alpha', 'beta') + '/capabilities')['status'], 200)

    def test_independent_repromotion_replays_historical_result_not_current_roles(self):
        reviewed = self.review()
        first = self.f.submit(reviewed)
        self.assertEqual(first['status'], 200)
        promoted = self.f.promote_back()
        self.assertEqual((promoted.role, promoted.revision), ('admin', 3))
        before = snapshot(self.f.path)
        self.assertEqual(self.f.submit(reviewed), first)
        self.assertEqual(snapshot(self.f.path), before)
        self.assertIn(('admin', 'admin', 'active', 3), self.f.current())
        self.assertEqual(self.f.counts()[:2], (1, 3))

    def test_replay_requires_complete_original_intent_before_fresh_diagnostics(self):
        reviewed = self.review()
        self.assertEqual(self.f.submit(reviewed)['status'], 200)
        self.f.promote_back()
        before = snapshot(self.f.path)
        changes = ({'replacement_id': 'second'}, {'actor_revision': 99},
                   {'replacement_revision': 99}, {'precondition': 'ats1-' + '0' * 64})
        for fields in changes:
            with self.subTest(fields=fields):
                self.assert_error(self.f.submit(dict(reviewed, **fields)), 409, 'idempotency_conflict')
        for proof in (None, '', 'malformed'):
            self.assert_error(self.f.submit(reviewed, extra={'HTTP_IF_ADMIN_TRANSFER_MATCH': proof}), 409, 'idempotency_conflict')
        headers = {'HTTP_IF_MATCH': '"1"', 'HTTP_IDEMPOTENCY_KEY': key()}
        self.assert_error(self.f.request(BASE, method='POST',
            body={'replacement_id': 'member', 'replacement_revision': '1'}, extra=headers), 409, 'idempotency_conflict')
        self.assertEqual(snapshot(self.f.path), before)

    def test_replay_allows_later_target_membership_revision_without_rewriting_it(self):
        reviewed = self.review()
        first = self.f.submit(reviewed)
        self.f.promote_back()
        self.f.policy.set_membership('admin', 'alpha', 'member', role='viewer', state='active', expected_revision=2)
        before = snapshot(self.f.path)
        self.assertEqual(self.f.submit(reviewed), first)
        self.assertEqual(snapshot(self.f.path), before)
        self.assertIn(('member', 'viewer', 'active', 3), self.f.current())

    def test_replay_identity_aba_or_ownership_tombstone_never_repairs_history(self):
        for mutation in ('identity', 'ownership'):
            f = self.fresh()
            reviewed = self.review(f)
            self.assertEqual(f.submit(reviewed)['status'], 200)
            f.promote_back()
            with sqlite3.connect(f.path) as db:
                if mutation == 'identity':
                    simulate_identity_with_core_regrant(db, "UPDATE principals SET subject='changed' WHERE id='member'")
                    simulate_identity_with_core_regrant(db, "UPDATE principals SET subject='member-sub' WHERE id='member'")
                else:
                    db.execute("UPDATE membership_authority SET realm=NULL,issuer=NULL,subject=NULL,generation=NULL WHERE workspace_id='alpha' AND principal_id='member'")
            before = snapshot(f.path)
            self.assert_error(f.submit(reviewed), 409, 'idempotency_conflict')
            self.assertEqual(snapshot(f.path), before)

    def test_same_uuid_is_independent_for_other_actor_and_workspace(self):
        first = self.review()
        second = self.review(actor='second', replacement='viewer')
        other_space = self.review(workspace='beta')
        for reviewed, actor in ((first, 'admin'), (second, 'second'), (other_space, 'admin')):
            self.assertEqual(self.f.submit(reviewed, actor=actor, request_key=key(7))['status'], 200)
        self.assertEqual(self.f.counts()[:2], (3, 6))

    def test_restart_new_handle_keeps_consumed_key_and_does_not_reexecute_core(self):
        reviewed = self.review()
        first = self.f.submit(reviewed)
        self.f.promote_back()
        replacement = SQLiteWorkspaceService(self.f.path)
        self.f.policy = replacement
        self.f.handle = TransferReceiptStore(replacement)
        if self.mode == 'bearer': self.f.base._store = replacement
        else: self.f.sessions.policy = replacement
        self.f.mount()
        before = snapshot(self.f.path)
        with patch.object(WorkspaceService, 'transfer_admin', side_effect=AssertionError('must not run')):
            self.assertEqual(self.f.submit(reviewed), first)
        self.assertEqual(snapshot(self.f.path), before)

    def test_full_policy_actor_unicode_nul_del_is_not_truncated_or_used_as_route(self):
        for actor in ('\U0001f680' * 200, 'admin\x00retained\x7f'):
            f = self.fresh(actor=actor)
            reviewed = self.review(f)
            self.assertEqual(reviewed['actor_id'], actor)
            result = f.submit(reviewed)
            self.assertEqual(result['status'], 200, result)
            self.assertEqual(result['body']['data']['actor_id'], actor)
            with sqlite3.connect(f.path) as db:
                stored = db.execute('SELECT CAST(actor AS BLOB) FROM ' + TABLE).fetchone()[0]
                self.assertEqual(stored, actor.encode('utf-8'))
            self.assertEqual(f.review(replacement=actor)['status'], 400)

    def test_w05_read_denial_and_admin_denial_still_use_final_current_guard(self):
        for path, actor, extra in ((BASE + '/capabilities', 'admin', {'HTTP_X_WORKSPACE_ID': 'beta'}),
                (BASE + '/selections/member', 'viewer', {})):
            f = self.fresh()
            if self.mode == 'bearer':
                original = f.base._verifier.validate_current
                count = [0]
                def check(*args):
                    count[0] += 1
                    if count[0] >= 3: raise AuthenticationError()
                    return original(*args)
                target, attr = f.base._verifier, 'validate_current'
            else:
                original = f.sessions._current_actor
                count = [0]
                def check(*args):
                    count[0] += 1
                    if count[0] >= 2: raise AuthenticationError()
                    return original(*args)
                target, attr = f.sessions, '_current_actor'
            with patch.object(target, attr, side_effect=check):
                self.assertEqual(f.request(path, actor=actor, extra=extra)['status'], 401)
            self.assertGreaterEqual(count[0], 2)
            self.assertEqual(f.counts()[:2], (0, 0))

    def test_read_storage_recheck_is_not_delayed_or_replaced_by_final_expiry(self):
        for path in (BASE + '/capabilities', BASE + '/selections/member'):
            with self.subTest(path=path):
                f = self.fresh()
                original = f.handle._admit_schema
                calls = []
                f.clock.now += timedelta(seconds=10)
                expected_floor = f.login._now() if f.login else None
                def admission(db, **kwargs):
                    calls.append(id(db))
                    if len(calls) == 2:
                        # The first gate already admitted clean storage. Only
                        # the in-callback bound recheck fails; if swallowed as
                        # a business denial, final authentication masks its 503.
                        f.clock.now += timedelta(hours=2)
                        raise PolicyError(503, 'admin_transfer_unavailable')
                    return original(db, **kwargs)
                with patch.object(f.handle, '_admit_schema', side_effect=admission):
                    self.assert_error(f.request(path), 503, 'admin_transfer_unavailable')
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0], calls[1])
                self.assertEqual(f.counts()[:2], (0, 0))
                if f.login:
                    with closing(sqlite3.connect(f.login._path)) as db:
                        self.assertEqual(db.execute('SELECT clock FROM metadata WHERE id=1').fetchone(),
                                         (expected_floor,))

    def test_persistence_failure_rolls_back_pair_receipt_audit_and_epoch(self):
        reviewed = self.review()
        before = snapshot(self.f.path)
        with patch.object(self.f.policy, '_persist', side_effect=StorageError('synthetic')):
            self.assertEqual(self.f.submit(reviewed)['status'], 503)
        self.assertEqual(snapshot(self.f.path), before)

    def test_real_independent_writer_changes_current_role_before_waiter_enters(self):
        reviewed = self.review()
        reached, done = Event(), Event()
        results, errors = [], []
        original = self.f.policy._connect
        def connecting():
            def before(db, sql, params):
                if sql == 'BEGIN IMMEDIATE': reached.set()
            return ConnectionProxy(original(), before=before)
        def request():
            try: results.append(self.f.submit(reviewed))
            except BaseException as error: errors.append(error)
            finally: done.set()
        with closing(sqlite3.connect(self.f.path, isolation_level=None)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            policy = self.f.policy._load(writer)
            audit_start = len(policy._audit)
            policy.set_membership('second', 'alpha', 'admin', role='viewer', state='active', expected_revision=1)
            self.f.policy._persist(writer, policy, audit_start=audit_start)
            with patch.object(self.f.policy, '_connect', side_effect=connecting):
                thread = Thread(target=request, daemon=True)
                thread.start()
                try:
                    self.assertTrue(reached.wait(5), 'waiter never attempted its actual writer reservation')
                    self.assertFalse(done.is_set())
                finally:
                    writer.commit()
                    thread.join(10)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([r['status'] for r in results], [403])
        self.assertEqual(self.f.counts()[:2], (0, 1))

    def test_two_real_requests_have_one_commit_and_current_forbidden_loser(self):
        reviewed = self.review()
        gate = Barrier(3, timeout=5)
        results, errors = [], []
        def request():
            try:
                gate.wait()  # Outside every transaction, never holds a DB lock.
                results.append(self.f.submit(reviewed))
            except BaseException as error: errors.append(error)
        workers = [Thread(target=request, daemon=True) for _ in range(2)]
        for worker in workers: worker.start()
        try: gate.wait()
        finally:
            for worker in workers: worker.join(10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r['status'] for r in results), [200, 403])
        self.assertEqual(self.f.counts()[:2], (1, 2))


class BearerTransferTests(TransferContract, unittest.TestCase):
    mode = 'bearer'


class CookieTransferTests(TransferContract, unittest.TestCase):
    mode = 'cookie'

    def test_origin_csrf_and_mixed_credentials_reject_before_storage_admission(self):
        reviewed = self.review()
        for extra in ({'HTTP_ORIGIN': None}, {'HTTP_X_CSRF_TOKEN': None},
                      {'HTTP_ORIGIN': 'https://other.example.invalid'},
                      {'HTTP_AUTHORIZATION': 'Bearer test-admin'}):
            with self.subTest(extra=extra), patch.object(TransferReceiptStore, '_admit_schema') as admission:
                self.assertEqual(self.f.submit(reviewed, extra=extra)['status'], 401)
                admission.assert_not_called()
        self.assertEqual(self.f.counts()[:2], (0, 0))


if __name__ == '__main__': unittest.main()
