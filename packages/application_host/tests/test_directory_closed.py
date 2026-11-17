"""Unconfigured directories still finish real Cookie/Bearer authentication.

Only disposable synthetic stores and identities are used. The hooks below wrap
the actual initial/final current-principal checks; the route/module/W05 gates
are never replaced. No receipt handle is supplied, even when its schema exists.
"""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from packages.application_host import ApplicationHost
from packages.application_host.tests import fixtures
from packages.application_ui.tests.test_application import request
from packages.directory_cursors import initialize_directory_cursors
from packages.directory_cursors.tests.support import Clock, NOW, initialize
from packages.identity import AuthenticationUnavailable
from packages.session_api.application import _SESSION
from packages.session_api.tests.test_application import cookie_header
from packages.workspace_api.tests.test_application import call
from packages.workspace_api.tests.test_directory_integration import adapters


NAMES = ('pipelines', 'artifacts', 'artifact-admin')
EXPECTED = {
    'available': (503, 'scope_directory_unavailable'),
    'forbidden': (403, 'forbidden'),
    'missing': (404, 'not_found'),
    'nonmember': (404, 'not_found'),
    'disabled': (409, 'module_unavailable'),
    'hint': (400, 'invalid_request'),
}


def forbidden(*args, **kwargs):
    raise AssertionError('unconfigured directory must not inspect or write receipt/business state')


