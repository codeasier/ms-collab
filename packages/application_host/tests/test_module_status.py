"""W08 member status through the real host Cookie/SQLite composition.

Fresh synthetic stores only. Final current-session validation is never replaced
by an always-successful fake, and no browser or provider acquisition is needed.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import re
import sqlite3
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests import fixtures
from packages.directory_cursors import initialize_directory_cursors
from packages.identity import AuthenticationUnavailable
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import call, cookie_header, cookie_values
from packages.workspace_api.tests.test_module_status import database_snapshot, forbidden, RECEIPT_TRAPS
from packages.workspaces import Membership, WorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity


STATUS = '/api/workspaces/alpha/modules/pipelines'
FIELDS = {'workspace_id', 'key', 'state', 'revision'}
GATES = {
    'normal': (200, None),
    'empty': (200, None),
    'disabling': (200, None),
    'disabled': (200, None),
    'archived': (200, None),
    'missing_module': (404, 'not_found'),
    'missing_workspace': (404, 'not_found'),
    'nonmember': (404, 'not_found'),
    'revoked': (404, 'not_found'),
    'suspended': (409, 'workspace_unavailable'),
    'hint': (400, 'invalid_request'),
}


def signed_cookie(login, exchange, subject):
    session = fixtures.session(login, exchange, subject)
    return session, cookie_header({_SESSION: session.token.value, _CSRF: session.csrf.value})


class HostModuleStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = fixtures.NOW
        self.app, self.policy, self.login, self.exchange = fixtures.setup(
            self.temp.name, clock=lambda: self.now, directory_cursors=False)
        self.sessions = self.app._boundary._sessions
        self.session, self.cookies = signed_cookie(self.login, self.exchange, 'admin-sub')

    def request(self, path=STATUS, **kwargs):
        return call(self.app, path, cookies=kwargs.pop('cookies', self.cookies), **kwargs)

    def mutate(self, path=STATUS, **kwargs):
        options = dict(method='PATCH', origin=fixtures.ORIGIN, csrf=self.session.csrf.value,
                       body={'state': 'disabling'}, revision='"1"')
        options.update(kwargs)
        return self.request(path, **options)

    def rejected(self, result, status, code):
        self.assertEqual((result['status'], result['body']), (status, {'error': code}), result)
        headers = dict(result['headers'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Vary'], 'Cookie, Origin')
        self.assertNotIn('WWW-Authenticate', headers)

    def projected(self, result, workspace='alpha', key='pipelines', state='enabled', revision=1):
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(set(result['body']), {'data'})
        data = result['body']['data']
        self.assertEqual(set(data), FIELDS)
        self.assertEqual(data, dict(workspace_id=workspace, key=key, state=state, revision=revision))
        self.assertIs(type(data['revision']), int)
        self.assertEqual(dict(result['headers'])['Vary'], 'Cookie, Origin')
        self.assertEqual(dict(result['headers'])['Cache-Control'], 'no-store')
        self.assertFalse(cookie_values(result))

    def reader_in_three_spaces(self):
        # Explicit synthetic provisioning, with real authority capture for NEW
        # memberships. This is not identity rebind or stale-grant recovery.
        def provision(policy):
            policy._grant_membership(Membership('beta', 'reader', 'developer'))
            policy._grant_membership(Membership('commons', 'reader', 'admin'))
        self.policy._transaction(provision, write=True)
        return signed_cookie(self.login, self.exchange, 'reader-sub')

    def test_same_key_is_scoped_to_three_spaces_and_local_roles(self):
        _, cookies = self.reader_in_three_spaces()
        roles = self.policy._transaction(lambda policy: tuple(
            policy._member('reader', workspace).role for workspace in ('alpha', 'beta', 'commons')), write=False)
        self.assertEqual(roles, ('viewer', 'developer', 'admin'))
        self.policy.set_module_state('second', 'beta', 'pipelines', state='disabling', expected_revision=1)
        self.policy.set_module_state('second', 'commons', 'pipelines', state='disabling', expected_revision=1)
        self.policy.set_module_state('second', 'commons', 'pipelines', state='disabled', expected_revision=2)
        for workspace, state, revision in (('alpha', 'enabled', 1), ('beta', 'disabling', 2), ('commons', 'disabled', 3)):
            path = STATUS.replace('/alpha/', '/' + workspace + '/')
            self.projected(self.request(path, cookies=cookies), workspace, state=state, revision=revision)
            self.projected(self.request(path, cookies=cookies, extra={'HTTP_X_WORKSPACE_ID': workspace}),
                           workspace, state=state, revision=revision)
            self.rejected(self.request(path, cookies=cookies, extra={'HTTP_X_WORKSPACE_ID': 'wrong'}),
                          400, 'invalid_request')

    def test_lifecycle_sequence_keeps_status_visible_but_data_gate_denied(self):
        _, cookies = self.reader_in_three_spaces()
        for state, revision in (('enabled', 1), ('disabling', 2), ('disabled', 3)):
            if revision > 1:
                for workspace in ('alpha', 'beta', 'commons'):
                    self.policy.set_module_state('second', workspace, 'pipelines', state=state,
                                                 expected_revision=revision - 1)
            for workspace in ('alpha', 'beta', 'commons'):
                path = STATUS.replace('/alpha/', '/' + workspace + '/')
                self.projected(self.request(path, cookies=cookies), workspace, state=state, revision=revision)
                data_path = fixtures.READS[1].replace('/alpha/', '/' + workspace + '/')
                result = self.request(data_path, cookies=cookies)
                if state == 'enabled':
                    self.assertEqual(result['status'], 200, result)
                else:
                    self.rejected(result, 409, 'module_unavailable')
                # The host still refuses raw resources around module ACLs.
                self.rejected(self.request(path + '/resources/raw', cookies=cookies), 404, 'not_found')

    def test_empty_capabilities_allow_status_not_module_data_or_configuration(self):
        _, cookies = self.reader_in_three_spaces()
        with sqlite3.connect(self.policy._path) as db:
            db.execute("UPDATE modules SET can_read=0,can_write=0 WHERE key='pipelines'")
        for workspace in ('alpha', 'beta', 'commons'):
            path = STATUS.replace('/alpha/', '/' + workspace + '/')
            self.projected(self.request(path, cookies=cookies), workspace)
            self.rejected(self.request(fixtures.READS[1].replace('/alpha/', '/' + workspace + '/'),
                                      cookies=cookies), 403, 'forbidden')
        for suffix in ('modules', 'module-catalog'):
            self.rejected(self.request('/api/workspaces/alpha/' + suffix, cookies=cookies), 403, 'forbidden')

    def test_admin_list_catalog_and_patch_keep_their_existing_authority_and_cas(self):
        reader, cookies = self.reader_in_three_spaces()
        for workspace in ('alpha', 'beta'):
            for suffix in ('modules', 'module-catalog'):
                self.rejected(self.request(f'/api/workspaces/{workspace}/{suffix}', cookies=cookies),
                              403, 'forbidden')
            self.rejected(self.mutate(STATUS.replace('/alpha/', '/' + workspace + '/'), cookies=cookies,
                                     csrf=reader.csrf.value), 403, 'forbidden')
        for suffix in ('modules', 'module-catalog'):
            result = self.request('/api/workspaces/alpha/' + suffix)
            self.assertEqual(result['status'], 200, result)
            self.assertTrue(result['body']['data'])
            self.assertTrue(all('capabilities' in item for item in result['body']['data']))
        self.rejected(self.mutate(csrf=None), 401, 'authentication_required')
        changed = self.mutate()
        self.assertEqual(changed['status'], 200, changed)
        self.assertIn('capabilities', changed['body']['data'])
        self.projected(self.request(cookies=cookies), state='disabling', revision=2)
        self.rejected(self.mutate(body={'state': 'disabled'}), 412, 'stale_revision')
        self.assertEqual(self.mutate(body={'state': 'disabled'}, revision='"2"')['status'], 200)
        self.projected(self.request(cookies=cookies), state='disabled', revision=3)

    def test_authentication_unknown_nonmember_and_operator_only_are_non_enumerating(self):
        _, outsider = signed_cookie(self.login, self.exchange, 'outsider-sub')
        for workspace in ('alpha', 'beta', 'commons', 'missing'):
            for module in ('pipelines', 'missing'):
                path = f'/api/workspaces/{workspace}/modules/{module}'
                self.rejected(self.request(path, cookies=''), 401, 'authentication_required')
                self.rejected(self.request(path, cookies=outsider), 404, 'not_found')
                if workspace != 'alpha':  # admin is operator but is not a member.
                    self.rejected(self.request(path), 404, 'not_found')
        self.rejected(self.request(STATUS.replace('/pipelines', '/missing')), 404, 'not_found')
        self.rejected(self.request(extra={'HTTP_AUTHORIZATION': 'Bearer test-admin'}), 401, 'authentication_required')
        self.rejected(self.request(cookies='', extra={'HTTP_X_USER_ID': 'admin',
            'HTTP_X_SUBJECT': 'admin-sub', 'REMOTE_USER': 'admin'}), 401, 'authentication_required')

    def test_suspension_precedes_installed_key_but_not_membership(self):
        _, reader = signed_cookie(self.login, self.exchange, 'reader-sub')
        _, outsider = signed_cookie(self.login, self.exchange, 'outsider-sub')
        self.policy.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
        for module in ('pipelines', 'missing'):
            path = STATUS.replace('/pipelines', '/' + module)
            self.rejected(self.request(path, cookies=reader), 409, 'workspace_unavailable')
            self.rejected(self.request(path, cookies=outsider), 404, 'not_found')
        self.rejected(self.request(cookies=reader, extra={'HTTP_X_WORKSPACE_ID': 'beta'}),
                      400, 'invalid_request')  # W05's existing current-member precedence is unchanged.
        self.rejected(self.request(fixtures.READS[1], cookies=reader), 409, 'workspace_unavailable')

    def test_archive_keeps_status_without_adding_mutation_or_catalog_authority(self):
        _, reader = signed_cookie(self.login, self.exchange, 'reader-sub')
        self.policy.set_workspace_state('admin', 'alpha', state='archived', expected_revision=1)
        self.projected(self.request(cookies=reader))
        self.assertEqual(self.request(fixtures.READS[1], cookies=reader)['status'], 200)
        self.assertEqual(self.request('/api/workspaces/alpha/modules')['status'], 200)
        self.rejected(self.request('/api/workspaces/alpha/modules', cookies=reader), 403, 'forbidden')
        self.rejected(self.request('/api/workspaces/alpha/module-catalog'), 409, 'workspace_unavailable')
        self.rejected(self.mutate(), 409, 'workspace_unavailable')

    def test_exact_route_query_head_extra_and_hint_do_not_expand_dispatch(self):
        for query in ('x=1', 'after=%XX', 'capabilities=1', 'workspace_id=beta', 'offset=0'):
            self.rejected(self.request(query=query), 400, 'invalid_request')
        for method in ('HEAD', 'OPTIONS'):
            self.rejected(self.request(method=method), 404, 'not_found')
        for path in (STATUS + '/', STATUS + '/extra', STATUS.replace('/pipelines', '/Pipelines')):
            self.rejected(self.request(path), 404, 'not_found')
        for hint in ('beta', '', ' alpha', 'alpha ', 'ALPHA', 'alpha,beta', None, ['alpha']):
            self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': hint}), 400, 'invalid_request')
        self.projected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'alpha'}))
        self.rejected(self.request(cookies='', extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 401, 'authentication_required')
        self.rejected(self.request(STATUS.replace('/alpha/', '/beta/'), extra={'HTTP_X_WORKSPACE_ID': 'alpha'}),
                      404, 'not_found')
        self.projected(self.request())

    def test_status_observation_never_supplies_module_acl_owner_or_host_mount_authority(self):
        _, second = signed_cookie(self.login, self.exchange, 'second-sub')
        for key in ('community', 'assistant'):
            self.projected(self.request(STATUS.replace('/pipelines', '/' + key), cookies=second), key=key)
        self.rejected(self.request(fixtures.READS[0], cookies=second), 404, 'not_found')
        self.rejected(self.request(fixtures.READS[-1], cookies=second), 404, 'not_found')
        unmounted = ApplicationHost(config=HostConfig(fixtures.ORIGIN), sessions=self.sessions)
        self.projected(call(unmounted, STATUS, cookies=self.cookies))
        self.rejected(call(unmounted, fixtures.READS[1], cookies=self.cookies), 404, 'not_found')
        _, reader = signed_cookie(self.login, self.exchange, 'reader-sub')
        self.projected(self.request(cookies=reader))
        self.policy.set_membership('admin', 'alpha', 'reader', role='viewer', state='revoked', expected_revision=1)
        self.rejected(self.request(cookies=reader), 404, 'not_found')
        self.rejected(self.request(fixtures.READS[1], cookies=reader), 404, 'not_found')

    def test_cookie_identity_aba_old_session_and_new_session_do_not_regrant_membership(self):
        for field, away, restored in (('active', 0, 1), ('subject', 'different-sub', 'reader-sub')):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                app, policy, login, exchange = fixtures.setup(directory, directory_cursors=False)
                _, old_cookie = signed_cookie(login, exchange, 'reader-sub')
                self.projected(call(app, STATUS, cookies=old_cookie))
                with sqlite3.connect(policy._path) as db:
                    generation = db.execute("SELECT generation FROM principals WHERE id='reader'").fetchone()[0]
                    for value in (away, restored):
                        simulate_identity(db, 'UPDATE principals SET ' + field + "=? WHERE id='reader'", (value,))
                    self.assertEqual(db.execute("SELECT generation FROM principals WHERE id='reader'").fetchone()[0],
                                     generation + 2)
                self.rejected(call(app, STATUS, cookies=old_cookie), 401, 'authentication_required')
                _, new_cookie = signed_cookie(login, exchange, 'reader-sub')
                self.assertEqual(call(app, '/auth/session', cookies=new_cookie)['status'], 200)
                self.rejected(call(app, STATUS, cookies=new_cookie), 404, 'not_found')
                self.rejected(call(app, fixtures.READS[1], cookies=new_cookie), 404, 'not_found')
                with sqlite3.connect(policy._path) as db:
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute("UPDATE principals SET active=0 WHERE id='reader'")

    def final_matrix(self, outcome, *, schema=False):
        for condition, expected in GATES.items():
            with self.subTest(condition=condition, outcome=outcome, schema=schema), tempfile.TemporaryDirectory() as directory:
                now = [fixtures.NOW]
                app, policy, login, exchange = fixtures.setup(
                    directory, clock=lambda: now[0], directory_cursors=False)
                if schema:
                    initialize_directory_cursors(policy, layout='explicit-factory-v1')
                who = 'outsider-sub' if condition == 'nonmember' else 'reader-sub'
                session, cookies = signed_cookie(login, exchange, who)
                if condition in ('suspended', 'archived'):
                    policy.set_workspace_state('admin', 'alpha', state=condition, expected_revision=1)
                if condition in ('disabling', 'disabled'):
                    policy.set_module_state('admin', 'alpha', 'pipelines', state='disabling', expected_revision=1)
                    if condition == 'disabled':
                        policy.set_module_state('admin', 'alpha', 'pipelines', state='disabled', expected_revision=2)
                if condition == 'empty':
                    with sqlite3.connect(policy._path) as db:
                        db.execute("UPDATE modules SET can_read=0,can_write=0 WHERE workspace_id='alpha' AND key='pipelines'")
                if condition == 'revoked':
                    policy.set_membership('admin', 'alpha', 'reader', role='viewer', state='revoked', expected_revision=1)
                path = (STATUS.replace('/alpha/', '/missing/') if condition == 'missing_workspace' else
                        STATUS.replace('/pipelines', '/missing') if condition == 'missing_module' else STATUS)
                extra = {'HTTP_X_WORKSPACE_ID': 'beta'} if condition == 'hint' else None
                before = database_snapshot(policy._path)
                login_before = database_snapshot(login._path)
                old_clock = login_before[1]['metadata'][0][-1]
                sessions = app._boundary._sessions
                current = sessions._current_actor
                calls, trace = [], []
                def observe_current(*args):
                    calls.append(args[-1])
                    actor = current(*args)  # Actual session/principal/generation/expiry check.
                    if len(calls) == 1:
                        now[0] = session.expires_at if outcome == 'expired' else now[0] + timedelta(seconds=1)
                    elif outcome == 'unavailable':
                        raise AuthenticationUnavailable()
                    return actor
                connect = policy._connect
                def traced_connect():
                    db = connect()
                    db.set_trace_callback(trace.append)
                    return db
                getter = WorkspaceService.get_module_status
                with ExitStack() as stack:
                    stack.enter_context(patch.object(policy, '_connect', side_effect=traced_connect))
                    checked = stack.enter_context(patch.object(sessions, '_current_actor', side_effect=observe_current))
                    business = stack.enter_context(patch.object(WorkspaceService, 'get_module_status', autospec=True, side_effect=getter))
                    traps = [stack.enter_context(patch(name, side_effect=forbidden)) for name in RECEIPT_TRAPS]
                    traps.append(stack.enter_context(patch.object(policy, '_persist', side_effect=forbidden)))
                    traps.append(stack.enter_context(patch.object(exchange, 'exchange_bound_code', side_effect=forbidden)))
                    traps.append(stack.enter_context(patch('packages.community_sync.GitCodeProvider.observe', side_effect=forbidden)))
                    result = call(app, path, cookies=cookies, extra=extra)
                    self.assertEqual(checked.call_count, 2)
                    self.assertEqual(business.call_count, 0 if condition == 'hint' else 1)
                    for guard in traps:
                        guard.assert_not_called()
                self.assertEqual(len(calls), 2)
                self.assertGreater(calls[1], calls[0])
                status, code = (expected if outcome == 'valid' else
                                (401, 'authentication_required') if outcome == 'expired' else
                                (503, 'authentication_unavailable'))
                if status == 200:
                    self.assertEqual(result['status'], 200, result)
                    self.assertEqual(set(result['body']['data']), FIELDS)
                else:
                    self.rejected(result, status, code)
                self.assertFalse(cookie_values(result))
                self.assertEqual(database_snapshot(policy._path), before)
                self.assertIn('BEGIN IMMEDIATE', trace)
                self.assertIn('SAVEPOINT business', trace)  # Ordinary Cookie path is intentionally retained.
                self.assertFalse(any('directory_cursor_' in sql.lower() for sql in trace), trace)
                writes = [sql for sql in trace if re.match(r'\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', sql, re.I)]
                self.assertEqual(len(writes), 1, writes)
                self.assertTrue(writes[0].startswith('UPDATE login.metadata SET clock='), writes)
                login_after = database_snapshot(login._path)
                expected_clock = (now[0] - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(microseconds=1)
                with sqlite3.connect(login._path) as db:
                    self.assertEqual(db.execute('SELECT clock FROM metadata WHERE id=1').fetchone()[0], expected_clock)
                self.assertGreater(expected_clock, old_clock)
                self.assertEqual(login_after[0], login_before[0])
                # Only the existing security clock may change, not sessions,
                # attempts, policy rows, receipts, schema or other metadata.
                for name in login_before[1]:
                    if name != 'metadata':
                        self.assertEqual(login_after[1][name], login_before[1][name], name)
                self.assertEqual(login_after[1]['metadata'][0][:-1], login_before[1]['metadata'][0][:-1])

    def test_real_final_session_validation_preserves_normal_gate_and_w05_results(self):
        self.final_matrix('valid')

    def test_real_final_expiry_overrides_normal_gate_and_w05_results_and_keeps_clock(self):
        self.final_matrix('expired')

    def test_final_availability_fault_overrides_normal_gate_and_w05_results_and_keeps_clock(self):
        self.final_matrix('unavailable')

    def test_existing_receipt_schema_is_not_accessed_or_changed_in_any_final_outcome(self):
        for outcome in ('valid', 'expired', 'unavailable'):
            self.final_matrix(outcome, schema=True)

    def test_real_core_projection_expiry_uses_final_session_guard(self):
        getter = WorkspaceService.get_module_status
        def expire_after_projection(policy, *args):
            result = getter(policy, *args)
            self.now = self.session.expires_at
            return result
        with patch.object(WorkspaceService, 'get_module_status', autospec=True, side_effect=expire_after_projection) as business:
            self.rejected(self.request(), 401, 'authentication_required')
            self.assertEqual(business.call_count, 1)

    def test_pending_rejection_is_request_local_and_only_policy_errors_are_deferred(self):
        self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 400, 'invalid_request')
        self.projected(self.request())
        self.rejected(self.request(STATUS.replace('/pipelines', '/missing')), 404, 'not_found')
        self.projected(self.request())
        current = self.sessions._current_actor
        with patch.object(self.sessions, '_current_actor', wraps=current) as checked, \
                patch.object(WorkspaceService, 'get_module_status', side_effect=RuntimeError('synthetic private detail')):
            result = self.request()
        # An unexpected business exception keeps the ordinary transaction's
        # fail-closed behavior, rather than being treated as a public PolicyError.
        self.rejected(result, 503, 'authentication_unavailable')
        self.assertEqual(checked.call_count, 1)
        self.assertNotIn('synthetic private detail', str(result))

    def test_cookie_writer_lock_then_membership_revocation_is_observed(self):
        _, cookies = signed_cookie(self.login, self.exchange, 'reader-sub')
        ready, release, attempting, finished = Event(), Event(), Event(), Event()
        responses, failures = [], []
        connect = self.sessions._connect
        def parked_connect():
            db = connect()
            db.set_trace_callback(lambda sql: attempting.set() if sql == 'BEGIN IMMEDIATE' else None)
            ready.set()
            if not release.wait(5):
                db.close()
                raise AssertionError('synthetic writer barrier timed out')
            return db
        def worker():
            try:
                responses.append(self.request(cookies=cookies))
            except BaseException as error:
                failures.append(error)
            finally:
                finished.set()
        with patch.object(self.sessions, '_connect', side_effect=parked_connect):
            thread = Thread(target=worker, daemon=True)
            thread.start()
            writer = sqlite3.connect(self.policy._path, isolation_level=None, timeout=5)
            try:
                self.assertTrue(ready.wait(5))
                writer.execute('BEGIN IMMEDIATE')
                release.set()
                self.assertTrue(attempting.wait(5))
                self.assertFalse(finished.is_set())
                writer.execute("UPDATE memberships SET state='revoked',revision=revision+1 WHERE workspace_id='alpha' AND principal_id='reader'")
                writer.commit()
                thread.join(6)
                self.assertFalse(thread.is_alive())
            finally:
                release.set()
                writer.rollback()
                writer.close()
                thread.join(6)
        self.assertEqual(failures, [])
        self.assertEqual(len(responses), 1)
        self.rejected(responses[0], 404, 'not_found')
