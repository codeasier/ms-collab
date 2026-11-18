# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Online handle/admission/guard tests. SQL and VM checks run only in CI.

These tests intentionally do not re-test the frozen A historical codec. Actual
SQLite rollback is retained throughout fault injection; no mock claims rollback.
"""
from contextlib import closing, ExitStack
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from packages.directory_cursors.transaction import _DirectoryUnavailable
from packages.identity import AuthenticationUnavailable
from packages.session_api import SessionRequest
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError, WorkspaceService
from packages.workspace_admin_api import WorkspaceAdministrationApplication, transfer_records, transfer_store
from packages.workspace_admin_api.transfer_store import QUOTA_SQL, TransferReceiptStore, initialize_transfers
from packages.workspace_admin_api.tests.transfer_fixtures import (
    BASE, NOW, TABLE, ConnectionProxy, TransferFixture, initialize_policy, key, snapshot)
from packages.service_runtime.tests.layout_profile_fixture import append_transfer


class StorageTests(unittest.TestCase):
    def fresh(self, mode='bearer', **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return TransferFixture(directory.name, mode, **kwargs)

    def review(self, f):
        result = f.review()
        self.assertEqual(result['status'], 200, result)
        return result['body']['data']

    def test_initializer_is_explicit_repeatable_and_open_never_creates(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'policy.sqlite'
        policy = initialize_policy(path)
        before = snapshot(path)
        with self.assertRaises((PolicyError, StorageError)):
            TransferReceiptStore(policy)
        self.assertEqual(snapshot(path), before)
        for layout in (None, 1, 2, True, 'runtime', ''):
            with self.assertRaises(ValueError): initialize_transfers(policy, layout=layout)
        handle = initialize_transfers(policy)
        self.assertIs(type(handle), TransferReceiptStore)
        created = snapshot(path)
        initialize_transfers(policy)
        self.assertEqual(snapshot(path), created)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone(), (0,))
            self.assertEqual(db.execute('SELECT epoch FROM metadata').fetchone(), (1,))

    def test_exact_handle_and_same_configured_policy_required(self):
        f = self.fresh()
        for invalid in (True, object(), str(f.path), lambda db: None):
            with self.assertRaises(ValueError): WorkspaceAdministrationApplication(api=f.base, transfers=invalid)
        other = self.fresh()
        with self.assertRaises(ValueError):
            WorkspaceAdministrationApplication(api=f.base, transfers=other.handle)
        for invalid in (None, f.path, object()):
            with self.assertRaises(ValueError): TransferReceiptStore(invalid)

    def test_default_factory_unmounted_and_no_http_initializer(self):
        for mode in ('bearer', 'cookie'):
            f = self.fresh(mode, mounted=False)
            with sqlite3.connect(f.path) as db: db.execute('DROP TABLE ' + TABLE)
            before = snapshot(f.path)
            for path, method in ((BASE + '/capabilities', 'GET'),
                    (BASE + '/selections/member', 'GET'), (BASE, 'POST')):
                self.assertEqual(f.request(path, method=method, body={})['status'], 404)
            self.assertEqual(snapshot(f.path), before)

    def test_runtime_markers_including_broken_symlinks_refuse_initializer(self):
        for marker in ('installation.json', '.runtime.lock'):
            for symlink in (False, True):
                f = self.fresh()
                target = f.directory / marker
                if symlink: target.symlink_to(f.directory / 'absent-marker-target')
                else: target.write_text('synthetic runtime marker')
                before = snapshot(f.path)
                with self.assertRaises(PolicyError): initialize_transfers(f.policy)
                with self.assertRaises(PolicyError): TransferReceiptStore(f.policy)
                self.assertEqual(snapshot(f.path), before)

    def test_unknown_or_partial_receipt_family_is_not_repaired(self):
        for statement in ('DROP TABLE ' + TABLE,
                'CREATE TABLE workspace_admin_transfers_v0 (x TEXT)',
                'CREATE INDEX unexpected_receipt_index ON ' + TABLE + '(replacement_id)'):
            f = self.fresh()
            with sqlite3.connect(f.path) as db: db.execute(statement)
            before = snapshot(f.path)
            self.assertEqual(f.request()['status'], 503)
            self.assertEqual(snapshot(f.path), before)
            if statement.startswith('DROP'):
                # Constructor/HTTP do not create; explicit initializer may do so.
                with self.assertRaises(PolicyError): TransferReceiptStore(f.policy)
            else:
                with self.assertRaises(PolicyError): initialize_transfers(f.policy)
                self.assertEqual(snapshot(f.path), before)

    def test_mixed_case_reserved_family_is_not_adopted_or_extended(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'policy.sqlite'
        policy = initialize_policy(path)
        ddl = 'CREATE TABLE Workspace_Admin_Transfers_Legacy (value TEXT)'
        with sqlite3.connect(path) as db:
            db.execute(ddl)
        before, before_bytes = snapshot(path), path.read_bytes()
        connect, seen = policy._connect, []
        def opening():
            db = connect()
            db.set_trace_callback(seen.append)
            return db
        with patch.object(policy, '_connect', side_effect=opening), self.assertRaises(PolicyError):
            initialize_transfers(policy)
        self.assertFalse(any(sql.upper().startswith('CREATE ') for sql in seen), seen)
        self.assertEqual((snapshot(path), path.read_bytes()), (before, before_bytes))
        # Already-mounted online check rejects the sibling as well.
        f = self.fresh()
        with sqlite3.connect(f.path) as db:
            db.execute(ddl)
        before = snapshot(f.path)
        self.assertEqual(f.request()['status'], 503)
        self.assertEqual(snapshot(f.path), before)

    def test_same_bytes_replaced_inode_and_other_attached_main_fail_closed(self):
        f = self.fresh()
        before = f.path.read_bytes()
        replacement = f.directory / 'replacement.sqlite'
        replacement.write_bytes(before)
        replacement.replace(f.path)
        self.assertEqual(f.request()['status'], 503)
        self.assertEqual(f.path.read_bytes(), before)
        f = self.fresh()
        other = self.fresh()
        with closing(other.policy._connect()) as db:
            with self.assertRaises(PolicyError): f.handle._admit_schema(db)

    def test_realm_change_same_inode_is_not_recaptured(self):
        from packages.workspaces.sqlite import _SCHEMA
        f = self.fresh()
        inode = f.path.stat().st_ino
        with sqlite3.connect(f.path) as db:
            for name in ('policy_authority_monotonic', 'authority_marker_update_closed'):
                db.execute('DROP TRIGGER ' + name)
            db.execute("UPDATE metadata SET realm=? WHERE id=1", ('f' * 32,))
            db.execute("UPDATE authority_migrations SET realm=? WHERE id=1", ('f' * 32,))
            for sql in _SCHEMA:
                if sql.startswith(('CREATE TRIGGER policy_authority_monotonic ', 'CREATE TRIGGER authority_marker_update_closed ')):
                    db.execute(sql)
        before = snapshot(f.path)
        self.assertEqual(f.path.stat().st_ino, inode)
        self.assertEqual(f.request()['status'], 503)
        self.assertEqual(snapshot(f.path), before)

    def test_actual_admission_is_read_only_fixed_sql_and_same_connection(self):
        for mode in ('bearer', 'cookie'):
            f = self.fresh(mode)
            connector = f.policy._connect if mode == 'bearer' else f.sessions._connect
            with closing(connector()) as db:
                db.execute('BEGIN IMMEDIATE')
                statements = []
                db.set_trace_callback(statements.append)
                changes = db.total_changes
                self.assertIsNone(f.handle._admit_schema(db))
                self.assertEqual(db.total_changes, changes)
                db.set_trace_callback(None)
                db.rollback()
            self.assertTrue(statements)
            allowed_pragmas = {'PRAGMA DATABASE_LIST', 'PRAGMA MAIN.APPLICATION_ID',
                               'PRAGMA MAIN.USER_VERSION', 'PRAGMA LOGIN.APPLICATION_ID', 'PRAGMA LOGIN.USER_VERSION'}
            for sql in statements:
                normalized = ' '.join(sql.upper().split())
                self.assertTrue(normalized.startswith('SELECT ') or normalized in allowed_pragmas, normalized)
            self.assertTrue(any('SQLITE_SCHEMA' in sql.upper() for sql in statements))
            self.assertFalse(any('LOGIN.METADATA' in sql.upper() for sql in statements))

    def test_explicit_schema_capacity_256_and_257_bounds_whole_catalog_walk(self):
        f = self.fresh()
        with sqlite3.connect(f.path) as db:
            count = db.execute('SELECT count(*) FROM sqlite_schema').fetchone()[0]
            for number in range(256 - count):
                db.execute('CREATE TABLE auxiliary_' + str(number) + ' (value TEXT)')
            self.assertEqual(db.execute('SELECT count(*) FROM sqlite_schema').fetchone(), (256,))
        self.assertEqual(f.request()['status'], 200)
        statements = []
        with closing(f.policy._connect()) as db:
            db.execute('BEGIN')
            db.set_trace_callback(statements.append)
            f.handle._admit_bearer(db)
            db.set_trace_callback(None)
            db.rollback()
            catalog = [sql for sql in statements if 'FROM main.sqlite_schema' in sql]
            self.assertEqual(len(catalog), 1)
            sql = catalog[0]
            normalized = ' '.join(sql.upper().split())
            self.assertNotIn(' WHERE ', normalized)
            self.assertNotIn(' ORDER BY ', normalized)
            self.assertTrue(normalized.endswith('LIMIT 257'), normalized)
            plan = ' '.join(str(row[-1]).upper() for row in db.execute('EXPLAIN QUERY PLAN ' + sql))
            self.assertIn('SCAN', plan)
            self.assertNotIn('TEMP B-TREE', plan)
            def counted():
                steps = [0]
                def progress():
                    steps[0] += 1
                    return int(steps[0] > 30000)
                db.set_progress_handler(progress, 1)
                try: rows = db.execute(sql).fetchall()
                finally: db.set_progress_handler(None, 0)
                return rows, steps[0]
            rows, admitted_steps = counted()
            self.assertEqual(len(rows), 256)
            db.execute('CREATE TABLE auxiliary_beyond_limit (value TEXT)')
            rows, refused_steps = counted()
            self.assertEqual(len(rows), 257)
            self.assertEqual(f.request()['status'], 503)
            self.assertLess(refused_steps, 30000)
            self.assertLessEqual(refused_steps - admitted_steps, 150)
            for number in range(100):
                db.execute('CREATE TABLE auxiliary_after_limit_' + str(number) + ' (value TEXT)')
            rows, larger_steps = counted()
            self.assertEqual(len(rows), 257)
            self.assertLessEqual(abs(larger_steps - refused_steps), 150)
        before = snapshot(f.path)
        self.assertEqual(f.request()['status'], 503)
        self.assertEqual(snapshot(f.path), before)
        with self.assertRaises(PolicyError): initialize_transfers(f.policy)
        self.assertEqual(snapshot(f.path), before)

    def test_reviewed_optional_module_tables_and_their_exact_indexes(self):
        from packages.service_runtime._layout_v1 import MODULE_SCHEMA
        from packages.artifact_download.records import _CREATE as download, _LEASE_CREATE as leases
        from packages.artifact_refresh.service import _CREATE as artifact_refresh
        from packages.assistant_protocol.preflight import _SCHEMA as preflight
        from packages.community_refresh.service import _CREATE as community_refresh, _INDEX as refresh_index
        from packages.directory_cursors.store import SCHEMA as cursors
        from packages.workspace_connections.records import _SCHEMA as connections

        for mode in ('cookie', 'bearer'):
            with self.subTest(mode=mode):
                f = self.fresh(mode)
                with closing(f.policy._connect()) as db:
                    for ddl in (*MODULE_SCHEMA, download, leases, artifact_refresh, preflight,
                                community_refresh, refresh_index, *cursors, connections):
                        db.execute(ddl)
                    db.commit()
                self.assertIs(type(TransferReceiptStore(f.policy)), TransferReceiptStore)
                self.assertEqual(f.request()['status'], 200)
                with closing(f.policy._connect()) as db:
                    db.execute('DROP INDEX research_asset_query')
                    db.commit()
                self.assertEqual(f.request()['status'], 503)

    def test_nonreviewed_table_reference_index_or_changed_owner_fails_before_cookie_clock(self):
        # Harmless schema differences only: the FK has the default NO ACTION.
        variants = ('CREATE TABLE auxiliary_notes (value TEXT REFERENCES workspaces(id))',
                    'CREATE INDEX workspace_name_lookup ON workspaces(name)',
                    'CREATE TABLE community_connections (value TEXT)',
                    'CREATE TABLE ancillary_notes (value TEXT)')
        for ddl in variants:
            with self.subTest(ddl=ddl):
                f = self.fresh('cookie')
                with closing(f.policy._connect()) as db:
                    db.execute(ddl)
                    db.commit()
                before = snapshot(f.path), snapshot(f.login._path)
                with patch.object(f.login, '_now', side_effect=AssertionError('unadmitted storage')) as clock:
                    result = f.request()
                    self.assertEqual((result['status'], result['body']), (503, {'error': 'admin_transfer_unavailable'}))
                    clock.assert_not_called()
                with self.assertRaises(PolicyError): initialize_transfers(f.policy)
                self.assertEqual((snapshot(f.path), snapshot(f.login._path)), before)

    def test_only_plain_inert_table_metadata_is_accepted_not_virtual_or_index(self):
        # Catalog tuples are data: no virtual-table extension is loaded here.
        f = self.fresh()
        required, optional = f.handle._required, f.handle._optional
        name = 'auxiliary_notes'
        plain = ('table', name, name, 'CREATE TABLE auxiliary_notes (value TEXT)')
        rows = required | {plain}
        self.assertIsNone(transfer_store._admit_explicit(rows, required, optional))
        for row in (('table', name, name, 'CREATE VIRTUAL TABLE auxiliary_notes USING fts5(value)'),
                    ('index', 'notes_index', name, 'CREATE INDEX notes_index ON auxiliary_notes(value)')):
            with self.subTest(row=row), self.assertRaises(PolicyError):
                transfer_store._admit_explicit(required | {row}, required, optional)

    def test_ddl_comparison_preserves_literal_and_non_sql_whitespace(self):
        fmt = transfer_store._ddl
        self.assertEqual(fmt("CHECK( tag\n =   'a  b' )"), fmt("CHECK( tag = 'a  b' )"))
        self.assertNotEqual(fmt("CHECK(tag='a  b')"), fmt("CHECK(tag='a b')"))
        self.assertNotEqual(fmt("CHECK(tag='a''  b')"), fmt("CHECK(tag='a'' b')"))
        self.assertNotEqual(fmt('value\u2003TEXT'), fmt('value TEXT'))

    def test_schema_field_sentinels_reject_oversized_complete_names_and_ddl(self):
        for ddl in ('CREATE TABLE "' + 'n' * 201 + '" (value TEXT)',
                    "CREATE TABLE large_ddl (value TEXT DEFAULT '" + 'x' * 17000 + "')"):
            f = self.fresh()
            with sqlite3.connect(f.path) as db: db.execute(ddl)
            before = snapshot(f.path)
            self.assertEqual(f.request()['status'], 503)
            self.assertEqual(snapshot(f.path), before)

    def test_failed_initializer_rolls_back_new_receipt_table_for_unsafe_schema(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'policy.sqlite'
        policy = initialize_policy(path)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TRIGGER hostile AFTER UPDATE ON metadata BEGIN DELETE FROM audit; END')
        before = snapshot(path)
        with self.assertRaises(PolicyError): initialize_transfers(policy)
        self.assertEqual(snapshot(path), before)
        with sqlite3.connect(path) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (TABLE,)).fetchone())

    def test_fixed_attachment_expectations_never_adopt_unexpected_login(self):
        f = self.fresh('cookie')
        with closing(f.policy._connect()) as db:
            db.execute('BEGIN')
            with self.assertRaises(PolicyError): f.handle._admit_cookie(db)
            db.rollback()
        with closing(f.sessions._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(PolicyError): f.handle._admit_bearer(db)
            self.assertIsNone(f.handle._admit_cookie(db))
            db.rollback()
        with closing(f.policy._connect()) as db:
            db.execute("ATTACH DATABASE ':memory:' AS unexpected")
            with self.assertRaises(PolicyError): f.handle._admit_bearer(db)
        # Mounted boundary chooses its fixed method; the route cannot provide
        # the expected-attachment selector through headers or body fields.
        connect = f.sessions._connect
        def detached():
            db = connect()
            db.execute('DETACH DATABASE login')
            return db
        with patch.object(f.sessions, '_connect', side_effect=detached):
            self.assertEqual(f.request()['status'], 503)

    def test_factory_file_and_expected_schema_faults_are_value_free_not_fatal_capture(self):
        f = self.fresh()
        for factory in (TransferReceiptStore, initialize_transfers):
            for context in (patch('packages.workspace_admin_api.transfer_store.Path.resolve', side_effect=OSError('synthetic path detail')),
                            patch('packages.workspace_admin_api.transfer_store._expected', side_effect=sqlite3.OperationalError('synthetic SQL detail'))):
                with context, self.assertRaises(PolicyError) as error:
                    factory(f.policy)
                self.assertEqual((error.exception.status, error.exception.code), (503, 'admin_transfer_unavailable'))
            with patch('packages.workspace_admin_api.transfer_store._expected', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt): factory(f.policy)

    def test_old_key_at_full_partition_replays_before_quota_and_257_fails_closed(self):
        for mode in ('bearer', 'cookie'):
            f = self.fresh(mode)
            original = self.review(f)
            first = f.submit(original)
            self.assertEqual(first['status'], 200)
            f.promote_back()
            # Trusted fixture uses real core pairs and independently built A
            # history; avoids pretending 256 arbitrary rows are valid receipts.
            for number in range(2, 257):
                append_transfer(f.policy, number=number)
                f.promote_back()
            before = snapshot(f.path)
            with patch('packages.workspace_admin_api.transfer_store.QUOTA_SQL', 'INVALID QUOTA MUST NOT BE READ'):
                self.assertEqual(f.submit(original), first)
            self.assertEqual(snapshot(f.path), before)
            fresh = self.review(f)
            rejected = f.submit(fresh, request_key=key(999))
            self.assertEqual(rejected['status'], 503)
            self.assertEqual(rejected['body'], {'error': 'admin_transfer_unavailable'})
            self.assertEqual(snapshot(f.path), before)
            append_transfer(f.policy, number=257)
            f.promote_back()
            over_capacity = snapshot(f.path)
            self.assertEqual(f.submit(self.review(f), request_key=key(1000))['status'], 503)
            self.assertEqual(snapshot(f.path), over_capacity)
            with self.assertRaises(PolicyError): TransferReceiptStore(f.policy)

    def test_consumed_record_or_original_audit_corruption_never_becomes_fresh(self):
        for mode in ('bearer', 'cookie'):
            for corrupted in ('receipt', 'audit'):
                f = self.fresh(mode)
                reviewed = self.review(f)
                self.assertEqual(f.submit(reviewed)['status'], 200)
                f.promote_back()
                with sqlite3.connect(f.path) as db:
                    if corrupted == 'receipt':
                        db.execute('UPDATE ' + TABLE + ' SET intent=intent||?', ('\x00tail',))
                    else:
                        db.execute("UPDATE audit SET object_id='second' WHERE sequence=1")
                before = snapshot(f.path)
                self.assertEqual(f.submit(reviewed)['status'], 503)
                self.assertEqual(snapshot(f.path), before)

    def test_pk_and_quota_actual_query_plans_vm_work_and_blob_sentinels(self):
        f = self.fresh()
        for workspace in ('alpha', 'beta'):
            for number in range(1, 9):
                append_transfer(f.policy, workspace=workspace, number=number)
                f.promote_back(workspace=workspace)
        with closing(sqlite3.connect(f.path)) as db:
            queries = ((transfer_records.RECEIPT_SQL, ('admin', 'alpha', key(1))),
                       (QUOTA_SQL, ('admin', 'alpha')))
            for sql, args in queries:
                plan = ' '.join(str(row[-1]).upper() for row in db.execute('EXPLAIN QUERY PLAN ' + sql, args))
                self.assertIn('SEARCH', plan)
                self.assertIn('ACTOR=? AND WORKSPACE_ID=?', plan)
                self.assertNotIn('TEMP B-TREE', plan)
                steps = [0]
                def progress():
                    steps[0] += 1
                    return int(steps[0] > 10000)
                db.set_progress_handler(progress, 1)
                try: rows = db.execute(sql, args).fetchall()
                finally: db.set_progress_handler(None, 0)
                self.assertLess(steps[0], 10000)
                self.assertLessEqual(len(rows), 257)
            # Actual complete BLOB projection cannot hide a NUL suffix or pull
            # an unbounded key into Python. This corruption is test-only.
            db.execute('UPDATE ' + TABLE + ' SET request_key=? WHERE actor=? AND workspace_id=? AND request_key=?',
                       (key(1) + '\x00' + 'x' * 10000, 'admin', 'alpha', key(1)))
            projected = db.execute(QUOTA_SQL, ('admin', 'alpha')).fetchall()
            self.assertTrue(any(len(row[0]) == 37 and row[0][-1:] == b'\x00' for row in projected))
            self.assertTrue(all(type(row[0]) is bytes and len(row[0]) <= 37 for row in projected))

    def test_insert_and_every_readback_class_fault_roll_back_full_policy(self):
        for mode in ('bearer', 'cookie'):
            for stage in ('insert', 'snapshot', 'receipt', 'audit-pair', 'audit-tail',
                          'metadata', 'workspace', 'principal', 'member', 'owner'):
                f = self.fresh(mode)
                reviewed = self.review(f)
                before = snapshot(f.path)
                if stage == 'insert':
                    context = patch.object(TransferReceiptStore, '_insert', side_effect=sqlite3.OperationalError('synthetic'))
                elif stage == 'snapshot':
                    context = patch.object(TransferReceiptStore, '_snapshot', side_effect=sqlite3.OperationalError('synthetic'))
                elif stage == 'receipt':
                    original = TransferReceiptStore._read
                    calls = [0]
                    def reading(*args):
                        calls[0] += 1
                        if calls[0] == 2: raise sqlite3.OperationalError('synthetic')
                        return original(*args)
                    context = patch.object(TransferReceiptStore, '_read', side_effect=reading)
                else:
                    connect = f.policy._connect
                    def opening():
                        def before_sql(db, sql, args):
                            selected = {
                                'metadata': transfer_store._METADATA_SQL,
                                'workspace': transfer_store._WORKSPACE_SQL,
                                'principal': transfer_store._PRINCIPAL_SQL,
                                'member': transfer_store._MEMBER_SQL,
                                'owner': transfer_store._OWNER_SQL,
                                'audit-pair': transfer_records._AUDIT_SQL,
                            }.get(stage)
                            if (sql == selected or stage == 'audit-tail' and 'FROM main.audit WHERE sequence>' in sql):
                                raise sqlite3.OperationalError('synthetic')
                        return ConnectionProxy(connect(), before=before_sql)
                    context = patch.object(f.policy, '_connect', side_effect=opening)
                with self.subTest(mode=mode, stage=stage), context:
                    self.assertEqual(f.submit(reviewed)['status'], 503)
                self.assertEqual(snapshot(f.path), before)

    def test_actual_post_persist_corruption_is_detected_and_rolled_back(self):
        mutations = (
            "UPDATE memberships SET role='viewer' WHERE workspace_id='alpha' AND principal_id='member'",
            "UPDATE membership_authority SET realm=NULL,issuer=NULL,subject=NULL,generation=NULL WHERE workspace_id='alpha' AND principal_id='member'",
            "UPDATE workspaces SET name='altered' WHERE id='alpha'",
            "UPDATE metadata SET epoch=epoch+1 WHERE id=1",
            "UPDATE " + TABLE + " SET request_digest='" + '0' * 64 + "'",
            "UPDATE audit SET object_id='second' WHERE sequence=1",
        )
        for mode in ('bearer', 'cookie'):
            for sql in mutations:
                f = self.fresh(mode)
                reviewed = self.review(f)
                before = snapshot(f.path)
                original = f.policy._persist
                def corrupt(db, policy, *, audit_start):
                    original(db, policy, audit_start=audit_start)
                    db.execute(sql)
                with self.subTest(mode=mode, sql=sql), patch.object(f.policy, '_persist', side_effect=corrupt):
                    self.assertEqual(f.submit(reviewed)['status'], 503)
                self.assertEqual(snapshot(f.path), before)

    def test_final_expiry_after_transfer_guard_rolls_back_before_success(self):
        for mode in ('bearer', 'cookie'):
            f = self.fresh(mode)
            reviewed = self.review(f)
            before = snapshot(f.path)
            original = f.handle._snapshot
            def expiring(*args):
                result = original(*args)
                f.clock.now = NOW + timedelta(hours=2)
                return result
            with patch.object(f.handle, '_snapshot', side_effect=expiring):
                self.assertEqual(f.submit(reviewed)['status'], 401)
            self.assertEqual(snapshot(f.path), before)


class AdmissionTests(unittest.TestCase):
    def fresh(self, mode='cookie'):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return TransferFixture(directory.name, mode)

    def install_trigger(self, f, kind):
        path = f.login._path if kind.startswith('login') else f.path
        with sqlite3.connect(path) as db:
            if kind == 'login.metadata':
                # Observable if it runs: all real sessions are destroyed. A
                # harmless SELECT trigger would not prove the old bug closed.
                db.execute('CREATE TRIGGER hostile AFTER UPDATE ON metadata BEGIN DELETE FROM sessions; END')
            elif kind == 'login.sessions':
                db.execute('CREATE TRIGGER hostile AFTER UPDATE ON sessions BEGIN UPDATE metadata SET clock=-1; END')
            elif kind == 'main':
                db.execute("CREATE TRIGGER hostile AFTER UPDATE ON metadata BEGIN UPDATE memberships SET role='viewer' WHERE principal_id='admin'; END")

    def invoke(self, f, route, actor='admin', extra=None, reviewed=None):
        if route == 'post': return f.submit(reviewed, actor=actor, extra=extra)
        path = BASE + ('/capabilities' if route == 'capabilities' else '/selections/member')
        return f.request(path, actor=actor, extra=extra)

    def test_unsafe_schema_precedes_current_auth_w05_admin_and_clock_on_every_route(self):
        for mode in ('bearer', 'cookie'):
            kinds = ('main', 'temp') if mode == 'bearer' else ('main', 'login.metadata', 'login.sessions', 'temp')
            for kind in kinds:
                for route in ('capabilities', 'selection', 'post'):
                    for condition in ('current', 'expired', 'revoked', 'w05', 'viewer', 'outsider'):
                        with self.subTest(mode=mode, kind=kind, route=route, condition=condition):
                            f = self.fresh(mode)
                            reviewed = f.review()['body']['data']
                            actor = condition if condition in ('viewer', 'outsider') else 'admin'
                            extra = {'HTTP_X_WORKSPACE_ID': 'beta'} if condition == 'w05' else {}
                            if condition == 'expired':
                                if mode == 'cookie': f.clock.now = f.credentials['admin'].expires_at
                                else:
                                    # Protocol assertion remains valid; expiry is
                                    # made current only after transaction starts.
                                    pass
                            if condition == 'revoked':
                                if mode == 'cookie':
                                    c = f.credentials['admin']
                                    f.login.logout(c.token, c.csrf)
                                else:
                                    from packages.workspaces.tests.identity_fixture import simulate_identity
                                    with sqlite3.connect(f.path) as db:
                                        simulate_identity(db, "UPDATE principals SET active=0 WHERE id='admin'")
                            if kind != 'temp': self.install_trigger(f, kind)
                            before_policy = snapshot(f.path)
                            before_bytes = f.path.read_bytes()
                            before_login = snapshot(f.login._path) if f.login else None
                            before_login_bytes = f.login._path.read_bytes() if f.login else None
                            statements = []
                            connect = f.policy._connect
                            def opening():
                                db = connect()
                                if kind == 'temp':
                                    db.execute("CREATE TEMP TRIGGER hostile AFTER UPDATE ON main.metadata BEGIN UPDATE memberships SET role='viewer' WHERE principal_id='admin'; END")
                                db.set_trace_callback(statements.append)
                                if mode == 'bearer' and condition == 'expired':
                                    def after_sql(connection, sql, parameters):
                                        if sql in ('BEGIN', 'BEGIN IMMEDIATE'): f.clock.now = NOW + timedelta(hours=2)
                                    return ConnectionProxy(db, after=after_sql)
                                return db
                            owner = f.base if mode == 'bearer' else f.sessions
                            current_name = '_current_bearer_actor' if mode == 'bearer' else '_current_actor'
                            with ExitStack() as stack:
                                stack.enter_context(patch.object(f.policy, '_connect', side_effect=opening))
                                current = stack.enter_context(patch.object(owner, current_name, side_effect=AssertionError('current must follow admission')))
                                transfer = stack.enter_context(patch.object(WorkspaceService, 'transfer_admin', side_effect=AssertionError('no mutation')))
                                clock = stack.enter_context(patch.object(f.login, '_now', side_effect=AssertionError('no clock observation'))) if f.login else None
                                result = self.invoke(f, route, actor, extra, reviewed)
                                self.assertEqual(result['status'], 503, result)
                                self.assertEqual(result['body'], {'error': 'admin_transfer_unavailable'})
                                current.assert_not_called()
                                transfer.assert_not_called()
                                if clock: clock.assert_not_called()
                            after_begin = next(i for i, sql in enumerate(statements) if sql.upper().startswith('BEGIN'))
                            transaction = [' '.join(sql.upper().split()) for sql in statements[after_begin + 1:]]
                            self.assertNotIn('SAVEPOINT BUSINESS', transaction)
                            self.assertFalse(any(sql.startswith(('UPDATE ', 'INSERT ', 'DELETE ', 'COMMIT')) for sql in transaction), transaction)
                            self.assertFalse(any('SELECT BINDING,CLOCK FROM LOGIN.METADATA' in sql for sql in transaction))
                            self.assertIn('ROLLBACK', transaction)
                            self.assertEqual(snapshot(f.path), before_policy)
                            self.assertEqual(f.path.read_bytes(), before_bytes)
                            if f.login:
                                self.assertEqual(snapshot(f.login._path), before_login)
                                self.assertEqual(f.login._path.read_bytes(), before_login_bytes)

    def test_malicious_login_clock_trigger_has_real_observable_control_effect(self):
        f = self.fresh()
        self.install_trigger(f, 'login.metadata')
        with sqlite3.connect(f.login._path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone(), (6,))
            db.execute('UPDATE metadata SET clock=clock+1 WHERE id=1')
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone(), (0,))

    def test_cookie_admission_holds_both_writer_locks_on_current_actor_connection(self):
        f = self.fresh()
        original_admission, original_current = f.handle._admit_schema, f.sessions._current_actor
        seen_admission, seen_current = [], []
        def admission(db, **kwargs):
            seen_admission.append(id(db))
            self.assertTrue(db.in_transaction)
            for path in (f.path, f.login._path):
                with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as competitor:
                    with self.assertRaises(sqlite3.OperationalError): competitor.execute('BEGIN IMMEDIATE')
            return original_admission(db, **kwargs)
        def current(db, *args):
            self.assertTrue(seen_admission)
            seen_current.append(id(db))
            return original_current(db, *args)
        with patch.object(f.handle, '_admit_schema', side_effect=admission), patch.object(f.sessions, '_current_actor', side_effect=current):
            self.assertEqual(f.request()['status'], 200)
        self.assertTrue(seen_current)
        self.assertEqual(set(seen_admission + seen_current), {seen_admission[0]})

    def test_clean_schema_controls_preserve_auth_w05_admin_and_legal_clock(self):
        for route in ('capabilities', 'selection', 'post'):
            for condition, expected in (('current', 200), ('expired', 401), ('revoked', 401), ('w05', 400), ('viewer', 403), ('outsider', 404)):
                with self.subTest(route=route, condition=condition):
                    f = self.fresh()
                    reviewed = f.review()['body']['data']
                    if condition == 'revoked':
                        c = f.credentials['admin']
                        f.login.logout(c.token, c.csrf)
                    f.clock.now = f.credentials['admin'].expires_at if condition == 'expired' else NOW + timedelta(seconds=1)
                    actor = condition if condition in ('viewer', 'outsider') else 'admin'
                    extra = {'HTTP_X_WORKSPACE_ID': 'beta'} if condition == 'w05' else {}
                    result = self.invoke(f, route, actor, extra, reviewed)
                    self.assertEqual(result['status'], expected, result)
                    with sqlite3.connect(f.login._path) as db:
                        floor = db.execute('SELECT clock FROM metadata').fetchone()[0]
                    from packages.login_state.core import _micros
                    self.assertEqual(floor, _micros(f.clock.now))
                    self.assertEqual(f.counts()[:2], (1, 2) if route == 'post' and condition == 'current' else (0, 0))

    def test_hook_shape_and_receipt_mode_reject_before_branch_dispatch(self):
        for mode in ('bearer', 'cookie'):
            f = self.fresh(mode)
            env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': BASE + '/capabilities',
                   'wsgi.url_scheme': 'https', 'HTTP_HOST': 'app.example.invalid', **f.headers()}
            assertion = f.base._verify_request(env)
            direct = [(f.base._authenticated_transaction, assertion)]
            if mode == 'cookie':
                direct += [(f.boundary._authenticated_transaction, assertion), (f.sessions.transaction, assertion)]
            valid_mode = _DirectoryUnavailable('alpha', 'pipeline.scopes.read')
            for method, request in direct:
                for options in ({'storage_admission': object(), 'with_connection': True},
                        {'storage_admission': lambda db: None},
                        {'storage_admission': lambda db: None, 'with_connection': True, 'receipt_only': valid_mode}):
                    business = Mock()
                    with patch('packages.directory_cursors.transaction.run') as receipt_run, patch.object(f.policy, '_connect') as connect:
                        with self.assertRaises(ValueError): method(request, business, **options)
                        business.assert_not_called()
                        receipt_run.assert_not_called()
                        connect.assert_not_called()

    def test_non_none_admission_return_and_admission_fault_never_enter_business(self):
        for mode in ('bearer', 'cookie'):
            for admission in (lambda db: 1, lambda db: (_ for _ in ()).throw(RuntimeError('synthetic'))):
                f = self.fresh(mode)
                env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': BASE + '/capabilities',
                       'wsgi.url_scheme': 'https', 'HTTP_HOST': 'app.example.invalid', **f.headers()}
                assertion = f.base._verify_request(env)
                before = snapshot(f.path)
                login_before = snapshot(f.login._path) if f.login else None
                business = Mock()
                with self.assertRaises((ValueError, RuntimeError, AuthenticationUnavailable)):
                    f.base._authenticated_transaction(assertion, business,
                        with_connection=True, storage_admission=admission)
                business.assert_not_called()
                self.assertEqual(snapshot(f.path), before)
                if f.login: self.assertEqual(snapshot(f.login._path), login_before)

    def test_default_none_does_not_call_admission_and_old_cookie_flow_stays_ordinary(self):
        for mode in ('bearer', 'cookie'):
            f = self.fresh(mode)
            with patch.object(TransferReceiptStore, '_admit_schema', side_effect=AssertionError('not an old-route hook')):
                self.assertEqual(f.request('/api/workspaces')['status'], 200)
                response = f.request('/api/workspaces/alpha/members/member', method='PATCH',
                    body={'role': 'viewer', 'state': 'active'}, extra={'HTTP_IF_MATCH': '"1"'})
                self.assertEqual(response['status'], 200)
                self.assertEqual(f.request('/api/workspaces/alpha/members', actor='viewer')['status'], 403)
                if mode == 'cookie':
                    self.assertEqual(f.request('/api/workspaces/alpha/members/member', method='PATCH',
                        body={'role': 'developer', 'state': 'active'},
                        extra={'HTTP_IF_MATCH': '"2"', 'HTTP_X_CSRF_TOKEN': None})['status'], 401)
            self.assertEqual(f.counts()[:2], (0, 1))

    def test_post_savepoint_trigger_is_really_rolled_back_before_clock_write(self):
        f = self.fresh()
        reviewed = f.review()['body']['data']
        before = snapshot(f.path)
        f.clock.now = NOW + timedelta(seconds=1)
        original = f.policy._persist
        statements = []
        def injecting(db, policy, *, audit_start):
            original(db, policy, audit_start=audit_start)
            db.execute('CREATE TRIGGER login.transient_hostile AFTER UPDATE ON metadata BEGIN DELETE FROM sessions; END')
        connect = f.policy._connect
        def opening():
            db = connect()
            db.set_trace_callback(statements.append)
            return db
        with patch.object(f.policy, '_persist', side_effect=injecting), patch.object(f.policy, '_connect', side_effect=opening):
            self.assertEqual(f.submit(reviewed)['status'], 503)
        self.assertEqual(snapshot(f.path), before)
        with sqlite3.connect(f.login._path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_schema WHERE type='trigger'").fetchone(), (0,))
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone(), (6,))
            from packages.login_state.core import _micros
            self.assertEqual(db.execute('SELECT clock FROM metadata').fetchone(), (_micros(f.clock.now),))
        normalized = [' '.join(sql.upper().split()) for sql in statements]
        rollback = normalized.index('ROLLBACK TO BUSINESS')
        clock_write = next(i for i, sql in enumerate(normalized) if sql.startswith('UPDATE LOGIN.METADATA SET CLOCK='))
        self.assertLess(rollback, clock_write)

    def test_read_guard_rechecks_schema_after_business_and_rolls_back_ddl(self):
        for mode in ('bearer', 'cookie'):
            for route in ('capabilities', 'selection'):
                f = self.fresh(mode)
                before = snapshot(f.path)
                original = f.handle._bound
                def injected(policy, db, admission=None):
                    original(policy, db, admission)
                    db.execute('CREATE TEMP TABLE late_schema_change (value TEXT)')
                with patch.object(f.handle, '_bound', side_effect=injected):
                    self.assertEqual(self.invoke(f, route)['status'], 503)
                self.assertEqual(snapshot(f.path), before)
                self.assertEqual(self.invoke(f, route)['status'], 200)

    def test_failed_real_savepoint_rollback_cannot_reach_clock_commit(self):
        f = self.fresh()
        reviewed = f.review()['body']['data']
        before = snapshot(f.path)
        login_before = snapshot(f.login._path)
        statements = []
        connect = f.policy._connect
        def opening():
            db = connect()
            db.set_trace_callback(statements.append)
            def before_sql(connection, sql, args):
                if sql == 'ROLLBACK TO business': raise sqlite3.OperationalError('synthetic rollback failure')
            return ConnectionProxy(db, before=before_sql)
        with patch.object(f.policy, '_connect', side_effect=opening), patch.object(TransferReceiptStore, '_insert', side_effect=sqlite3.OperationalError('synthetic')):
            self.assertEqual(f.submit(reviewed)['status'], 503)
        self.assertEqual(snapshot(f.path), before)
        self.assertEqual(snapshot(f.login._path), login_before)
        self.assertFalse(any(sql.upper().startswith('UPDATE LOGIN.METADATA') for sql in statements))
        self.assertFalse(any(sql.upper() == 'COMMIT' for sql in statements))
        self.assertIn('ROLLBACK', [sql.upper() for sql in statements])


if __name__ == '__main__': unittest.main()