class UnconfiguredDirectoryFinalGuardTests(unittest.TestCase):
    def build(self, directory, boundary, namespace, *, schema):
        clock = Clock()
        path = Path(directory) / 'policy.sqlite'
        if boundary == 'cookie':
            # The host fixture uses January 2; the Bearer fixture uses January 1.
            clock.now = fixtures.NOW
            original, policy, login, exchange = fixtures.setup(
                directory, clock=clock, directory_cursors=False)
            sessions = original._boundary._sessions
            app = ApplicationHost(
                config=replace(original._config, artifact_administration=True),
                sessions=sessions, clock=clock, exchange=exchange,
                authorization_endpoint=fixtures.ISSUER + '/authorize',
                authorization_scopes=('synthetic_user',), directory_cursors=None)
            session = fixtures.session(login, exchange)
            cookies = cookie_header({_SESSION: session.token.value})
            case = SimpleNamespace(app=app, policy=policy, path=path, clock=clock,
                owner=sessions, hook='_current_actor', expiry=session.expires_at,
                cookies=cookies, login_path=Path(directory) / 'login.sqlite')
        else:
            policy = initialize(path)
            # No artifact indexes or receipt schema are provisioned here. None
            # is needed to check the real current route/module authority gate.
            app = adapters(policy, clock, enabled=False)[NAMES.index(namespace)]
            owner = app._service if namespace == 'artifact-admin' else app
            case = SimpleNamespace(app=app, policy=policy, path=path, clock=clock,
                owner=owner, hook='_current_bearer_actor', expiry=NOW + timedelta(hours=1))
        if schema:
            initialize_directory_cursors(policy, layout='explicit-factory-v1')
        return case

    @staticmethod
    def database_snapshot(path):
        """Read-only comparison including schema, business and auxiliary rows."""
        with sqlite3.connect(path) as db:
            schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
            rows = {}
            for kind, name, _, _ in schema:
                if kind == 'table':
                    quoted = '"' + name.replace('"', '""') + '"'
                    rows[name] = sorted(db.execute('SELECT * FROM ' + quoted).fetchall(), key=repr)
            return schema, rows

    @staticmethod
    def login_clock(path):
        with sqlite3.connect(path) as db:
            return db.execute('SELECT clock FROM metadata WHERE id=1').fetchone()[0]

    def exercise(self, boundary, namespace, condition, outcome, *, schema=False):
        with tempfile.TemporaryDirectory() as directory:
            case = self.build(directory, boundary, namespace, schema=schema)
            module = 'pipelines' if namespace == 'pipelines' else 'artifacts'
            with sqlite3.connect(case.path) as db:
                if condition == 'forbidden':
                    db.execute("UPDATE modules SET can_read=0,can_write=0 WHERE workspace_id='alpha' AND key=?", (module,))
                elif condition == 'disabled':
                    db.execute("UPDATE modules SET state='disabled',revision=revision+1 WHERE workspace_id='alpha' AND key=?", (module,))
                elif condition == 'nonmember':
                    db.execute("UPDATE memberships SET state='revoked',revision=revision+1 WHERE workspace_id='alpha' AND principal_id='admin'")
            before = self.database_snapshot(case.path)
            receipt_tables = {name for kind, name, _, _ in before[0]
                              if kind == 'table' and name.startswith('directory_cursor_')}
            self.assertEqual(len(receipt_tables), 3 if schema else 0)
            if schema:
                self.assertEqual(before[1]['directory_cursor_receipts_v1'], [])
                self.assertEqual(before[1]['directory_cursor_partitions_v1'], [])
            prior_clock = self.login_clock(case.login_path) if boundary == 'cookie' else None

            current = getattr(case.owner, case.hook)
            current_calls = []
            def observe_current(*args, **kwargs):
                current_calls.append(case.clock.now)
                # Real credential/principal validation, including the final
                # expiry check. Only time and the injected final fault differ.
                actor = current(*args, **kwargs)
                if len(current_calls) == 1:
                    case.clock.now = (case.expiry if outcome == 'expired'
                                      else case.clock.now + timedelta(seconds=1))
                elif outcome == 'unavailable':
                    raise AuthenticationUnavailable()
                return actor

            trace = []
            connect = case.policy._connect
            def traced_connect():
                db = connect()
                db.set_trace_callback(trace.append)
                return db

            # No successful fake projection can make these tests pass. These
            # traps must remain untouched throughout the actual HTTP dispatch.
            traps = (
                'packages.directory_cursors.transaction._ReceiptContext',
                'packages.directory_cursors.transaction.check_schema',
                'packages.directory_cursors.store.check_schema',
                'packages.directory_cursors.directory.after_query',
                'packages.pipeline_api.application.directory',
                'packages.artifact_sync.application.directory',
                'packages.artifact_admin.application.directory',
            )
            workspace = 'missing' if condition == 'missing' else 'alpha'
            path = f'/api/workspaces/{workspace}/{namespace}/scopes'
            extra = {'HTTP_X_WORKSPACE_ID': 'beta'} if condition == 'hint' else None
            with ExitStack() as stack:
                stack.enter_context(patch.object(case.policy, '_connect', side_effect=traced_connect))
                checked = stack.enter_context(patch.object(case.owner, case.hook, side_effect=observe_current))
                untouched = [stack.enter_context(patch(name, side_effect=forbidden)) for name in traps]
                untouched.append(stack.enter_context(patch.object(case.policy, '_persist', side_effect=forbidden)))
                if boundary == 'cookie':
                    result = request(case.app, path + '?after=%XX', cookies=case.cookies, extra=extra)
                else:
                    result = call(case.app, path, token='admin', query='after=%XX', extra=extra)
                self.assertEqual(checked.call_count, 2)
                for guard in untouched:
                    guard.assert_not_called()

            status, error = (EXPECTED[condition] if outcome == 'valid' else
                             (401, 'authentication_required') if outcome == 'expired' else
                             (503, 'authentication_unavailable'))
            self.assertEqual((result['status'], result['body']), (status, {'error': error}))
            self.assertEqual(dict(result['headers'])['Cache-Control'], 'no-store')
            self.assertEqual(len(current_calls), 2)
            self.assertGreater(current_calls[1], current_calls[0])
            self.assertEqual(current_calls[1], case.clock.now)
            self.assertEqual(self.database_snapshot(case.path), before)

            # Trace only the request, not fixture initialization or comparisons.
            # The existing global policy loader's PRAGMA foreign_key_check is
            # unchanged and may internally consult auxiliary tables. Assert no
            # dedicated auxiliary SELECT/schema query/DDL/DML, not no SQLite
            # internal access while validating that global policy snapshot.
            self.assertIn('BEGIN IMMEDIATE', trace)
            self.assertFalse(any('directory_cursor_' in statement.lower() for statement in trace), trace)
            self.assertFalse(any('savepoint business' in statement.lower() for statement in trace), trace)
            writes = [statement for statement in trace
                      if re.match(r'\s*(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', statement, re.I)]
            if boundary == 'cookie':
                self.assertEqual(len(writes), 1, writes)
                self.assertTrue(writes[0].startswith('UPDATE login.metadata SET clock='), writes)
                expected_clock = ((case.clock.now - datetime(1970, 1, 1, tzinfo=timezone.utc))
                                  // timedelta(microseconds=1))
                self.assertGreater(expected_clock, prior_clock)
                self.assertEqual(self.login_clock(case.login_path), expected_clock)
            else:
                self.assertEqual(writes, [])

    def matrix(self, boundary, outcome, *, schema=False, conditions=None):
        for namespace in NAMES:
            for condition in EXPECTED if conditions is None else conditions:
                with self.subTest(boundary=boundary, namespace=namespace,
                                  condition=condition, outcome=outcome, schema=schema):
                    self.exercise(boundary, namespace, condition, outcome, schema=schema)

    def test_cookie_valid_current_preserves_gate_w05_and_unconfigured_errors(self):
        self.matrix('cookie', 'valid')

    def test_cookie_final_real_expiry_overrides_gate_w05_and_unconfigured_errors(self):
        self.matrix('cookie', 'expired')

    def test_cookie_final_unavailable_overrides_gate_w05_and_unconfigured_errors(self):
        self.matrix('cookie', 'unavailable')

    def test_bearer_valid_current_preserves_gate_w05_and_unconfigured_errors(self):
        self.matrix('bearer', 'valid')

    def test_bearer_final_real_expiry_overrides_gate_w05_and_unconfigured_errors(self):
        self.matrix('bearer', 'expired')

    def test_bearer_final_unavailable_overrides_gate_w05_and_unconfigured_errors(self):
        self.matrix('bearer', 'unavailable')

    def test_cookie_existing_receipt_schema_has_no_dedicated_access_or_changes(self):
        for outcome in ('valid', 'expired', 'unavailable'):
            self.matrix('cookie', outcome, schema=True, conditions=('available', 'hint'))

    def test_bearer_existing_receipt_schema_has_no_dedicated_access_or_changes(self):
        for outcome in ('valid', 'expired', 'unavailable'):
            self.matrix('bearer', outcome, schema=True, conditions=('available', 'hint'))
