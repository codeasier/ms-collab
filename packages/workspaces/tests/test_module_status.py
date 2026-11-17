"""W08 minimal member lifecycle, independent of resource/config authority."""
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.workspaces import ModuleStatus, PolicyError, SQLiteWorkspaceService
from packages.workspaces.tests.test_service import fixture
from packages.workspaces.tests.identity_fixture import simulate_identity


class ModuleStatusTests(unittest.TestCase):
    def build(self, kind):
        core = fixture()
        if kind == 'memory':
            return core
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        return SQLiteWorkspaceService.initialize(Path(directory.name) / 'policy.sqlite',
            principals=tuple(core._principals.values()), workspaces=tuple(core._workspaces.values()),
            memberships=tuple(core._memberships.values()), modules=tuple(core._modules.values()),
            resources=tuple(core._resources.values()), operators=core._operators)

    def denied(self, status, code, operation, *args, **kwargs):
        with self.assertRaises(PolicyError) as caught:
            operation(*args, **kwargs)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))

    def test_projection_is_frozen_exact_allowlist_and_validated(self):
        value = ModuleStatus('alpha', 'board', 'disabled', 3)
        self.assertEqual(asdict(value), {'workspace_id':'alpha', 'key':'board', 'state':'disabled', 'revision':3})
        with self.assertRaises(FrozenInstanceError):
            value.state = 'enabled'
        for change in ({'workspace_id':'bad workspace'}, {'key':''}, {'state':'unknown'}, {'revision':0}, {'revision':True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(value, **change)

    def test_viewer_developer_admin_see_all_states_but_disabled_data_stays_denied(self):
        for kind in ('memory', 'sqlite'):
            store = self.build(kind)
            store.set_membership('alpha_admin', 'alpha', 'multi', role='developer', state='active', expected_revision=1)
            for state, revision in (('enabled',1), ('disabling',2), ('disabled',3), ('enabled',4)):
                if revision != 1:
                    store.set_module_state('alpha_admin', 'alpha', 'board', state=state, expected_revision=revision-1)
                for actor in ('viewer', 'multi', 'alpha_admin'):
                    with self.subTest(kind=kind, state=state, actor=actor):
                        self.assertEqual(asdict(store.get_module_status(actor, 'alpha', 'board')),
                            {'workspace_id':'alpha','key':'board','state':state,'revision':revision})
                        if state != 'enabled':
                            self.denied(409, 'module_unavailable', store.read_resource, actor, 'alpha', 'board', 'same')
                            self.denied(409, 'module_unavailable', store.update_resource, actor, 'alpha', 'board', 'same',
                                        text='not written', expected_revision=1)
                self.assertEqual(store.get_module('alpha_admin', 'alpha', 'board').state, state)
            self.assertEqual(store.read_resource('viewer', 'alpha', 'board', 'same').text, 'Alpha data')

    def test_three_spaces_same_key_are_exact_and_shared_is_not_implicit(self):
        for kind in ('memory', 'sqlite'):
            store = self.build(kind)
            store.set_module_state('alpha_admin', 'alpha', 'board', state='disabling', expected_revision=1)
            for actor, workspace, state, revision in (
                    ('viewer','alpha','disabling',2), ('multi','beta','enabled',1), ('viewer','commons','enabled',1)):
                self.assertEqual(asdict(store.get_module_status(actor, workspace, 'board')),
                    {'workspace_id':workspace,'key':'board','state':state,'revision':revision})
            for workspace in ('beta', 'missing'):
                self.denied(404, 'not_found', store.get_module_status, 'viewer', workspace, 'board')
            self.denied(404, 'not_found', store.get_module_status, 'outsider', 'commons', 'board')
            self.denied(404, 'not_found', store.get_module_status, 'viewer', 'alpha', 'missing')

    def test_current_identity_membership_precede_workspace_and_module_existence(self):
        for kind in ('memory', 'sqlite'):
            store = self.build(kind)
            store.set_workspace_state('operator', 'alpha', state='suspended', expected_revision=1)
            for actor in (None, 'missing', 'disabled'):
                for key in ('board', 'missing'):
                    self.denied(401, 'unauthenticated', store.get_module_status, actor, 'alpha', key)
            for actor in ('outsider', 'operator', 'beta_admin'):
                for workspace in ('alpha', 'missing'):
                    for key in ('board', 'missing'):
                        self.denied(404, 'not_found', store.get_module_status, actor, workspace, key)
            for key in ('board', 'missing'):
                self.denied(409, 'workspace_unavailable', store.get_module_status, 'viewer', 'alpha', key)

    def test_archive_status_is_read_only_and_recovery_does_not_restore_revoked_member(self):
        for kind in ('memory', 'sqlite'):
            store = self.build(kind)
            store.set_membership('alpha_admin', 'alpha', 'multi', role='viewer', state='revoked', expected_revision=1)
            store.set_workspace_state('operator', 'alpha', state='suspended', expected_revision=1)
            store.set_workspace_state('operator', 'alpha', state='active', expected_revision=2)
            self.denied(404, 'not_found', store.get_module_status, 'multi', 'alpha', 'board')
            store.set_workspace_state('operator', 'alpha', state='archived', expected_revision=3)
            self.assertEqual(store.get_module_status('viewer', 'alpha', 'board').state, 'enabled')
            self.assertEqual(store.read_resource('viewer', 'alpha', 'board', 'same').text, 'Alpha data')
            self.denied(409, 'workspace_unavailable', store.update_resource, 'alpha_admin', 'alpha', 'board', 'same',
                        text='no', expected_revision=1)
            self.denied(409, 'workspace_unavailable', store.set_module_state, 'alpha_admin', 'alpha', 'board',
                        state='disabling', expected_revision=1)

    def test_empty_capabilities_are_stored_status_not_effective_permission(self):
        for kind in ('memory', 'sqlite'):
            store = self.build(kind)
            store.install_module('alpha_admin', 'alpha', 'empty', capabilities=())
            for actor in ('viewer', 'alpha_admin'):
                self.assertEqual(store.get_module_status(actor, 'alpha', 'empty').state, 'disabled')
            store.set_module_state('alpha_admin', 'alpha', 'empty', state='enabled', expected_revision=1)
            self.assertEqual(store.get_module_status('viewer', 'alpha', 'empty').state, 'enabled')
            for action in ('read', 'write'):
                self.denied(403, 'forbidden', store.authorize, 'alpha_admin', 'alpha', 'empty', action)

    def test_status_does_not_widen_existing_getter_list_or_mutation(self):
        for kind in ('memory', 'sqlite'):
            store = self.build(kind)
            store.set_module_state('alpha_admin', 'alpha', 'board', state='disabling', expected_revision=1)
            self.assertEqual(store.get_module_status('viewer', 'alpha', 'board').state, 'disabling')
            self.denied(409, 'module_unavailable', store.get_module, 'viewer', 'alpha', 'board')
            self.denied(403, 'forbidden', store.list_modules, 'viewer', 'alpha')
            self.denied(403, 'forbidden', store.set_module_state, 'viewer', 'alpha', 'board',
                        state='disabled', expected_revision=2)
            self.assertEqual({module.key for module in store.list_modules('alpha_admin', 'alpha')}, {'board','notes'})

    def test_sqlite_status_has_no_persist_audit_ddl_or_receipt_dependency(self):
        store = self.build('sqlite')
        def snapshot():
            with sqlite3.connect(store._path) as db:
                return tuple(db.iterdump())
        before = snapshot(); traces = []; original = store._connect
        def traced():
            db = original(); db.set_trace_callback(traces.append); return db
        with patch.object(store, '_connect', side_effect=traced), \
                patch.object(store, '_persist', side_effect=AssertionError('read must not persist')):
            self.assertEqual(store.get_module_status('viewer', 'alpha', 'board').state, 'enabled')
        self.assertEqual(snapshot(), before)
        self.assertFalse(any(re.match(r'\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b', q, re.I) for q in traces))
        self.assertFalse(any('directory_cursor_' in q for q in traces))

    def test_sqlite_true_identity_aba_and_foreign_realm_grants_do_not_restore_status(self):
        for change in ('identity', 'realm'):
            store = self.build('sqlite')
            self.assertEqual(store.get_module_status('viewer', 'alpha', 'board').state, 'enabled')
            with sqlite3.connect(store._path) as db:
                if change == 'identity':
                    simulate_identity(db, "UPDATE principals SET subject='other-subject' WHERE id='viewer'")
                    simulate_identity(db, "UPDATE principals SET subject='viewer' WHERE id='viewer'")
                    self.assertEqual(db.execute("SELECT generation FROM principals WHERE id='viewer'").fetchone(), (3,))
                else:
                    db.execute("UPDATE membership_authority SET realm=? WHERE principal_id='viewer'", ('0'*32,))
                self.assertEqual(db.execute("SELECT revision FROM memberships WHERE principal_id='viewer' AND workspace_id='alpha'").fetchone(), (1,))
            self.denied(404, 'not_found', store.get_module_status, 'viewer', 'alpha', 'board')
