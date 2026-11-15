# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic cross-I/O authority transitions; no production mutation or network.

The shared identity fixture bypasses CLOSED guards only in disposable test DBs.
It is not a usable directory-transition implementation. Membership reactivation
below similarly isolates a future explicit regrant, not a supported edit API.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import sqlite3
import tempfile
from pathlib import Path
from threading import Barrier, Event, current_thread
import unittest
from unittest.mock import patch

from packages.identity import AuthenticationError, Credential, Provider
from packages.pipeline_api import decode_snapshots, resource_id
from packages.pipeline_sync import BoundCredential, Connection, PipelineSynchronizer
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.tests.identity_fixture import simulate_identity
from packages.pipeline_sync.tests.test_sync import Fake, ISSUER, NOW, ORIGIN, SCOPE, TOKEN, Verifier, initialize, payload


class FlowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "policy.sqlite"
        self.connection = Connection(SCOPE, ORIGIN)
        self.credential = BoundCredential(SCOPE, ORIGIN, TOKEN)
        self.store = initialize(self.path, self.connection)
        self.fake = Fake()
        self.now = NOW
        self.providers = (Provider("test", ISSUER, "pipeline-test", Verifier()),)
        self.sync = self.make_sync(self.fake)

    def make_sync(self, transport):
        return PipelineSynchronizer(store=self.store, providers=self.providers, provider_key="test",
                                    clock=lambda: self.now, transport=transport)

    def refresh(self, sync=None):
        return (sync or self.sync).refresh(user_credential=Credential("developer"), connection=self.connection,
                upstream_credential=self.credential, expected_revision=1)

    def record(self):
        return self.store.read_resource("admin", "alpha", "pipelines", resource_id(SCOPE))

    def assert_unpublished(self):
        self.assertEqual(self.record().revision, 1)
        self.assertEqual(decode_snapshots(SCOPE, self.record().text), [])
        self.assertEqual([e for e in self.store.audit_events("admin", "alpha")
                          if e.action == "resource.update"], [])

    def identity_aba(self, db=None, actor="developer", active=False):
        if db is None:
            with sqlite3.connect(self.path) as db:
                self.identity_aba(db, actor=actor, active=active)
            return
        if active:
            simulate_identity(db, "UPDATE principals SET active=0 WHERE id=?", (actor,))
            simulate_identity(db, "UPDATE principals SET active=1 WHERE id=?", (actor,))
        else:
            simulate_identity(db, "UPDATE principals SET subject='temporary-subject' WHERE id=?", (actor,))
            simulate_identity(db, "UPDATE principals SET subject=? WHERE id=?", (actor, actor))

    def once_at(self, count, action):
        self.fake.hook = lambda: action() if len(self.fake.calls) == count else None

    def test_identity_subject_aba_after_list_stops_next_read(self):
        self.once_at(1, self.identity_aba)
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assert_unpublished()

    def test_disable_reenable_aba_after_last_detail_stops_publication(self):
        self.once_at(2, lambda: self.identity_aba(active=True))
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 2)
        self.assert_unpublished()

    def test_aba_between_initial_authorization_and_first_get_sends_nothing(self):
        original = self.sync._authority._authenticated_transaction
        calls = []
        def transaction(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(None)
            if len(calls) == 1:
                self.identity_aba()
            return result
        with patch.object(self.sync._authority, "_authenticated_transaction", side_effect=transaction):
            with self.assertRaises(AuthenticationError):
                self.refresh()
        self.assertEqual(self.fake.calls, [])
        self.assert_unpublished()

    def test_multi_run_traversal_checks_before_each_detail(self):
        self.fake.items = [{"id": "r1", "pipeline_id": "build"}, {"id": "r2", "pipeline_id": "build"}]
        self.once_at(2, self.identity_aba)
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(self.fake.calls, ["/api/v1/pipelines/build/runs?limit=5", "/api/v1/pipelines/build/runs/r1"])
        self.assert_unpublished()

    def test_empty_list_still_revalidates_before_publication(self):
        self.fake.items = []
        self.once_at(1, self.identity_aba)
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assert_unpublished()

    def test_foreign_realm_with_identical_bindings_rejects(self):
        other = self.path.with_name("rebuilt.sqlite")
        initialize(other, self.connection)
        self.once_at(1, lambda: os.replace(other, self.path))
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assert_unpublished()

    def test_same_subject_reassigned_to_different_local_actor_rejects(self):
        self.store.set_membership("admin", "alpha", "reader", role="developer", state="active", expected_revision=1)
        def reassign():
            with sqlite3.connect(self.path) as db:
                simulate_identity(db, "UPDATE principals SET subject='detached' WHERE id='developer'")
                simulate_identity(db, "UPDATE principals SET subject='developer' WHERE id='reader'")
        self.once_at(1, reassign)
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assert_unpublished()

    def test_role_reduction_then_restore_does_not_revive_flow(self):
        def change():
            self.store.set_membership("admin", "alpha", "developer", role="viewer", state="active", expected_revision=1)
            self.store.set_membership("admin", "alpha", "developer", role="developer", state="active", expected_revision=2)
        self.once_at(1, change)
        with self.assertRaises(PolicyError) as error:
            self.refresh()
        self.assertEqual((error.exception.status, error.exception.code), (409, "refresh_changed"))
        self.assertEqual(len(self.fake.calls), 1)
        self.assert_unpublished()

    def test_revoked_membership_synthetic_regrant_does_not_revive_flow(self):
        def change():
            self.store.set_membership("admin", "alpha", "developer", role="developer", state="revoked", expected_revision=1)
            # Ordinary membership edits do NOT reactivate a revoked record.
            with self.assertRaises(PolicyError):
                self.store.set_membership("admin", "alpha", "developer", role="developer", state="active", expected_revision=2)
            # Disposable future-regrant simulation only, retaining monotonic revision.
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE memberships SET state='active',revision=revision+1 WHERE workspace_id='alpha' AND principal_id='developer'")
        self.once_at(2, change)
        with self.assertRaises(PolicyError) as error:
            self.refresh()
        self.assertEqual(error.exception.code, "refresh_changed")
        self.assert_unpublished()

    def test_both_module_disable_restore_cycles_reject(self):
        for key in ("pipelines", "pipeline_connections"):
            with self.subTest(module=key):
                # New candidate captures current revisions, not a process-global pin.
                self.fake.calls.clear()
                revision = self.store.get_module("admin", "alpha", key).revision
                def change():
                    for offset, state in enumerate(("disabling", "disabled", "enabled")):
                        self.store.set_module_state("admin", "alpha", key, state=state, expected_revision=revision+offset)
                self.once_at(1, change)
                with self.assertRaises(PolicyError) as error:
                    self.refresh()
                self.assertEqual(error.exception.code, "refresh_changed")
                self.assertEqual(len(self.fake.calls), 1)
                self.assert_unpublished()

    def test_workspace_suspend_restore_rejects(self):
        def change():
            self.store.set_workspace_state("admin", "alpha", state="suspended", expected_revision=1)
            self.store.set_workspace_state("admin", "alpha", state="active", expected_revision=2)
        self.once_at(2, change)
        with self.assertRaises(PolicyError) as error:
            self.refresh()
        self.assertEqual(error.exception.code, "refresh_changed")
        self.assert_unpublished()

    def test_unrelated_identity_and_membership_changes_do_not_cancel(self):
        def change():
            self.identity_aba(actor="other")
            self.store.set_membership("admin", "alpha", "reader", role="developer", state="active", expected_revision=1)
        self.once_at(1, change)
        self.assertEqual(self.refresh()["revision"], 2)

    def test_new_refresh_captures_current_generation_not_old_flow_pin(self):
        self.once_at(1, self.identity_aba)
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.fake.hook = lambda: None
        self.assertEqual(self.refresh()["revision"], 2)
        self.assertEqual(len([e for e in self.store.audit_events("admin", "alpha") if e.action == "resource.update"]), 1)

    def test_writer_lock_wait_rechecks_identity_before_final_publish(self):
        at_last_response, release_response, begin_write = Event(), Event(), Event()
        def hold_response():
            if len(self.fake.calls) == 2:
                at_last_response.set()
                if not release_response.wait(5):
                    raise RuntimeError("test timeout")
        self.fake.hook = hold_response
        original = self.store._connect
        def connect():
            db = original()
            if current_thread().name.startswith("flow-reader"):
                db.set_trace_callback(lambda sql: begin_write.set() if sql == "BEGIN IMMEDIATE" else None)
            return db
        with patch.object(self.store, "_connect", side_effect=connect), ThreadPoolExecutor(max_workers=1, thread_name_prefix="flow-reader") as pool:
            future = pool.submit(self.refresh)
            try:
                self.assertTrue(at_last_response.wait(5))
                with sqlite3.connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.identity_aba(db)
                    release_response.set()
                    self.assertTrue(begin_write.wait(5))
                    db.commit()
                with self.assertRaises(AuthenticationError):
                    future.result(timeout=5)
            finally:
                release_response.set()
        self.assert_unpublished()

    def test_two_refreshes_compete_one_snapshot_and_one_audit(self):
        barrier = Barrier(2, timeout=5)
        def transport():
            fake = Fake()
            fake.hook = lambda: barrier.wait() if len(fake.calls) == 2 else None
            return fake
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.refresh, self.make_sync(transport())) for _ in range(2)]
            results = []
            for future in futures:
                try:
                    results.append(future.result(timeout=10)["revision"])
                except PolicyError as error:
                    results.append(error.code)
        self.assertCountEqual(results, [2, "revision_mismatch"])
        self.assertEqual(self.record().revision, 2)
        self.assertEqual(len(self.store.audit_events("admin", "alpha")), 1)

    def test_assertion_expiry_after_persist_rolls_back_snapshot_and_audit(self):
        original = self.store._persist
        def persist(*args, **kwargs):
            original(*args, **kwargs)
            self.now = NOW + timedelta(minutes=10)
        with patch.object(self.store, "_persist", side_effect=persist):
            with self.assertRaises(AuthenticationError):
                self.refresh()
        self.assert_unpublished()

    def test_failed_audit_insert_rolls_back_snapshot(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER fail_pipeline_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic-failure'); END")
        with self.assertRaises(StorageError) as error:
            self.refresh()
        self.assertNotIn("synthetic-failure", str(error.exception))
        self.assert_unpublished()

    def test_identity_mutation_service_and_sql_gates_still_closed(self):
        with self.assertRaises(PolicyError) as error:
            self.store.set_principal("admin", "developer", active=False)
        self.assertEqual(error.exception.code, "identity_transition_unavailable")
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE principals SET active=0 WHERE id='developer'")
        self.assertEqual(self.refresh()["revision"], 2)


if __name__ == "__main__":
    unittest.main()
