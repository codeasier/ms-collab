"""W08 exact module status through real SQLite/Bearer and direct Cookie APIs.

The protocol verifier/exchange is synthetic; current identity, membership,
expiry, transaction and HTTP routing checks are the real implementations.
No provider acquisition, production identity transition or browser is used.
"""
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
import re
import sqlite3
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from packages.directory_cursors import initialize_directory_cursors
from packages.identity import Provider
from packages.session_api.tests import test_application as cookie_fixture
from packages.workspace_admin_api import ModuleTemplate, WorkspaceAdministrationApplication
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.tests import test_application as bearer_fixture
from packages.workspaces import (
    Membership, ModuleInstance, Principal, Resource, SQLiteWorkspaceService, StorageError,
    Workspace, WorkspaceService,
)
from packages.workspaces.tests.identity_fixture import simulate_identity


STATUS = '/api/workspaces/alpha/modules/board'
FIELDS = {'workspace_id', 'key', 'state', 'revision'}
SPACES = ('alpha', 'beta', 'commons')
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
RECEIPT_TRAPS = (
    'packages.directory_cursors.transaction.run',
    'packages.directory_cursors.transaction._ReceiptContext',
    'packages.directory_cursors.transaction.check_schema',
    'packages.directory_cursors.store.check_schema',
)


def forbidden(*args, **kwargs):
    raise AssertionError('status must not persist business state or enter receipt machinery')


def database_snapshot(path):
    """Schema and every table, including auxiliary records, not just row counts."""
    with sqlite3.connect(path) as db:
        schema = db.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        rows = {}
        for kind, name, _, _ in schema:
            if kind == 'table':
                quoted = '"' + name.replace('"', '""') + '"'
                rows[name] = sorted(db.execute('SELECT * FROM ' + quoted).fetchall(), key=repr)
        return schema, rows


def initialize(path):
    """One member has viewer/developer/admin roles in three independent spaces."""
    return SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p, bearer_fixture.ISSUER, p + '-sub', p != 'disabled')
                         for p in ('admin', 'viewer', 'other', 'disabled', 'outsider')),
        workspaces=tuple(Workspace(w, w, 'shared' if w == 'commons' else 'product') for w in SPACES),
        memberships=(Membership('alpha', 'admin', 'admin'),
                     *(Membership(w, 'other', 'admin') for w in SPACES),
                     *(Membership(w, 'viewer', role) for w, role in
                       zip(SPACES, ('viewer', 'developer', 'admin')))),
        modules=tuple(ModuleInstance(w, key, capabilities)
                      for w in SPACES for key, capabilities in
                      (('board', ('read', 'write')), ('empty', ()))),
        resources=tuple(Resource(w, key, 'same', 'Private synthetic ' + w)
                        for w in SPACES for key in ('board', 'empty')),
        operators=frozenset({'admin'}))


class BearerModuleStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'policy.sqlite'
        self.policy = initialize(self.path)
        self.now = bearer_fixture.NOW
        self.verifier = bearer_fixture.SyntheticVerifier()
        self.app = WorkspaceApplication(store=self.policy,
            providers=(Provider('test', bearer_fixture.ISSUER, 'test-api', self.verifier),),
            provider_key='test', clock=lambda: self.now)

    def request(self, path=STATUS, **kwargs):
        return bearer_fixture.call(self.app, path, **kwargs)

    def rejected(self, result, status, code):
        self.assertEqual((result['status'], result['body']), (status, {'error': code}), result)
        self.assertEqual(result['headers']['Cache-Control'], 'no-store')
        self.assertEqual(result['headers']['Vary'], 'Authorization')

    def projected(self, result, workspace='alpha', key='board', state='enabled', revision=1):
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(set(result['body']), {'data'})
        data = result['body']['data']
        self.assertEqual(set(data), FIELDS)
        self.assertEqual(data, dict(workspace_id=workspace, key=key, state=state, revision=revision))
        self.assertIs(type(data['revision']), int)
        self.assertEqual(result['headers']['Cache-Control'], 'no-store')

    def test_exact_projection_in_three_spaces_with_different_local_roles(self):
        for workspace in SPACES:
            with self.subTest(workspace=workspace):
                self.projected(self.request(STATUS.replace('/alpha/', '/' + workspace + '/'),
                                            token='test-viewer'), workspace)
        self.assertEqual(self.policy.audit_events('other', 'alpha'), ())

    def test_lifecycle_status_never_enables_disabled_resource_list_read_or_write(self):
        for state, revision in (('enabled', 1), ('disabling', 2), ('disabled', 3)):
            if revision > 1:
                for workspace in SPACES:
                    self.policy.set_module_state('other', workspace, 'board',
                                                 state=state, expected_revision=revision - 1)
            for workspace in SPACES:
                path = STATUS.replace('/alpha/', '/' + workspace + '/')
                self.projected(self.request(path, token='test-viewer'), workspace, state=state,
                               revision=revision)
                for token in ('test-viewer', 'test-other'):
                    for suffix, method in (('/resources', 'GET'), ('/resources/same', 'GET'),
                                           ('/resources/same', 'PUT')):
                        if state == 'enabled' and method == 'PUT' and (token, workspace) != ('test-viewer', 'alpha'):
                            continue  # No successful business writes are needed to observe status.
                        result = self.request(path + suffix, token=token, method=method,
                                              body={'text': 'must not bypass'} if method == 'PUT' else None,
                                              revision='"1"' if method == 'PUT' else None)
                        if state != 'enabled':
                            self.rejected(result, 409, 'module_unavailable')
                        elif method == 'GET':
                            self.assertEqual(result['status'], 200)
                        elif token == 'test-viewer' and workspace == 'alpha':
                            self.rejected(result, 403, 'forbidden')

    def test_empty_configured_capabilities_still_have_status_but_no_resources(self):
        for workspace in SPACES:
            path = STATUS.replace('/alpha/', '/' + workspace + '/').replace('/board', '/empty')
            for token in ('test-viewer', 'test-other'):
                self.projected(self.request(path, token=token), workspace, 'empty')
                for suffix in ('/resources', '/resources/same'):
                    self.rejected(self.request(path + suffix, token=token), 403, 'forbidden')
                self.rejected(self.request(path + '/resources/same', token=token, method='PUT',
                    body={'text': 'no grant'}, revision='"1"'), 403, 'forbidden')

    def test_authentication_membership_unknown_and_suspension_precedence(self):
        for token in (None, 'forged', 'test-disabled'):
            self.rejected(self.request(token=token), 401, 'authentication_required')
        for token, workspace in (('test-outsider', 'alpha'), ('test-admin', 'beta'),
                                 ('test-admin', 'commons'), ('test-admin', 'missing')):
            for module in ('board', 'missing'):
                self.rejected(self.request(f'/api/workspaces/{workspace}/modules/{module}',
                                           token=token), 404, 'not_found')
        self.rejected(self.request(STATUS.replace('/board', '/missing'), token='test-viewer'),
                      404, 'not_found')
        self.policy.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
        for module in ('board', 'missing'):
            self.rejected(self.request(STATUS.replace('/board', '/' + module), token='test-viewer'),
                          409, 'workspace_unavailable')
            self.rejected(self.request(STATUS.replace('/board', '/' + module), token='test-outsider'),
                          404, 'not_found')
        self.rejected(self.request(token='test-viewer', extra={'HTTP_X_WORKSPACE_ID': 'beta'}),
                      400, 'invalid_request')  # Existing W05 checks membership, not module/workspace state.

    def test_archive_keeps_status_and_existing_read_only_resource_rules(self):
        self.policy.set_workspace_state('admin', 'alpha', state='archived', expected_revision=1)
        self.projected(self.request(token='test-viewer'))
        self.assertEqual(self.request(STATUS + '/resources/same', token='test-viewer')['status'], 200)
        self.rejected(self.request(STATUS + '/resources/same', method='PUT',
            body={'text': 'archive is not writable'}, revision='"1"'), 409, 'workspace_unavailable')
        self.rejected(self.request(STATUS.replace('/board', '/missing')), 404, 'not_found')

    def test_exact_shape_query_head_extra_segments_and_hint_is_not_a_route(self):
        for query in ('x=1', 'state=enabled', 'after=%XX', 'workspace_id=beta', 'offset=0', 'limit=1'):
            self.rejected(self.request(query=query), 400, 'invalid_request')
        for method in ('HEAD', 'POST', 'DELETE', 'OPTIONS'):
            self.rejected(self.request(method=method), 404, 'not_found')
        for path in (STATUS + '/', STATUS + '/extra', STATUS.replace('/board', '/%62oard'),
                     STATUS.replace('/alpha/', '//'), STATUS.replace('/board', '/Board')):
            self.rejected(self.request(path), 404, 'not_found')
        self.projected(self.request(extra={'HTTP_X_WORKSPACE_ID': 'alpha'}))
        for hint in ('beta', '', ' alpha', 'alpha ', 'ALPHA', 'alpha,beta', None, ['alpha']):
            self.rejected(self.request(extra={'HTTP_X_WORKSPACE_ID': hint}), 400, 'invalid_request')
        self.rejected(self.request(token='test-outsider', extra={'HTTP_X_WORKSPACE_ID': 'beta'}),
                      404, 'not_found')
        self.projected(self.request())  # No previous request's pending rejection leaks.

    def test_admin_wrapper_verifies_once_and_preserves_list_catalog_patch_permissions(self):
        admin = WorkspaceAdministrationApplication(api=self.app, module_catalog=(ModuleTemplate('board', ('read', 'write')),))
        with patch.object(self.verifier, 'verify', wraps=self.verifier.verify) as protocol:
            self.projected(bearer_fixture.call(admin, STATUS, token='test-viewer'))
            self.assertEqual(protocol.call_count, 1)
        for workspace in ('alpha', 'beta'):
            for suffix in ('modules', 'module-catalog'):
                path = f'/api/workspaces/{workspace}/{suffix}'
                self.rejected(bearer_fixture.call(admin, path, token='test-viewer'), 403, 'forbidden')
                self.assertEqual(bearer_fixture.call(admin, path, token='test-other')['status'], 200)
            path = f'/api/workspaces/{workspace}/modules/board'
            self.rejected(bearer_fixture.call(admin, path, token='test-viewer', method='PATCH',
                body={'state': 'disabling'}, revision='"1"'), 403, 'forbidden')
        changed = bearer_fixture.call(admin, STATUS, token='test-other', method='PATCH',
                                     body={'state': 'disabling'}, revision='"1"')
        self.assertEqual(changed['status'], 200, changed)
        self.assertIn('capabilities', changed['body']['data'])  # Existing admin projection unchanged.
        self.projected(bearer_fixture.call(admin, STATUS, token='test-viewer'), state='disabling', revision=2)
        self.rejected(bearer_fixture.call(admin, STATUS, method='PATCH',
            body={'state': 'disabled'}, revision='"1"'), 412, 'stale_revision')

    def test_previous_status_is_not_authority_after_membership_revocation(self):
        self.projected(self.request(token='test-viewer'))
        self.policy.set_membership('admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=1)
        for suffix in ('', '/resources', '/resources/same'):
            self.rejected(self.request(STATUS + suffix, token='test-viewer'), 404, 'not_found')

    def test_disabled_and_rebound_identity_fail_after_protocol_verification(self):
        for statement in ("UPDATE principals SET active=0 WHERE id='viewer'",
                          "UPDATE principals SET subject='other-binding' WHERE id='viewer'"):
            with self.subTest(statement=statement), tempfile.TemporaryDirectory() as directory:
                policy = initialize(Path(directory) / 'policy.sqlite')
                verifier = bearer_fixture.SyntheticVerifier()
                def transition():
                    with sqlite3.connect(policy._path) as db:
                        simulate_identity(db, statement)
                verifier.hook = transition
                app = WorkspaceApplication(store=policy,
                    providers=(Provider('test', bearer_fixture.ISSUER, 'test-api', verifier),),
                    provider_key='test', clock=lambda: bearer_fixture.NOW)
                self.rejected(bearer_fixture.call(app, STATUS, token='test-viewer'), 401, 'authentication_required')

    def test_identity_aba_does_not_restore_historical_membership(self):
        self.projected(self.request(token='test-viewer'))
        with sqlite3.connect(self.path) as db:
            before = db.execute("SELECT generation FROM principals WHERE id='viewer'").fetchone()[0]
            for active in (0, 1):
                simulate_identity(db, "UPDATE principals SET active=? WHERE id='viewer'", (active,))
            self.assertEqual(db.execute("SELECT generation FROM principals WHERE id='viewer'").fetchone()[0], before + 2)
        # Same subject/token now authenticates, but the old core grant is stale.
        self.rejected(self.request(token='test-viewer'), 404, 'not_found')
        self.rejected(self.request(STATUS + '/resources/same', token='test-viewer'), 404, 'not_found')
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE principals SET active=0 WHERE id='viewer'")

    def final_matrix(self, outcome, *, schema=False):
        for condition, expected in GATES.items():
            with self.subTest(condition=condition, outcome=outcome, schema=schema), tempfile.TemporaryDirectory() as directory:
                policy = initialize(Path(directory) / 'policy.sqlite')
                if schema:
                    initialize_directory_cursors(policy, layout='explicit-factory-v1')
                now = [bearer_fixture.NOW]
                verifier = bearer_fixture.SyntheticVerifier()
                app = WorkspaceApplication(store=policy,
                    providers=(Provider('test', bearer_fixture.ISSUER, 'test-api', verifier),),
                    provider_key='test', clock=lambda: now[0])
                if condition in ('suspended', 'archived'):
                    policy.set_workspace_state('admin', 'alpha', state=condition, expected_revision=1)
                if condition in ('disabling', 'disabled'):
                    policy.set_module_state('admin', 'alpha', 'board', state='disabling', expected_revision=1)
                    if condition == 'disabled':
                        policy.set_module_state('admin', 'alpha', 'board', state='disabled', expected_revision=2)
                if condition == 'revoked':
                    policy.set_membership('admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=1)
                path = (STATUS.replace('/alpha/', '/missing/') if condition == 'missing_workspace' else
                        STATUS.replace('/board', '/missing') if condition == 'missing_module' else
                        STATUS.replace('/board', '/empty') if condition == 'empty' else STATUS)
                token = 'test-outsider' if condition == 'nonmember' else 'test-viewer'
                extra = {'HTTP_X_WORKSPACE_ID': 'beta'} if condition == 'hint' else None
                before = database_snapshot(policy._path)
                trace, current_calls, final_times = [], [], []
                current = app._current_bearer_actor
                validate = app._verifier.validate_current
                def observe_current(*args):
                    actor = current(*args)
                    current_calls.append(now[0])
                    now[0] += timedelta(minutes=1) if outcome == 'expired' else timedelta(seconds=1)
                    return actor
                def observe_validation(*args):
                    # First two calls are protocol validation and initial current.
                    if current_calls:
                        final_times.append(now[0])
                        if outcome == 'unavailable':
                            # The actual validator maps clock failures to 503.
                            with patch.object(app._verifier, '_clock', side_effect=RuntimeError('synthetic clock unavailable')):
                                return validate(*args)
                    return validate(*args)
                connect = policy._connect
                def traced_connect():
                    db = connect()
                    db.set_trace_callback(trace.append)
                    return db
                getter = WorkspaceService.get_module_status
                with ExitStack() as stack:
                    stack.enter_context(patch.object(policy, '_connect', side_effect=traced_connect))
                    stack.enter_context(patch.object(app, '_current_bearer_actor', side_effect=observe_current))
                    checked = stack.enter_context(patch.object(app._verifier, 'validate_current', side_effect=observe_validation))
                    business = stack.enter_context(patch.object(WorkspaceService, 'get_module_status', autospec=True, side_effect=getter))
                    traps = [stack.enter_context(patch(name, side_effect=forbidden)) for name in RECEIPT_TRAPS]
                    traps.append(stack.enter_context(patch.object(policy, '_persist', side_effect=forbidden)))
                    result = bearer_fixture.call(app, path, token=token, extra=extra)
                    self.assertEqual(checked.call_count, 4 if outcome == 'valid' else 3)
                    self.assertEqual(business.call_count, 0 if condition == 'hint' else 1)
                    for guard in traps:
                        guard.assert_not_called()
                self.assertEqual(len(current_calls), 1)
                self.assertEqual(len(verifier.calls), 1)  # Only the initial protocol verification.
                self.assertEqual(len(final_times), 2 if outcome == 'valid' else 1)
                self.assertTrue(all(value > current_calls[0] for value in final_times))
                status, code = (expected if outcome == 'valid' else
                                (401, 'authentication_required') if outcome == 'expired' else
                                (503, 'authentication_unavailable'))
                if status == 200:
                    self.assertEqual(result['status'], 200, result)
                    self.assertEqual(set(result['body']['data']), FIELDS)
                else:
                    self.rejected(result, status, code)
                self.assertEqual(database_snapshot(policy._path), before)
                self.assertIn('BEGIN', trace)  # Ordinary read, not a receipt-only writer transaction.
                self.assertNotIn('BEGIN IMMEDIATE', trace)
                self.assertFalse(any('directory_cursor_' in sql.lower() for sql in trace), trace)
                self.assertFalse(any(re.match(r'\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', sql, re.I)
                                     for sql in trace), trace)

    def test_real_final_validation_preserves_success_gate_and_w05_results(self):
        self.final_matrix('valid')

    def test_real_final_expiry_overrides_success_gate_and_w05_results(self):
        self.final_matrix('expired')

    def test_real_final_clock_failure_overrides_success_gate_and_w05_results(self):
        self.final_matrix('unavailable')

    def test_existing_receipt_schema_is_not_read_created_or_mutated(self):
        for outcome in ('valid', 'expired', 'unavailable'):
            self.final_matrix(outcome, schema=True)

    def test_storage_and_fatal_errors_are_not_captured_as_pending_policy_denials(self):
        getter = WorkspaceService.get_module_status
        before = database_snapshot(self.path)
        for failure in (StorageError('synthetic_private_storage_detail'), KeyboardInterrupt('synthetic fatal')):
            with self.subTest(error=type(failure).__name__):
                def fail_after_projection(policy, *args):
                    getter(policy, *args)
                    raise failure
                with patch.object(WorkspaceService, 'get_module_status', autospec=True,
                                  side_effect=fail_after_projection) as business, \
                        patch.object(self.app._verifier, 'validate_current',
                                     wraps=self.app._verifier.validate_current) as checked:
                    if isinstance(failure, StorageError):
                        self.rejected(self.request(), 503, 'storage_unavailable')
                    else:
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            self.request()
                        self.assertIs(caught.exception, failure)
                    self.assertEqual(business.call_count, 1)
                    self.assertEqual(checked.call_count, 2)  # Protocol + initial; no deferred-policy final path.
                self.assertEqual(database_snapshot(self.path), before)
                self.projected(self.request())

    def test_exclusive_writer_blocks_first_read_then_revocation_is_observed(self):
        entered, release, reading, finished = Event(), Event(), Event(), Event()
        responses, failures = [], []
        load = self.policy._load
        class FirstRead:
            """Observe the actual first execute, not a claim that BEGIN reads."""
            def __init__(self, db):
                self.db = db
            def execute(self, statement, *args):
                reading.set()
                return self.db.execute(statement, *args)
            def __getattr__(self, name):
                return getattr(self.db, name)
        def awaiting_load(db):
            entered.set()
            if not release.wait(5):
                raise AssertionError('synthetic first-read barrier timed out')
            return load(FirstRead(db))
        def worker():
            try:
                responses.append(self.request(token='test-viewer'))
            except BaseException as error:
                failures.append(error)
            finally:
                finished.set()
        # A RESERVED writer would not block an ordinary deferred Bearer read.
        # Prepare the reader connection before taking a real EXCLUSIVE lock,
        # and park it just before _load's first SQLite read, not on a sleep.
        with patch.object(self.policy, '_load', side_effect=awaiting_load):
            thread = Thread(target=worker, daemon=True)
            thread.start()
            writer = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            try:
                self.assertTrue(entered.wait(5))
                writer.execute('BEGIN EXCLUSIVE')
                release.set()
                self.assertTrue(reading.wait(5))
                self.assertFalse(finished.is_set())
                writer.execute("UPDATE memberships SET state='revoked',revision=revision+1 WHERE workspace_id='alpha' AND principal_id='viewer'")
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


class DirectCookieModuleStatusTests(unittest.TestCase):
    # Import fixture module, not its TestCase, so discover does not repeat it.
    setUp = cookie_fixture.SessionHTTPTests.setUp
    make_session = cookie_fixture.SessionHTTPTests.make_session

    def test_base_cookie_application_inherits_exact_status_without_admin_wrapper(self):
        viewer = self.make_session('viewer-sub')
        cookies = cookie_fixture.cookie_header({cookie_fixture._SESSION: viewer.token.value})
        for state, revision in (('enabled', 1), ('disabling', 2), ('disabled', 3)):
            if revision > 1:
                self.policy.set_module_state('admin', 'alpha', 'board', state=state, expected_revision=revision - 1)
            result = cookie_fixture.call(self.app, STATUS, cookies=cookies)
            self.assertEqual((result['status'], result['body']), (200, {'data': {
                'workspace_id': 'alpha', 'key': 'board', 'state': state, 'revision': revision}}))
            self.assertEqual(dict(result['headers'])['Vary'], 'Cookie, Origin')
            if revision > 1:
                self.assertEqual(cookie_fixture.call(self.app, STATUS + '/resources/same', cookies=cookies)['body'],
                                 {'error': 'module_unavailable'})

    def test_base_cookie_w05_rejection_still_runs_real_final_expiry(self):
        current = self.sessions._current_actor
        calls = []
        def expire(*args):
            calls.append(args[-1])
            actor = current(*args)
            if len(calls) == 1:
                self.clock.now = self.session.expires_at
            return actor
        with patch.object(self.sessions, '_current_actor', side_effect=expire), \
                patch.object(WorkspaceService, 'get_module_status', side_effect=forbidden) as business:
            result = cookie_fixture.call(self.app, STATUS, cookies=self.cookies,
                                         extra={'HTTP_X_WORKSPACE_ID': 'beta'})
            business.assert_not_called()
        self.assertEqual((result['status'], result['body']), (401, {'error': 'authentication_required'}))
        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1], calls[0])
