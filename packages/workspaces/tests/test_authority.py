"""Closed authority foundation; all identities/data are synthetic."""
import ast
from contextlib import closing
from dataclasses import astuple
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.workspaces import Principal, PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.sqlite import _APPLICATION_ID, _SCHEMA_V1, _SCHEMA, _MAX_AUTHORITY
from packages.workspaces.tests.test_service import fixture
from packages.workspaces.tests.test_sqlite import bootstrap


def legacy_policy(path, *, source=None):
    source = source or fixture(two_admins=True)
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        for sql in _SCHEMA_V1: db.execute(sql)
        db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
        db.executemany("INSERT INTO principals VALUES(?,?,?,?)", (astuple(p)[:4] for p in source._principals.values()))
        db.executemany("INSERT INTO operators VALUES(?)", ((x,) for x in source._operators))
        db.execute("INSERT INTO metadata VALUES(1,?)", (source._epoch,))
        # Build the historical schema explicitly. A current writer must never
        # create missing authority sidecars when handed a legacy database.
        db.executemany("INSERT INTO workspaces VALUES (?,?,?,?,?)", (astuple(w) for w in source._workspaces.values()))
        db.executemany("INSERT INTO memberships VALUES (?,?,?,?,?)", (astuple(m) for m in source._memberships.values()))
        db.executemany("INSERT INTO modules VALUES (?,?,?,?,?,?)", ((m.workspace_id,m.key,"read" in m.capabilities,"write" in m.capabilities,m.state,m.revision) for m in source._modules.values()))
        db.executemany("INSERT INTO resources VALUES (?,?,?,?,?)", (astuple(r) for r in source._resources.values()))
        db.executemany("INSERT INTO audit VALUES (?,?,?,?,?,?)", (astuple(e) for e in source._audit))
        db.commit()
    return source


def snapshot(path):
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        return (db.execute("PRAGMA user_version").fetchone()[0], tuple(db.iterdump()))


class AuthorityFoundationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "policy.sqlite"

    def test_model_requires_bounded_exact_integer_authority(self):
        for value in (0, -1, True, 1.0, "1", _MAX_AUTHORITY + 1):
            for field in ("generation", "authority_epoch"):
                with self.subTest(value=value, field=field), self.assertRaises(ValueError):
                    Principal("p", "https://identity.example.invalid", "sub", **{field:value})

    def test_runtime_and_trusted_entry_are_unconditionally_closed(self):
        store = bootstrap(self.path)
        for target in (store, fixture()):
            for kwargs in ({}, {"active":False}, {"force":True}, {"enabled":True}, {"generation":2}):
                with self.assertRaises(PolicyError) as caught:
                    target.set_principal("operator", "viewer", **kwargs)
                self.assertEqual((caught.exception.status, caught.exception.code), (503, "identity_transition_unavailable"))
        # The generic trusted transaction cannot reach a hidden transition helper.
        with self.assertRaises(PolicyError):
            store._call("set_principal", "operator", "viewer", active=False, write=True)

    def test_sql_identity_metadata_and_migration_marker_gates(self):
        bootstrap(self.path)
        before = snapshot(self.path)
        statements = [
            "UPDATE principals SET subject='replacement' WHERE id='viewer'",
            "UPDATE principals SET issuer='https://other.example.invalid' WHERE id='viewer'",
            "UPDATE principals SET active=0 WHERE id='viewer'",
            "UPDATE principals SET generation=generation+1 WHERE id='viewer'",
            "UPDATE principals SET authority_epoch=authority_epoch+1 WHERE id='viewer'",
            "UPDATE principals SET id='replacement' WHERE id='viewer'",
            "DELETE FROM principals WHERE id='viewer'",
            "INSERT OR REPLACE INTO principals SELECT id,issuer,subject,active,generation,authority_epoch FROM principals WHERE id='viewer'",
            "INSERT INTO principals VALUES('new','https://identity.example.invalid','new',1,1,1)",
            "UPDATE metadata SET epoch=0", "UPDATE metadata SET realm='replacement'",
            "UPDATE metadata SET epoch=9007199254740992", "DELETE FROM metadata",
            "INSERT OR REPLACE INTO metadata SELECT * FROM metadata",
            "DELETE FROM authority_migrations", "UPDATE authority_migrations SET principal_count=0",
            "INSERT OR REPLACE INTO authority_migrations SELECT * FROM authority_migrations",
        ]
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            for sql in statements:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError): db.execute(sql)
                self.assertEqual(snapshot(self.path), before)
            db.execute("UPDATE principals SET active=active, generation=generation")
            self.assertEqual(snapshot(self.path), before)

    def test_reopen_preserves_realm_and_new_database_changes_it(self):
        store = bootstrap(self.path)
        realm = store._transaction(lambda p:p._realm, write=False)
        self.assertEqual(SQLiteWorkspaceService(self.path)._transaction(lambda p:p._realm, write=False), realm)
        other = bootstrap(self.path.with_name("other.sqlite"))
        self.assertNotEqual(other._transaction(lambda p:p._realm, write=False), realm)

    def test_legacy_requires_explicit_migration_and_keeps_module_data(self):
        source = legacy_policy(self.path)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("CREATE TABLE synthetic_module (value TEXT) STRICT")
            db.execute("INSERT INTO synthetic_module VALUES('retained')")
        before = snapshot(self.path)
        with self.assertRaises(StorageError): SQLiteWorkspaceService(self.path)
        self.assertEqual(snapshot(self.path), before)
        report = SQLiteWorkspaceService.migrate_v1(self.path)
        self.assertEqual(report, dict(from_version=1, to_version=3, principals=len(source._principals), identity_mutation_enabled=False, legacy_grants_upgraded=False))
        migrated = SQLiteWorkspaceService(self.path)
        def verify(p):
            self.assertEqual(p._memberships, source._memberships)
            self.assertEqual(p._operators, source._operators)
            self.assertEqual(p._resources, source._resources)
            self.assertTrue(all(x.generation == x.authority_epoch == 1 for x in p._principals.values()))
        migrated._transaction(verify, write=False)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            self.assertEqual(db.execute("SELECT * FROM synthetic_module").fetchall(), [("retained",)])
        before = snapshot(self.path)
        with self.assertRaises(StorageError): SQLiteWorkspaceService.migrate_v1(self.path)
        self.assertEqual(snapshot(self.path), before)

    def test_legacy_inactive_members_and_operator_are_not_reactivated(self):
        source = legacy_policy(self.path)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("UPDATE principals SET active=0 WHERE id='viewer'")
            db.execute("INSERT INTO operators VALUES('viewer')")
        SQLiteWorkspaceService.migrate_v1(self.path)
        store = SQLiteWorkspaceService(self.path)
        self.assertFalse(store._transaction(lambda p:p._principals['viewer'].active, write=False))
        with self.assertRaises(PolicyError): store.set_principal('operator', 'viewer', active=True)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE principals SET active=1 WHERE id='viewer'")
        with self.assertRaises(PolicyError): store.list_workspaces('viewer')

    def test_migration_rejects_weakened_schema_trigger_index_and_corruption(self):
        variants = ["CREATE TRIGGER untrusted AFTER UPDATE ON metadata BEGIN SELECT 1; END",
                    "CREATE INDEX untrusted ON principals(subject)",
                    "PRAGMA ignore_check_constraints=ON; UPDATE principals SET active=2 WHERE id='viewer'",
                    "PRAGMA ignore_check_constraints=ON; UPDATE metadata SET epoch=9007199254740992"]
        for i, sql in enumerate(variants):
            path = self.path.with_name(f"legacy-{i}.sqlite"); legacy_policy(path)
            with closing(sqlite3.connect(path, isolation_level=None)) as db: db.executescript(sql)
            before = snapshot(path)
            with self.subTest(sql=sql), self.assertRaises(StorageError): SQLiteWorkspaceService.migrate_v1(path)
            self.assertEqual(snapshot(path), before)
        # A same-named permissive table is not the known v1 core.
        path = self.path.with_name('weak.sqlite'); legacy_policy(path)
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            db.executescript("ALTER TABLE principals RENAME TO old; CREATE TABLE principals AS SELECT * FROM old; DROP TABLE old;")
        before = snapshot(path)
        with self.assertRaises(StorageError): SQLiteWorkspaceService.migrate_v1(path)
        self.assertEqual(snapshot(path), before)

    def test_every_migration_ddl_failure_rolls_back_columns_triggers_data_and_version(self):
        fail_points = ["ALTER TABLE principals ADD COLUMN generation", "ALTER TABLE principals ADD COLUMN authority_epoch",
                       "ALTER TABLE metadata ADD COLUMN realm", "CREATE TABLE authority_migrations",
                       *[sql.split(" BEGIN")[0] for sql in _SCHEMA if sql.startswith("CREATE TRIGGER")], "PRAGMA user_version=3"]
        original_connect = SQLiteWorkspaceService._connect
        class Injected(sqlite3.Connection):
            needle = None
            def execute(self, sql, *args):
                if sql.startswith(self.needle): raise sqlite3.OperationalError('synthetic fault')
                return super().execute(sql, *args)
        for i, point in enumerate(fail_points):
            path = self.path.with_name(f'fault-{i}.sqlite'); legacy_policy(path); before = snapshot(path)
            Injected.needle = point
            def connect(store): return sqlite3.connect(store._uri, uri=True, isolation_level=None, factory=Injected)
            with self.subTest(point=point), patch.object(SQLiteWorkspaceService, '_connect', connect), self.assertRaises(StorageError):
                SQLiteWorkspaceService.migrate_v1(path)
            self.assertEqual(snapshot(path), before)

    def test_missing_guard_is_not_an_activation_switch(self):
        store = bootstrap(self.path)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db: db.execute("DROP TRIGGER principal_authority_closed")
        with self.assertRaises(StorageError): store.list_workspaces('viewer')

    def test_epoch_overflow_rolls_back_policy_and_audit(self):
        store = bootstrap(self.path)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db: db.execute("UPDATE metadata SET epoch=?", (_MAX_AUTHORITY,))
        before = snapshot(self.path)
        with self.assertRaises(PolicyError) as caught: store.set_membership('alpha_admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=1)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, 'counter_exhausted'))
        self.assertEqual(snapshot(self.path), before)

    def test_production_modules_do_not_import_fixture_bypass(self):
        root = Path(__file__).resolve().parents[3]
        for path in (root / 'packages').rglob('*.py'):
            if 'tests' in path.parts: continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn('identity_fixture', node.module or '', str(path))
                if isinstance(node, ast.Import):
                    for alias in node.names: self.assertNotIn('identity_fixture', alias.name, str(path))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertNotEqual(node.name, 'simulate_identity', str(path))


if __name__ == '__main__': unittest.main()
