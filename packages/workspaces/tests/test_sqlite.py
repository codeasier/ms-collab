"""Synthetic policy parity, rollback, reopen and real cross-process races."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
from packages.workspaces.tests.identity_fixture import simulate_identity
import unittest
from unittest.mock import patch

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.tests import test_service as memory_tests


def bootstrap(path, *, two_admins=False, timeout=5.0):
    source = memory_tests.fixture(two_admins=two_admins)
    return SQLiteWorkspaceService.initialize(
        path, principals=tuple(source._principals.values()),
        workspaces=tuple(source._workspaces.values()),
        memberships=tuple(source._memberships.values()),
        modules=tuple(source._modules.values()), resources=tuple(source._resources.values()),
        operators=source._operators, timeout=timeout,
    )


class SQLiteContractTests(memory_tests.ServiceTests):
    """Run the existing behavioral policy contract against the SQLite adapter."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.counter = 0
        super().setUp()

    def fixture(self, **kwargs):
        self.counter += 1
        return bootstrap(Path(self.directory.name) / f"policy-{self.counter}.sqlite3", **kwargs)


class SQLiteStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "policy ?# local.sqlite3"
        self.store = bootstrap(self.path, two_admins=True)

    def assert_storage(self, code, function, *args, **kwargs):
        with self.assertRaises(StorageError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(str(self.path), str(caught.exception))

    def connection(self):
        connection = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(connection.close)
        return connection

    def worker(self, action):
        code = '''
import sys
from packages.workspaces import SQLiteWorkspaceService, PolicyError
store = SQLiteWorkspaceService(sys.argv[1])
action = sys.argv[2]
if action == "stale-decision":
    store.authorize("multi", "alpha", "board", "write")
print("ready", flush=True)
assert sys.stdin.readline().strip() == "go"
try:
    if action == "demote":
        store.set_membership(sys.argv[3], "alpha", sys.argv[3], role="developer", state="active", expected_revision=1)
    else:
        store.update_resource("multi", "alpha", "board", "same", text="synthetic child", expected_revision=1)
    print("ok", flush=True)
except PolicyError as error:
    print(error.code, flush=True)
'''
        process = subprocess.Popen([sys.executable, "-c", code, str(self.path), *action],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        self.assertEqual(process.stdout.readline().strip(), "ready")
        return process

    def go(self, process):
        process.stdin.write("go\n")
        process.stdin.flush()

    def finish(self, process):
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        return output.strip()

    def test_reopen_preserves_revisions_epoch_resources_and_scoped_audit(self):
        epoch = self.store.authorize("viewer", "alpha", "board", "read").policy_epoch
        self.store.update_resource("alpha_admin", "alpha", "board", "same", text="after restart", expected_revision=1)
        self.store.set_membership("alpha_admin", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        reopened = SQLiteWorkspaceService(self.path)
        self.assertEqual(reopened.read_resource("alpha_admin", "alpha", "board", "same").revision, 2)
        self.assertEqual(reopened.read_resource("alpha_admin", "alpha", "board", "same").text, "after restart")
        self.assertGreater(reopened.authorize("alpha_admin", "alpha", "board", "read").policy_epoch, epoch)
        with self.assertRaises(PolicyError) as denied:
            reopened.read_resource("viewer", "alpha", "board", "same")
        self.assertEqual(denied.exception.code, "not_found")
        self.assertEqual([event.sequence for event in reopened.audit_events("alpha_admin", "alpha")], [1, 2])
        self.assertEqual(reopened.audit_events("beta_admin", "beta"), ())
        self.assertNotIn("after restart", repr(reopened.audit_events("alpha_admin", "alpha")))

    def test_initialize_refuses_any_existing_file_and_open_never_creates(self):
        before = self.path.read_bytes()
        self.assert_storage("store_exists", bootstrap, self.path)
        self.assertEqual(before, self.path.read_bytes())
        missing = self.path.parent / "missing.sqlite3"
        self.assert_storage("store_unavailable", SQLiteWorkspaceService, missing)
        self.assertFalse(missing.exists())
        empty = self.path.parent / "empty.sqlite3"
        empty.touch()
        self.assert_storage("store_exists", bootstrap, empty)
        self.assert_storage("unsupported_schema", SQLiteWorkspaceService, empty)

    def test_invalid_bootstrap_does_not_create_store(self):
        target = self.path.parent / "invalid.sqlite3"
        source = memory_tests.fixture()
        with self.assertRaises(ValueError):
            SQLiteWorkspaceService.initialize(target, principals=tuple(source._principals.values()),
                workspaces=tuple(source._workspaces.values()), memberships=(), modules=())
        self.assertFalse(target.exists())

    def test_unknown_schema_version_and_unrelated_database_fail_closed(self):
        connection = self.connection()
        connection.execute("PRAGMA user_version=999")
        self.assert_storage("unsupported_schema", SQLiteWorkspaceService, self.path)
        self.assert_storage("unsupported_schema", self.store.list_workspaces, "viewer")
        connection.execute("PRAGMA user_version=1")
        connection.execute("PRAGMA application_id=0")
        self.assert_storage("unsupported_schema", self.store.list_workspaces, "viewer")

    def test_corrupt_store_errors_do_not_leak_paths_or_sql(self):
        invalid = self.path.parent / "corrupt.sqlite3"
        invalid.write_bytes(b"synthetic invalid database")
        self.assert_storage("store_unavailable", SQLiteWorkspaceService, invalid)

    def test_database_foreign_keys_and_composite_uniqueness(self):
        connection = self.connection()
        connection.execute("PRAGMA foreign_keys=ON")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO resources VALUES ('missing','board','x','text',1)")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO resources VALUES ('alpha','board','same','text',1)")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO principals VALUES ('new','synthetic','viewer',1,1,1)")

    def test_malformed_boolean_cannot_reenable_principal_or_capability(self):
        connection = self.connection()
        connection.execute("PRAGMA ignore_check_constraints=ON")
        simulate_identity(connection, "UPDATE principals SET active=2 WHERE id='disabled'")
        self.assert_storage("invalid_store", self.store.list_workspaces, "disabled")
        simulate_identity(connection, "UPDATE principals SET active=0 WHERE id='disabled'")
        for field in ("can_read", "can_write"):
            connection.execute(f"UPDATE modules SET {field}=2 WHERE workspace_id='commons'")
            self.assert_storage("invalid_store", self.store.authorize, "beta_admin", "commons", "board", "write")
            connection.execute(f"UPDATE modules SET {field}=? WHERE workspace_id='commons'", (int(field == "can_read"),))

    def test_orphan_or_invalid_audit_data_fails_closed(self):
        connection = self.connection()  # External SQLite connections default to FK off.
        connection.execute("INSERT INTO audit VALUES (1,'missing','alpha','resource.update','same',1)")
        self.assert_storage("invalid_store", self.store.audit_events, "alpha_admin", "alpha")
        connection.execute("UPDATE audit SET actor_id='alpha_admin',action='unsupported'")
        self.assert_storage("invalid_store", self.store.audit_events, "alpha_admin", "alpha")

    def test_atomic_transfer_and_scoped_changes_survive_restart(self):
        self.store.transfer_admin("alpha_admin", "alpha", "viewer", expected_revision=1, replacement_revision=1)
        reopened = SQLiteWorkspaceService(self.path)
        self.assertEqual({m.principal_id: m.role for m in reopened.list_memberships("viewer", "alpha")}["alpha_admin"], "developer")
        self.assertEqual([e.action for e in reopened.audit_events("viewer", "alpha")], ["membership.promote", "membership.demote"])
        self.assertEqual(reopened.read_resource("beta_admin", "beta", "board", "same").text, "Beta data")

    def test_workspace_creation_and_admin_membership_commit_together(self):
        selected = self.store.initial_admin_selection("operator", "viewer")["precondition"]
        workspace = self.store.create_workspace("operator", name="Persisted", kind="shared", initial_admin="viewer",
                                                expected_initial_admin_selection=selected)
        reopened = SQLiteWorkspaceService(self.path)
        self.assertIn(workspace, reopened.list_workspaces("viewer"))
        self.assertNotIn(workspace, reopened.list_workspaces("operator"))
        self.assertEqual(reopened.list_memberships("viewer", workspace.id)[0].role, "admin")
        self.assertEqual(reopened.audit_events("viewer", workspace.id)[0].action, "workspace.create")

    def test_failed_audit_insert_rolls_back_policy_resource_and_epoch(self):
        connection = self.connection()
        epoch = self.store.authorize("viewer", "alpha", "board", "read").policy_epoch
        connection.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        self.assert_storage("store_unavailable", self.store.update_resource, "alpha_admin", "alpha", "board", "same", text="must rollback", expected_revision=1)
        self.assert_storage("store_unavailable", self.store.set_membership, "alpha_admin", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        reopened = SQLiteWorkspaceService(self.path)
        self.assertEqual(reopened.read_resource("viewer", "alpha", "board", "same").text, "Alpha data")
        self.assertEqual(reopened.authorize("viewer", "alpha", "board", "read").policy_epoch, epoch)
        self.assertEqual(reopened.audit_events("alpha_admin", "alpha"), ())

    def test_failed_transfer_cannot_commit_only_one_membership(self):
        with patch.object(SQLiteWorkspaceService, "_persist", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.store.transfer_admin("alpha_admin", "alpha", "viewer", expected_revision=1, replacement_revision=1)
        reopened = SQLiteWorkspaceService(self.path)
        members = {m.principal_id: m.role for m in reopened.list_memberships("alpha_admin", "alpha")}
        self.assertEqual((members["alpha_admin"], members["viewer"]), ("admin", "viewer"))
        self.assertEqual(reopened.audit_events("alpha_admin", "alpha"), ())

    def test_busy_is_bounded_and_not_misreported_as_authorization(self):
        contender = SQLiteWorkspaceService(self.path, timeout=0)
        connection = self.connection()
        connection.execute("BEGIN IMMEDIATE")
        self.assert_storage("store_busy", contender.update_resource, "alpha_admin", "alpha", "board", "same", text="no", expected_revision=1)
        connection.rollback()
        self.assertEqual(contender.read_resource("viewer", "alpha", "board", "same").revision, 1)
        self.assertEqual(contender.audit_events("alpha_admin", "alpha"), ())
        self.assertEqual(contender.update_resource("alpha_admin", "alpha", "board", "same", text="retry", expected_revision=1).revision, 2)

    def test_invalid_timeout_does_not_create_store(self):
        for timeout in (-1, 31, True, float("nan"), float("inf"), "1"):
            target = self.path.parent / "invalid-timeout.sqlite3"
            with self.assertRaises(ValueError):
                bootstrap(target, timeout=timeout)
            self.assertFalse(target.exists())

    def test_separate_connections_read_after_revocation_commit_deny(self):
        second = SQLiteWorkspaceService(self.path)
        second.authorize("multi", "alpha", "board", "write")
        self.store.set_membership("alpha_admin", "alpha", "multi", role="admin", state="revoked", expected_revision=1)
        with self.assertRaises(PolicyError) as denied:
            second.update_resource("multi", "alpha", "board", "same", text="late", expected_revision=1)
        self.assertEqual(denied.exception.code, "not_found")
        self.assertEqual(self.store.read_resource("alpha_admin", "alpha", "board", "same").text, "Alpha data")

    def test_two_instances_competing_updates_have_one_winner(self):
        barrier = Barrier(2)
        def update(value):
            instance = SQLiteWorkspaceService(self.path)
            barrier.wait(timeout=5)
            try:
                instance.update_resource("alpha_admin", "alpha", "board", "same", text=value, expected_revision=1)
                return "ok"
            except PolicyError as error:
                return error.code
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(list(pool.map(update, ("one", "two"))), ["ok", "stale_revision"])
        self.assertEqual(len(self.store.audit_events("alpha_admin", "alpha")), 1)

    def test_two_processes_competing_updates_have_one_winner(self):
        processes = [self.worker(("write",)) for _ in range(2)]
        for process in processes:
            self.go(process)
        self.assertCountEqual([self.finish(p) for p in processes], ["ok", "stale_revision"])
        self.assertEqual(self.store.read_resource("viewer", "alpha", "board", "same").revision, 2)
        self.assertEqual(len(self.store.audit_events("alpha_admin", "alpha")), 1)

    def test_two_processes_cannot_demote_last_admin(self):
        processes = [self.worker(("demote", actor)) for actor in ("alpha_admin", "multi")]
        for process in processes:
            self.go(process)
        results = [self.finish(p) for p in processes]
        self.assertCountEqual(results, ["ok", "last_admin"])
        survivor = "alpha_admin" if results[0] != "ok" else "multi"
        self.assertEqual(sum(m.role == "admin" and m.state == "active" for m in self.store.list_memberships(survivor, "alpha")), 1)

    def test_prior_decision_in_other_process_is_not_valid_after_revoke(self):
        process = self.worker(("stale-decision",))  # readiness means authorize ran.
        self.store.set_membership("alpha_admin", "alpha", "multi", role="admin", state="revoked", expected_revision=1)
        self.go(process)
        self.assertEqual(self.finish(process), "not_found")
        self.assertEqual(self.store.read_resource("alpha_admin", "alpha", "board", "same").revision, 1)

    def test_killed_writer_does_not_leave_partial_commit(self):
        code = '''
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
connection.execute("UPDATE resources SET text='uncommitted',revision=2 WHERE workspace_id='alpha'")
os._exit(23)
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], timeout=10, capture_output=True)
        self.assertEqual(result.returncode, 23)
        reopened = SQLiteWorkspaceService(self.path)
        self.assertEqual(reopened.read_resource("viewer", "alpha", "board", "same").text, "Alpha data")
        self.assertEqual(reopened.audit_events("alpha_admin", "alpha"), ())

    def test_read_snapshot_does_not_mix_policy_and_resource_versions(self):
        # Pin an old read snapshot explicitly, then show writers cannot commit
        # until it finishes under the default rollback-journal mode.
        connection = self.connection()
        connection.execute("BEGIN")
        connection.execute("SELECT epoch FROM metadata").fetchone()
        contender = SQLiteWorkspaceService(self.path, timeout=0)
        self.assert_storage("store_busy", contender.set_membership, "alpha_admin", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        self.assertEqual(connection.execute("SELECT state FROM memberships WHERE workspace_id='alpha' AND principal_id='viewer'").fetchone()[0], "active")
        connection.commit()
        self.assertEqual(contender.audit_events("alpha_admin", "alpha"), ())
        contender.set_membership("alpha_admin", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        with self.assertRaises(PolicyError):
            self.store.read_resource("viewer", "alpha", "board", "same")


if __name__ == "__main__":
    unittest.main()
