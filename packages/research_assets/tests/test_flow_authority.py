"""Synthetic in-flight authority changes, not an enabled identity mutation API."""
import sqlite3
from threading import Event, Thread
import unittest
from unittest.mock import patch

from packages.identity import AuthenticationError
from packages.research_assets import AssetScope, provision_catalog
from packages.research_assets.tests import test_assets as fixtures
from packages.workspaces import PolicyError, SQLiteWorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity


class FlowAuthorityTests(unittest.TestCase):
    # Reuse setup/helpers without inheriting or rediscovering its existing tests.
    setUp = fixtures.ServiceTests.setUp
    refresh = fixtures.ServiceTests.refresh
    sql = fixtures.ServiceTests.sql

    def retained(self):
        return (self.sql('SELECT * FROM research_catalog ORDER BY workspace_id,catalog_id'),
                self.sql('SELECT * FROM research_asset ORDER BY workspace_id,catalog_id,id'),
                self.sql("SELECT * FROM audit WHERE action='resource.update' ORDER BY sequence"))

    def identity_aba(self, field='subject', actor='admin'):
        with sqlite3.connect(self.path) as conn:
            original = conn.execute(f'SELECT {field} FROM principals WHERE id=?', (actor,)).fetchone()[0]
            other = 0 if field == 'active' else 'synthetic-temporary-binding'
            simulate_identity(conn, f'UPDATE principals SET {field}=? WHERE id=?', (other, actor))
            simulate_identity(conn, f'UPDATE principals SET {field}=? WHERE id=?', (original, actor))

    def deny_after(self, mutate, *, source='primary', token='synthetic-admin'):
        self.refresh(); old = self.retained(); self.provider.calls.clear()
        self.provider.hook = lambda scope, item: mutate() if item.id == source else None
        with patch('packages.research_assets.application.prepare_batch', wraps=fixtures.prepare_batch) as projection:
            with self.assertRaises(PolicyError) as raised:
                self.refresh(2, token=token)
        self.assertEqual(projection.call_count, 0 if source == 'primary' else 1)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, 'asset_authority_changed'))
        self.assertEqual([x[1] for x in self.provider.calls], ['primary'] if source == 'primary' else ['primary', 'secondary'])
        self.assertEqual(self.retained(), old)

    def test_subject_aba_after_first_source_stops_next_source_and_discards_result(self):
        self.deny_after(self.identity_aba)

    def test_subject_aba_after_last_source_cannot_publish_or_append_audit(self):
        self.deny_after(self.identity_aba, source='secondary')

    def test_disable_reenable_during_source_does_not_restore_old_flow(self):
        self.deny_after(lambda: self.identity_aba('active'))

    def test_issuer_aba_during_source_does_not_restore_old_flow(self):
        self.deny_after(lambda: self.identity_aba('issuer'))

    def test_preflight_pin_checked_before_even_first_external_call(self):
        old = self.retained(); original = self.sync._authority._authenticated_transaction
        calls = 0
        def interpose(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.identity_aba()
            return original(*args, **kwargs)
        with patch.object(self.sync._authority, '_authenticated_transaction', side_effect=interpose):
            with self.assertRaisesRegex(PolicyError, 'asset_authority_changed'):
                self.refresh()
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.retained(), old)

    def test_identity_change_after_all_external_results_before_writer_is_rejected(self):
        old = self.retained(); original = self.sync._authority._authenticated_transaction
        def interpose(*args, **kwargs):
            if kwargs.get('write'):
                self.identity_aba()
            return original(*args, **kwargs)
        with patch.object(self.sync._authority, '_authenticated_transaction', side_effect=interpose):
            with self.assertRaisesRegex(PolicyError, 'asset_authority_changed'):
                self.refresh()
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(self.retained(), old)

    def test_nonreturned_subject_change_still_uses_current_authentication_error(self):
        old = self.retained()
        def rebind(*_):
            with sqlite3.connect(self.path) as conn:
                simulate_identity(conn, 'UPDATE principals SET subject=? WHERE id=?', ('different', 'admin'))
        self.provider.hook = rebind
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.retained(), old)

    def test_membership_revoke_regrant_role_aba_cannot_reuse_result(self):
        def change():
            self.store.set_membership('admin', 'alpha', 'developer', role='viewer', state='active', expected_revision=1)
            self.store.set_membership('admin', 'alpha', 'developer', role='developer', state='active', expected_revision=2)
        self.deny_after(change, token='synthetic-developer')

    def module_cycle(self, module):
        for revision, state in enumerate(('disabling', 'disabled', 'enabled'), 1):
            self.store.set_module_state('admin', 'alpha', module, state=state, expected_revision=revision)

    def test_read_module_disable_reenable_cannot_reuse_result(self):
        self.deny_after(lambda: self.module_cycle('research_assets'))

    def test_sync_module_disable_reenable_cannot_reuse_result(self):
        self.deny_after(lambda: self.module_cycle('research_assets_sync'))

    def test_workspace_suspend_resume_cannot_reuse_result(self):
        self.sql("INSERT INTO operators VALUES('admin')")  # Synthetic trusted platform setup only.
        def change():
            self.store.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
            self.store.set_workspace_state('admin', 'alpha', state='active', expected_revision=2)
        self.deny_after(change)

    def replacement_store(self):
        records = self.store._transaction(lambda p: {
            'principals': tuple(p._principals.values()), 'workspaces': tuple(p._workspaces.values()),
            'memberships': tuple(p._memberships.values()), 'modules': tuple(p._modules.values()),
        }, write=False)
        other = SQLiteWorkspaceService.initialize(self.path.with_name('independent-realm.sqlite'), **records)
        for workspace in ('alpha', 'beta', 'commons'):
            provision_catalog(other, fixtures.binding(AssetScope(workspace, 'engineering')))
        return other

    def test_identical_ids_sources_and_revision_in_new_realm_do_not_reuse_flow(self):
        other = self.replacement_store(); old = self.retained()
        self.assertNotEqual(self.store._transaction(lambda p:p._realm, write=False),
                            other._transaction(lambda p:p._realm, write=False))
        self.provider.hook = lambda *_: setattr(self.sync._authority, '_store', other)
        with self.assertRaisesRegex(PolicyError, 'asset_authority_changed'):
            self.refresh()
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.retained(), old)
        self.assertEqual(other._transaction(lambda p,c:c.execute('SELECT count(*) FROM research_asset').fetchone()[0], write=False, with_connection=True), 0)

    def test_unrelated_identity_or_other_workspace_revision_does_not_cancel_flow(self):
        def change(_, source):
            if source.id == 'primary':
                self.identity_aba(actor='other')
                self.store.set_membership('admin', 'beta', 'other', role='developer', state='active', expected_revision=1)
        self.provider.hook = change
        result = self.refresh()
        self.assertEqual((result['revision'], result['count']), (2, 1))
        self.assertEqual(len(self.provider.calls), 2)

    def test_final_writer_waits_then_sees_committed_aba_not_old_read_snapshot(self):
        # No fake identity API: only the test simulator mutates a disposable DB.
        # A separate writer holds an uncommitted ABA while post-I/O reads still
        # see the old snapshot. The final BEGIN IMMEDIATE must see the new pin.
        reserved, release, writer_waiting = Event(), Event(), Event()
        failures = []
        def external_writer():
            try:
                with sqlite3.connect(self.path, timeout=5) as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    simulate_identity(conn, 'UPDATE principals SET subject=? WHERE id=?', ('temporary', 'admin'))
                    simulate_identity(conn, 'UPDATE principals SET subject=? WHERE id=?', ('admin', 'admin'))
                    reserved.set()
                    if not release.wait(5):
                        raise AssertionError('test writer release missing')
            except BaseException as error:
                failures.append(error); reserved.set()
        writer = Thread(target=external_writer)
        def last_source(_, source):
            if source.id == 'secondary':
                writer.start()
                self.assertTrue(reserved.wait(5))
                self.assertEqual(failures, [])
        self.provider.hook = last_source
        original = self.store._connect
        def connect():
            conn = original()
            conn.set_trace_callback(lambda sql: writer_waiting.set() if sql == 'BEGIN IMMEDIATE' else None)
            return conn
        result = []
        def run_refresh():
            try:
                self.refresh()
                result.append('unexpected success')
            except PolicyError as error:
                result.append((error.status, error.code))
            except BaseException as error:
                failures.append(error)
        runner = Thread(target=run_refresh)
        old = self.retained()
        try:
            with patch.object(self.store, '_connect', side_effect=connect):
                runner.start()
                self.assertTrue(writer_waiting.wait(5))
                self.assertTrue(runner.is_alive())
                release.set()
                runner.join(5); writer.join(5)
            self.assertFalse(runner.is_alive()); self.assertFalse(writer.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(result, [(409, 'asset_authority_changed')])
            self.assertEqual(self.retained(), old)
        finally:
            release.set()
            if runner.ident is not None: runner.join(5)
            if writer.ident is not None: writer.join(5)


if __name__ == '__main__':
    unittest.main()
