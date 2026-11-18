# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Real I1/I2 factory and restored routing, with only private synthetic sessions.

No profile converter, provider activation, production enrollment or duplicate A
codec suite. Existing runtime and historical-storage tests remain unchanged.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.login_state import SQLiteLoginStore
from packages.session_api import SessionPolicyStore
from packages.service_runtime import (
    RuntimeFailure, backup, create_application, initialize, load_config, restore)
from packages.service_runtime import runtime, schema
from packages.service_runtime.tests import test_server
from packages.workspaces import PolicyError, SQLiteWorkspaceService
from packages.workspace_admin_api.transfer_store import initialize_transfers
from packages.workspace_admin_api.tests.transfer_fixtures import (
    BASE, TransferFixture, call, initialize_policy, issue_session, snapshot)
from packages.session_api.application import _SESSION, _CSRF


class RuntimeTransferProfileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def configured(self, layout, *, suffix='source'):
        directory = self.root / (suffix + '-' + str(layout))
        path = self.root / (suffix + '-' + str(layout) + '.json')
        value = {'version': 1, 'origin': 'https://app.example.invalid',
            'data_directory': str(directory), 'modules': ['community'],
            'artifact_administration': False,
            'login': {'mode': 'disabled', 'issuer': 'https://identity.example.invalid',
                      'audience': 'synthetic-client'}}
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path, directory, value

    def provision(self, config, directory, layout):
        initialize(config, layout=layout)
        # Replace only the new empty synthetic fixture before any factory or
        # lease is open, with precisely the same registered profile schema.
        temporary = self.root / ('synthetic-' + directory.name + '.sqlite')
        initialize_policy(temporary)
        with sqlite3.connect(temporary) as db:
            for statement in schema.layout_profile(layout).module_schema: db.execute(statement)
        (directory / 'policy.sqlite').write_bytes(temporary.read_bytes())
        temporary.unlink()
        return self.credentials(config, directory)

    def credentials(self, config, directory):
        settings = load_config(config)
        policy = SQLiteWorkspaceService(directory / 'policy.sqlite')
        login = SQLiteLoginStore(directory / 'login.sqlite', binding=settings.binding)
        SessionPolicyStore(policy=policy, login=login)
        current = issue_session(login, 'admin-sub', lambda: datetime.now(timezone.utc))
        return {'HTTP_COOKIE': f'{_SESSION}={current.token.value}; {_CSRF}={current.csrf.value}'}

    def test_actual_inspection_selects_i2_only_and_factory_never_initializes(self):
        for layout in (1, 2):
            config, directory, _ = self.configured(layout)
            headers = self.provision(config, directory, layout)
            inspected = []
            original = runtime.inspect
            def observe(*args, **kwargs):
                result = original(*args, **kwargs)
                inspected.append(result)
                return result
            with patch.object(runtime, 'inspect', side_effect=observe), patch(
                    'packages.workspace_admin_api.transfer_store.initialize_transfers', side_effect=AssertionError('runtime cannot initialize')):
                app = create_application(config)
            try:
                self.assertEqual(len(inspected), 1)
                self.assertIs(inspected[0].profile, schema.layout_profile(layout))
                result = call(app, BASE + '/capabilities', extra=headers)
                self.assertEqual(result['status'], 200 if layout == 2 else 404, result)
                if layout == 2: self.assertEqual(result['body'], {'data': {'available': True}})
                self.assertEqual(call(app, '/auth/start', method='POST', body={},
                    extra={'HTTP_ORIGIN': 'https://app.example.invalid', 'HTTP_X_MS_COLLAB_LOGIN': '1'})['status'], 503)
                self.assertEqual(call(app, BASE + '/capabilities')['status'], 401)
            finally: app.close()
            before = snapshot(directory / 'policy.sqlite')
            with self.assertRaises(PolicyError):
                initialize_transfers(SQLiteWorkspaceService(directory / 'policy.sqlite'))
            self.assertEqual(snapshot(directory / 'policy.sqlite'), before)

    def test_mounted_i2_rejects_an_explicit_factory_inert_table_without_repair(self):
        ddl = 'CREATE TABLE auxiliary_notes (value TEXT)'
        # Positive control: the same table added after an explicit Cookie
        # factory has opened is admissible there.
        explicit = TransferFixture(self.root, 'cookie')
        with closing(sqlite3.connect(explicit.path)) as db:
            db.execute(ddl)
            db.commit()
        accepted = explicit.request()
        self.assertEqual((accepted['status'], accepted['body']), (200, {'data': {'available': True}}))

        config, directory, _ = self.configured(2)
        headers = self.provision(config, directory, 2)
        app = create_application(config)
        try:
            # Keep this same inspected and mounted host/session for both calls.
            mounted = call(app, BASE + '/capabilities', extra=headers)
            self.assertEqual((mounted['status'], mounted['body']), (200, {'data': {'available': True}}))
            with closing(sqlite3.connect(directory / 'policy.sqlite')) as db:
                db.execute(ddl)
                db.commit()
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            refused = call(app, BASE + '/capabilities', extra=headers)
            self.assertEqual((refused['status'], refused['body']), (503, {'error': 'admin_transfer_unavailable'}))
            # Nothing reinitialized or repaired; session/clock, policy/receipt,
            # manifest, lease file and the installed extra table are unchanged.
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
        finally:
            app.close()

    def test_b1_b2_restore_keep_routing_and_do_not_restore_login_credentials(self):
        for layout in (1, 2):
            config, directory, _ = self.configured(layout)
            old_headers = self.provision(config, directory, layout)
            checkpoint = self.root / ('backup-' + str(layout))
            backup(config, checkpoint)
            restored, destination, _ = self.configured(layout, suffix='restored')
            restore(restored, checkpoint)
            headers = self.credentials(restored, destination)
            app = create_application(restored)
            try:
                self.assertEqual(call(app, BASE + '/capabilities', extra=headers)['status'], 200 if layout == 2 else 404)
                self.assertEqual(call(app, '/api/workspaces', extra=old_headers)['status'], 401)
                self.assertEqual(call(app, '/api/workspaces', extra=headers)['status'], 200)
            finally: app.close()
            installed = json.loads((destination / 'installation.json').read_text())
            self.assertEqual(installed['format'], layout)

    def test_i2_missing_or_altered_transfer_schema_fails_factory_without_repair(self):
        for mutation in ('DROP TABLE workspace_admin_transfers_v1',
                         'CREATE TRIGGER unknown AFTER INSERT ON audit BEGIN SELECT 1; END'):
            suffix = 'missing' if mutation.startswith('DROP') else 'changed'
            config, directory, _ = self.configured(2, suffix=suffix)
            self.provision(config, directory, 2)
            with sqlite3.connect(directory / 'policy.sqlite') as db: db.execute(mutation)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            with self.assertRaises(RuntimeFailure): create_application(config)
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    @unittest.skipUnless(test_server.HAS_GUNICORN, 'install the hash-pinned service runtime requirements')
    def test_existing_raw_server_rejects_duplicate_and_underscore_selection_headers(self):
        # Compose the original real raw parser harness without inheriting and
        # rediscovering the complete old runtime suite in this new test class.
        harness = test_server.GunicornPolicyTests()
        harness.setUp()
        proof = 'ats1-' + '0' * 64
        base = 'POST ' + BASE + ' HTTP/1.1\r\nHost: localhost\r\n'
        accepted = harness.request((base + 'If-Admin-Transfer-Match: ' + proof + '\r\n\r\n').encode())
        self.assertIn(('IF-ADMIN-TRANSFER-MATCH', proof), accepted.headers)
        for headers in (
            'If-Admin-Transfer-Match: ' + proof + '\r\nif-admin-transfer-match: ' + proof + '\r\n',
            'If_Admin_Transfer_Match: ' + proof + '\r\n',
            'If-Admin-Transfer_Match: ' + proof + '\r\n'):
            harness.rejected((base + headers + '\r\n').encode())


if __name__ == '__main__': unittest.main()
