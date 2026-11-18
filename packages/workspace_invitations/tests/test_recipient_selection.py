# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Recipient equality fencing through real synthetic Bearer/Cookie boundaries.

These are in-process WSGI/SQLite tests. The lock-wait case uses threads and
separate connections, not processes. Browser HTTP/HTTPS acceptance is separate.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
import sqlite3
from threading import Barrier, Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from packages.workspace_api.tests import test_application as bearer
from packages.workspace_admin_api.tests import retry_namespace_fixture as retry
from packages.workspace_invitations import application, selection, store
from packages.workspace_invitations.tests import selection_fixture as f
from packages.workspaces import PolicyError, Principal, StorageError, WorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity


class RecipientSelectionCodecTests(unittest.TestCase):
    def policy(self):
        people = {
            'admin': Principal('admin', bearer.ISSUER, 'admin-sub', generation=7),
            'recipient': Principal('recipient', bearer.ISSUER, 'subject-中-"-\\', generation=11),
        }
        return SimpleNamespace(_realm='0123456789abcdef0123456789abcdef',
                               _principals=people, _principal=lambda ident: people[ident])

    def test_fixed_literal_canonical_vector_binds_purpose_version_and_escaped_pins(self):
        policy = self.policy()
        # Literal vector, not an expected value assembled by the product helper.
        expected = 'irs1-a807abbe6aab976289076ac6bdf901cf1c55fedc800bf582bc0471113954adde'
        canonical = (b'{"actor":["0123456789abcdef0123456789abcdef","admin",'
            b'"https://identity.example.invalid","admin-sub",7],'
            b'"purpose":"workspace.invitation.create.recipient",'
            b'"realm":"0123456789abcdef0123456789abcdef","role":"viewer",'
            b'"target":["0123456789abcdef0123456789abcdef","recipient",'
            b'"https://identity.example.invalid","subject-\\u4e2d-\\\"-\\\\",11],'
            b'"version":1,"workspace_id":"alpha"}')
        with patch.object(selection, 'sha256', wraps=sha256) as digest:
            self.assertEqual(selection._precondition(policy, 'admin', 'alpha', 'recipient', 'viewer'), expected)
        digest.assert_called_once_with(canonical)
        for field, value in (('purpose', 'workspace.create.initial-admin'), ('version', 2)):
            with self.subTest(field=field):
                changed = json.loads(canonical) | {field: value}
                other = 'irs1-' + sha256(json.dumps(changed, ensure_ascii=True, sort_keys=True,
                    separators=(',', ':')).encode('ascii')).hexdigest()
                self.assertNotEqual(other, expected)

    def test_each_identity_coordinate_scope_and_role_change_the_value(self):
        original = selection._precondition(self.policy(), 'admin', 'alpha', 'recipient', 'viewer')
        for actor in ('admin', 'recipient'):
            for field, value in (('id', 'replacement'), ('issuer', 'https://other.example.invalid'),
                                 ('subject', 'replacement'), ('generation', 19)):
                with self.subTest(actor=actor, field=field):
                    policy = self.policy()
                    policy._principals[actor] = replace(policy._principals[actor], **{field: value})
                    self.assertNotEqual(selection._precondition(policy, 'admin', 'alpha', 'recipient', 'viewer'), original)
        policy = self.policy(); policy._realm = 'f' * 32
        self.assertNotEqual(selection._precondition(policy, 'admin', 'alpha', 'recipient', 'viewer'), original)
        self.assertNotEqual(selection._precondition(self.policy(), 'admin', 'beta', 'recipient', 'viewer'), original)
        self.assertNotEqual(selection._precondition(self.policy(), 'admin', 'alpha', 'recipient', 'developer'), original)
        self.assertNotEqual(selection._precondition(self.policy(), 'recipient', 'alpha', 'admin', 'viewer'), original)


class RecipientSelectionHTTPTests(unittest.TestCase):
    def response(self, case, result, status=200, code=None):
        self.assertEqual(result['status'], status, result)
        headers = dict(result['headers'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Vary'], 'Cookie, Origin' if case.boundary == 'cookie' else 'Authorization')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        if case.boundary == 'cookie':
            self.assertNotIn('WWW-Authenticate', headers)
            self.assertEqual(headers['Pragma'], 'no-cache')
            self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
            self.assertEqual(headers['Content-Security-Policy'], "default-src 'none'; frame-ancestors 'none'")
        if code is not None:
            self.assertEqual(result['body'], {'error': code})
            return None
        self.assertEqual(set(result['body']), {'data'})
        return result['body']['data']

    def reject(self, case, operation, status, code):
        before = case.snapshot()
        self.response(case, operation(), status, code)
        self.assertEqual(case.snapshot(), before)

    def test_review_is_minimal_exact_read_without_business_or_auxiliary_initialization(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                before = case.snapshot()
                case.clock.now += timedelta(seconds=1)
                with patch.object(case.store, '_persist', side_effect=AssertionError('review persisted policy')), \
                     patch.object(application, 'check_schema', side_effect=AssertionError('review checked invitation schema')), \
                     patch.object(application, 'records', side_effect=AssertionError('review read invitations')):
                    item = self.response(case, case.review())
                    self.assertEqual(self.response(case, case.review()), item)
                self.assertEqual(set(item), {'workspace_id', 'principal_id', 'role', 'precondition'})
                self.assertEqual({k: item[k] for k in ('workspace_id', 'principal_id', 'role')},
                    {'workspace_id': 'alpha', 'principal_id': 'outsider', 'role': 'viewer'})
                self.assertRegex(item['precondition'], r'\Airs1-[0-9a-f]{64}\Z')
                self.assertEqual(case.snapshot(), before)
                if boundary == 'cookie':
                    with closing(sqlite3.connect(case.login_path)) as db:
                        self.assertGreater(db.execute('SELECT clock FROM metadata').fetchone()[0],
                                           int(bearer.NOW.timestamp() * 1000000))
                case.sql('DROP TABLE workspace_invitations_v1')
                before = case.snapshot()
                self.assertEqual(self.response(case, case.review()), item)
                self.assertEqual(case.snapshot(), before)
                self.reject(case, lambda: case.create(proof=item['precondition']), 503, 'storage_unavailable')
                names = {row[0] for row in case.sql("SELECT name FROM sqlite_schema WHERE type='table'")}
                self.assertNotIn('workspace_invitations_v1', names)
                self.assertFalse(any('receipt' in name for name in names))

    def test_bare_workspace_api_does_not_gain_review_route_without_invitation_wrapper(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                if boundary == 'cookie':
                    case.app = lambda environ, start: case.responder._respond(
                        environ, start, dispatch=case.base._dispatch)
                else:
                    case.app = case.base
                self.reject(case, case.review, 404, 'not_found')

    def test_review_has_no_directory_query_head_or_extra_segment_alias(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                path = f.COLLECTION + '/selections/outsider/viewer'
                for invalid in (f.COLLECTION + '/selections', f.COLLECTION + '/selections/outsider', path + '/extra'):
                    self.reject(case, lambda: case.request(invalid), 404, 'not_found')
                self.reject(case, lambda: case.request(path, query='search=out'), 400, 'invalid_request')
                self.reject(case, lambda: case.request(path, method='HEAD'), 404, 'not_found')
                for invalid in ('', 'outsider%2Fother'):
                    self.reject(case, lambda: case.review(target=invalid), 400, 'invalid_request')
                for role in ('owner', 'admin', 'Viewer'):
                    self.reject(case, lambda: case.review(role=role), 400, 'invalid_invitation_role')
                if boundary == 'cookie':
                    self.reject(case, lambda: case.request(path, method='OPTIONS'), 401, 'authentication_required')

    def test_review_checks_current_route_admin_and_lifecycle_before_target(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                for target in ('outsider', 'missing', 'disabled'):
                    for actor, status, code in ((None, 401, 'authentication_required'),
                        ('viewer', 403, 'forbidden'), ('other', 404, 'not_found'), ('outsider', 404, 'not_found')):
                        with self.subTest(target=target, actor=actor):
                            self.reject(case, lambda: case.review(target=target, actor=actor), status, code)
                for target in ('missing', 'disabled'):
                    self.reject(case, lambda: case.review(target=target), 404, 'invitation_recipient_unavailable')
                # Admin is also an operator, but has no beta membership.
                for target in ('outsider', 'missing'):
                    self.reject(case, lambda: case.review('beta', target), 404, 'not_found')
                for state, revision in (('suspended', 1), ('archived', 2)):
                    case.store.set_workspace_state('admin', 'alpha', state=state, expected_revision=revision)
                    for target in ('outsider', 'missing'):
                        self.reject(case, lambda: case.review(target=target), 409, 'workspace_unavailable')

    def test_review_remains_identity_only_for_members_and_pending_invitees(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                proof = case.selection()
                self.response(case, case.create(proof=proof))
                before = case.snapshot()
                self.assertEqual(case.selection(), proof)
                member = case.selection(target='viewer')
                self.assertEqual(case.snapshot(), before)
                self.reject(case, lambda: case.create(proof=proof), 409, 'already_invited')
                self.reject(case, lambda: case.create(target='viewer', proof=member), 409, 'already_member')
                self.reject(case, lambda: case.create(target='viewer'), 428, 'invitation_recipient_selection_required')

    def test_fresh_header_full_string_codec_and_mismatch_have_no_effects(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                proof = case.selection()
                self.reject(case, lambda: case.create(), 428, 'invitation_recipient_selection_required')
                invalid = (None, '', 'irs1-' + 'a' * 63, 'irs1-' + 'A' * 64, 'irs2-' + '0' * 64,
                    ' ' + proof, proof + ' ', proof + '\t', proof + '\n', proof + '\x00',
                    proof + ',' + proof, '"' + proof + '"', 'W/"' + proof + '"',
                    'irs1-' + 'é' * 64, True, 7, [], {}, proof.encode())
                for value in invalid:
                    with self.subTest(value=repr(value)):
                        self.reject(case, lambda: case.create(proof=value), 400, 'invalid_invitation_recipient_selection')
                for value in ('irs1-' + '0' * 64, case.selection(role='developer'), case.selection(target='other')):
                    self.reject(case, lambda: case.create(proof=value), 412, 'invitation_recipient_selection_changed')
                self.response(case, case.create(proof=proof))

    def test_existing_post_gates_and_shape_precede_selection_diagnostics(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                for target in ('missing', 'disabled'):
                    self.reject(case, lambda: case.create(target=target), 400, 'invalid_invitee')
                self.reject(case, lambda: case.create(role='admin'), 400, 'invalid_invitation_role')
                for actor, status, code in (('viewer', 403, 'forbidden'), ('outsider', 404, 'not_found')):
                    self.reject(case, lambda: case.create(actor=actor), status, code)
                self.reject(case, lambda: case.create(body={'principal_id': 'outsider', 'role': 'viewer',
                                                           'actor': 'admin'}), 400, 'invalid_request')
                self.reject(case, lambda: case.create(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 400, 'invalid_request')
                case.store.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
                self.reject(case, lambda: case.create(), 409, 'workspace_unavailable')

    def test_get_final_authentication_wins_over_success_and_pending_policy_denials(self):
        for boundary in f.BOUNDARIES:
            for actor, target in (('admin', 'outsider'), ('admin', 'missing'), ('viewer', 'outsider'), ('outsider', 'outsider')):
                with self.subTest(boundary=boundary, actor=actor, target=target), f.SelectionFixture(boundary) as case:
                    original = application.recipient_selection
                    def expire(*args):
                        try:
                            return original(*args)
                        finally:
                            case.expire(actor)
                    with patch.object(application, 'recipient_selection', side_effect=expire):
                        self.reject(case, lambda: case.review(target=target, actor=actor), 401, 'authentication_required')

    def test_w05_is_unwrapped_before_pending_error_capture_and_final_auth(self):
        for boundary in f.BOUNDARIES:
            for actor, status, code in (('admin', 400, 'invalid_request'), ('outsider', 404, 'not_found')):
                with self.subTest(boundary=boundary, actor=actor), f.SelectionFixture(boundary) as case:
                    with patch.object(application, 'recipient_selection', side_effect=AssertionError('W05 inspected target')):
                        self.reject(case, lambda: case.review(actor=actor, target='missing',
                            extra={'HTTP_X_WORKSPACE_ID': 'beta'}), status, code)
                        original = WorkspaceService._member
                        def expire(policy, current_actor, workspace):
                            try:
                                return original(policy, current_actor, workspace)
                            finally:
                                case.expire(actor)
                        with patch.object(WorkspaceService, '_member', expire):
                            self.reject(case, lambda: case.review(actor=actor, target='missing',
                                extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')

    def test_storage_errors_are_not_captured_as_review_outcomes(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                with patch.object(application, 'recipient_selection', side_effect=StorageError('synthetic')):
                    self.reject(case, case.review, 503, 'storage_unavailable')
                # Global policy integrity checks still inspect corrupt auxiliary
                # references. This is not a promise of cross-corruption availability.
                self.response(case, case.create(proof=case.selection()))
                case.sql("UPDATE workspace_invitations_v1 SET principal_id='missing'")
                self.reject(case, case.review, 503, 'storage_unavailable')

    def test_old_key_exact_replay_ignores_all_new_proofs_and_body_conflict_stays_first(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                key = str(uuid4())
                item = self.response(case, case.create(key=key, proof=case.selection()))
                before = case.snapshot()
                for proof in (f.ABSENT, None, 'bad', [], 'irs1-' + '0' * 64, 'bad,duplicate'):
                    with self.subTest(proof=repr(proof)):
                        self.assertEqual(self.response(case, case.create(key=key, proof=proof)), item)
                        self.assertEqual(case.snapshot(), before)
                        self.reject(case, lambda: case.create(key=key, role='developer', proof=proof),
                                    409, 'idempotency_conflict')
                # Legacy binding remains historical, never upgraded during replay.
                legacy = sha256(json.dumps([bearer.ISSUER, 'outsider-sub'],
                    ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()
                case.sql('UPDATE workspace_invitations_v1 SET binding=?', (legacy,))
                before = case.snapshot()
                replay = self.response(case, case.create(key=key, proof='bad'))
                self.assertEqual(replay, item | {'state': 'invalidated'})
                self.assertEqual(case.snapshot(), before)
                self.assertEqual(case.sql('SELECT binding FROM workspace_invitations_v1'), [(legacy,)])

    def test_workspace_actor_role_and_foreign_realm_proofs_do_not_cross_namespaces(self):
        for boundary in retry.BOUNDARIES:
            with self.subTest(boundary=boundary), retry.RetryNamespaceFixture(boundary) as case, \
                    retry.RetryNamespaceFixture(boundary) as foreign:
                proof = case.invitation_selection('alpha')
                key = str(uuid4()); before = case.snapshot()
                for options, selected in (({'workspace': 'beta'}, proof),
                    ({'workspace': 'alpha', 'actor': 'other'}, proof),
                    ({'workspace': 'alpha', 'role': 'developer'}, proof),
                    ({'workspace': 'alpha'}, foreign.invitation_selection('alpha'))):
                    with self.subTest(options=options):
                        self.response(case, case.invite(key=key, proof=selected, **options),
                                      412, 'invitation_recipient_selection_changed')
                        self.assertEqual(case.snapshot(), before)
                for workspace in ('alpha', 'beta'):
                    self.response(case, case.invite(workspace, key=key,
                        proof=case.invitation_selection(workspace)))
                self.assertEqual(len(case.invitation_rows()), 2)

    def test_actor_and_target_aba_require_new_selection_with_current_authority(self):
        for boundary in f.BOUNDARIES:
            for actor in ('admin', 'outsider'):
                for field in ('issuer', 'subject', 'active'):
                    with self.subTest(boundary=boundary, actor=actor, field=field), f.SelectionFixture(boundary) as case:
                        proof = case.selection()
                        case.aba(actor, field)
                        if boundary == 'cookie' and actor == 'admin':
                            self.reject(case, lambda: case.create(proof=proof), 401, 'authentication_required')
                            case.sign_in('admin')
                        self.reject(case, lambda: case.create(proof=proof), 412, 'invitation_recipient_selection_changed')
                        fresh = case.selection()
                        self.assertNotEqual(fresh, proof)
                        self.response(case, case.create(proof=fresh))

    def test_newly_inactive_target_keeps_existing_invalid_invitee_precedence(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                proof = case.selection()
                with closing(sqlite3.connect(case.path, isolation_level=None)) as db:
                    simulate_identity(db, "UPDATE principals SET active=0 WHERE id='outsider'")
                self.reject(case, lambda: case.create(proof=proof), 400, 'invalid_invitee')
                self.reject(case, case.review, 404, 'invitation_recipient_unavailable')

    def test_membership_and_unrelated_data_changes_do_not_invalidate_identity_selection(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                proof = case.selection()
                case.store.set_membership('admin', 'alpha', 'viewer', role='developer', state='active', expected_revision=1)
                case.store.update_resource('admin', 'alpha', 'board', 'same', text='Unrelated synthetic edit', expected_revision=1)
                case.store.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
                self.reject(case, lambda: case.create(proof=proof), 409, 'workspace_unavailable')
                case.store.set_workspace_state('admin', 'alpha', state='active', expected_revision=2)
                self.assertEqual(case.selection(), proof)
                self.response(case, case.create(proof=proof))
                row = case.sql('SELECT workspace_revision,inviter_revision,member_revision FROM workspace_invitations_v1')
                self.assertEqual(row, [(3, 1, 0)])

    def test_restart_preserves_exact_review_but_new_objects_recheck_authority(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                proof = case.selection(); case.restart()
                if boundary == 'cookie': case.sign_in('admin')
                self.assertEqual(case.selection(), proof)
                key = str(uuid4()); item = self.response(case, case.create(key=key, proof=proof))
                case.restart()
                self.assertEqual(case.selection(), proof)
                before = case.snapshot()
                self.assertEqual(self.response(case, case.create(key=key)), item)
                self.assertEqual(case.snapshot(), before)

    def test_private_store_create_cannot_skip_fresh_selection_but_replay_remains_compatible(self):
        with f.SelectionFixture('bearer') as case:
            body = {'principal_id': 'outsider', 'role': 'viewer'}; key = str(uuid4())
            def direct(**kwargs):
                return case.store._transaction(lambda policy, db: store.create(db, policy, 'admin',
                    'alpha', body, key, int(bearer.NOW.timestamp()), **kwargs), write=True, with_connection=True)
            before = case.snapshot()
            with self.assertRaises(PolicyError) as caught: direct()
            self.assertEqual((caught.exception.status, caught.exception.code),
                             (428, 'invitation_recipient_selection_required'))
            self.assertEqual(case.snapshot(), before)
            item = direct(expected_recipient_selection=case.selection()); before = case.snapshot()
            self.assertEqual(direct(), item)
            self.assertEqual(direct(expected_recipient_selection=None), item)
            self.assertEqual(case.snapshot(), before)

    def test_successful_create_and_historical_replay_keep_final_auth_rollback(self):
        for boundary in f.BOUNDARIES:
            for replay in (False, True):
                with self.subTest(boundary=boundary, replay=replay), f.SelectionFixture(boundary) as case:
                    proof = case.selection(); key = str(uuid4())
                    if replay: self.response(case, case.create(proof=proof, key=key))
                    original = case.store._persist
                    def expire(*args, **kwargs):
                        original(*args, **kwargs); case.expire()
                    with patch.object(case.store, '_persist', side_effect=expire):
                        self.reject(case, lambda: case.create(key=key, proof=f.ABSENT if replay else proof),
                                    401, 'authentication_required')

    def test_ordinary_failed_post_keeps_existing_fail_fast_error_semantics(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                original = store.require_recipient_selection
                def expire(*args, **kwargs):
                    try:
                        return original(*args, **kwargs)
                    finally:
                        case.expire()
                with patch.object(store, 'require_recipient_selection', side_effect=expire):
                    self.reject(case, case.create, 428, 'invitation_recipient_selection_required')

    def test_cookie_selection_does_not_replace_origin_csrf_or_session_identity(self):
        with f.SelectionFixture('cookie') as case:
            proof = case.selection()
            for changes in ({'origin': None}, {'origin': 'https://other.example.invalid'}, {'csrf': None},
                            {'csrf': 'x' * 43}, {'cookies': None}, {'extra': {'HTTP_AUTHORIZATION': 'Bearer anything'}}):
                with self.subTest(changes=tuple(changes)):
                    self.reject(case, lambda: case.create(proof=proof, **changes), 401, 'authentication_required')
            for extra in ({'wsgi.url_scheme': 'http'}, {'HTTP_HOST': 'other.example.invalid'},
                          {'HTTP_ORIGIN': 'https://other.example.invalid'}, {'HTTP_SEC_FETCH_SITE': 'cross-site'}):
                self.reject(case, lambda: case.review(extra=extra), 401, 'authentication_required')
            self.response(case, case.create(proof=proof))
            session = case.credentials['admin']; case.login.logout(session.token, session.csrf)
            self.reject(case, case.review, 401, 'authentication_required')
            self.reject(case, lambda: case.create(proof=proof), 401, 'authentication_required')

    def test_writer_wait_rechecks_retained_selection_after_target_aba_commit(self):
        for boundary in f.BOUNDARIES:
            with self.subTest(boundary=boundary), f.SelectionFixture(boundary) as case:
                proof = case.selection(); ready = Barrier(2, timeout=5); attempted = Event(); start = Event()
                connect = case.store._connect
                def observed_connect():
                    db = connect()
                    db.set_trace_callback(lambda sql: attempted.set() if sql == 'BEGIN IMMEDIATE' else None)
                    return db
                def submit():
                    # The rendezvous is before either participant acquires a lock.
                    ready.wait()
                    if not start.wait(5): raise AssertionError('writer did not release caller')
                    return case.create(proof=proof)
                with closing(sqlite3.connect(case.path, isolation_level=None)) as writer, \
                        ThreadPoolExecutor(max_workers=1) as pool, \
                        patch.object(case.store, '_connect', side_effect=observed_connect):
                    # Start the caller only after the separate writer reservation;
                    # no policy callback waits on another transaction's callback.
                    future = pool.submit(submit)
                    ready.wait()
                    writer.execute('BEGIN IMMEDIATE')
                    try:
                        simulate_identity(writer, "UPDATE principals SET subject='temporary' WHERE id='outsider'")
                        simulate_identity(writer, "UPDATE principals SET subject='outsider-sub' WHERE id='outsider'")
                        expected = (writer.execute('PRAGMA user_version').fetchone()[0], tuple(writer.iterdump()))
                        start.set()
                        self.assertTrue(attempted.wait(3), 'POST never attempted its writer transaction')
                        self.assertFalse(future.done(), 'POST did not wait for the held writer')
                    finally:
                        writer.execute('COMMIT')
                        start.set()
                    result = future.result(timeout=5)
                self.response(case, result, 412, 'invitation_recipient_selection_changed')
                self.assertEqual(case.snapshot(), expected)
                self.assertEqual(case.sql('SELECT count(*) FROM workspace_invitations_v1'), [(0,)])
                self.assertEqual(case.sql('SELECT count(*) FROM audit'), [(0,)])
                self.assertEqual(case.sql("SELECT count(*) FROM memberships WHERE principal_id='outsider'"), [(0,)])
                self.assertNotEqual(case.selection(), proof)


if __name__ == '__main__':
    unittest.main()
