# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic core grant ownership regressions, never an identity-transition API.

Directory changes below deliberately use disposable fixtures while preserving
all old grants. They do not establish a supported rebind/recovery procedure.
"""
from contextlib import closing
from copy import deepcopy
from dataclasses import astuple, replace
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from packages.workspaces import Membership, PolicyError, SQLiteWorkspaceService, StorageError, WorkspaceService
from packages.workspaces.authority import capture_authority
from packages.workspaces.sqlite import _APPLICATION_ID, _SCHEMA_V1, _SCHEMA_V2
from packages.workspaces.tests.identity_fixture import simulate_identity
from packages.workspaces.tests.test_authority import snapshot
from packages.workspaces.tests.test_service import fixture
from packages.workspaces.tests.test_sqlite import bootstrap


def policy_state(policy):
    """Include pins in atomicity checks, not just visible membership rows."""
    return deepcopy((policy._principals, policy._workspaces, policy._memberships,
                     policy._modules, policy._resources, policy._operators,
                     policy._membership_authority, policy._operator_authority,
                     policy._epoch, policy._realm, policy._audit))


def legacy_policy(path, version):
    """Write exact old schemas explicitly; NEVER call today's grant writer."""
    source = fixture(two_admins=True)
    source.set_membership('alpha_admin', 'alpha', 'viewer', role='viewer',
                          state='revoked', expected_revision=1)
    source.update_resource('alpha_admin', 'alpha', 'board', 'same',
                           text='retained synthetic history', expected_revision=1)
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        for sql in (_SCHEMA_V1 if version == 1 else _SCHEMA_V2):
            db.execute(sql)
        db.execute(f'PRAGMA application_id={_APPLICATION_ID}')
        db.execute(f'PRAGMA user_version={version}')
        if version == 1:
            db.executemany('INSERT INTO principals VALUES(?,?,?,?)',
                           (astuple(p)[:4] for p in source._principals.values()))
            db.execute('INSERT INTO metadata VALUES(1,?)', (source._epoch,))
        else:
            db.executemany('INSERT INTO principals VALUES(?,?,?,?,?,?)',
                           (astuple(p) for p in source._principals.values()))
            db.execute('INSERT INTO metadata VALUES(1,?,?)', (source._epoch, source._realm))
        db.executemany('INSERT INTO operators VALUES(?)', ((p,) for p in source._operators))
        db.executemany('INSERT INTO workspaces VALUES(?,?,?,?,?)',
                       (astuple(w) for w in source._workspaces.values()))
        db.executemany('INSERT INTO memberships VALUES(?,?,?,?,?)',
                       (astuple(m) for m in source._memberships.values()))
        db.executemany('INSERT INTO modules VALUES(?,?,?,?,?,?)',
                       ((m.workspace_id, m.key, 'read' in m.capabilities,
                         'write' in m.capabilities, m.state, m.revision)
                        for m in source._modules.values()))
        db.executemany('INSERT INTO resources VALUES(?,?,?,?,?)',
                       (astuple(r) for r in source._resources.values()))
        db.executemany('INSERT INTO audit VALUES(?,?,?,?,?,?)',
                       (astuple(e) for e in source._audit))
        if version == 2:
            db.execute('INSERT INTO authority_migrations VALUES(1,0,2,?,?)',
                       (len(source._principals), source._realm))
        db.execute('CREATE TABLE synthetic_module (value TEXT) STRICT')
        db.execute("INSERT INTO synthetic_module VALUES('retained')")
        db.commit()
    return source


class MembershipAuthorityContract:
    """The same ownership contract against memory and persistent snapshots."""
    persistent = False

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'policy.sqlite'
        self.store = bootstrap(self.path, two_admins=True) if self.persistent else fixture(two_admins=True)

    def inspect(self, callback):
        if self.persistent:
            return self.store._transaction(callback, write=False)
        with self.store._lock:
            return callback(self.store)

    def mutate(self, callback):
        if self.persistent:
            return self.store._transaction(callback, write=True)
        with self.store._lock:
            return callback(self.store)

    def state(self):
        return snapshot(self.path) if self.persistent else self.inspect(policy_state)

    def pins(self):
        return self.inspect(lambda p: deepcopy((p._membership_authority, p._operator_authority)))

    def fixture_pin(self, actor, grant, *, workspace=None):
        """Corrupt a disposable fixture directly, never via the normal writer."""
        if not self.persistent:
            target = self.store._operator_authority if workspace is None else self.store._membership_authority
            target[actor if workspace is None else (workspace, actor)] = deepcopy(grant)
            return
        binding = grant['principals'][actor] if grant is not None else None
        values = ((grant['realm'], binding['issuer'], binding['subject'], binding['generation'])
                  if grant is not None else (None, None, None, None))
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            if workspace is None:
                db.execute('UPDATE operator_authority SET realm=?,issuer=?,subject=?,generation=? WHERE principal_id=?',
                           (*values, actor))
            else:
                db.execute('UPDATE membership_authority SET realm=?,issuer=?,subject=?,generation=? '
                           'WHERE workspace_id=? AND principal_id=?', (*values, workspace, actor))

    def change_identity(self, actor, **changes):
        # Solely synthetic future-directory changes, intentionally WITHOUT any
        # core regrant. Production set_principal and SQL guards remain closed.
        if self.persistent:
            assignments = ','.join(f'{field}=?' for field in changes)
            with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
                simulate_identity(db, f'UPDATE principals SET {assignments} WHERE id=?',
                                  (*changes.values(), actor))
        else:
            def change(policy):
                prior = policy._principals[actor]
                policy._epoch += 1
                policy._principals[actor] = replace(prior, **changes,
                    generation=prior.generation + 1, authority_epoch=policy._epoch)
            self.mutate(change)

    def deny(self, status, code, operation):
        before = self.state()
        with self.assertRaises(PolicyError) as caught:
            operation()
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        self.assertEqual(self.state(), before)

    def test_fresh_bootstrap_captures_exact_membership_and_operator_owners(self):
        def check(policy):
            self.assertEqual(set(policy._membership_authority), set(policy._memberships))
            self.assertEqual(set(policy._operator_authority), set(policy._operators))
            for (_, actor), grant in policy._membership_authority.items():
                self.assertEqual(grant, capture_authority(policy, (actor,)))
            for actor, grant in policy._operator_authority.items():
                self.assertEqual(grant, capture_authority(policy, (actor,)))
            self.assertTrue(policy._membership_current('viewer', 'alpha'))
            self.assertFalse(policy._membership_current('disabled', 'alpha'))
            self.assertTrue(policy._operator_current('operator'))
            self.assertFalse(policy._operator_current('alpha_admin'))
        self.inspect(check)

    def test_stale_member_hides_all_scopes_without_changing_membership_revision(self):
        before = self.inspect(lambda p: p._memberships.copy())
        pins = self.pins()
        self.change_identity('viewer', subject='replacement-viewer')
        self.assertEqual(self.store.list_workspaces('viewer'), ())
        for workspace, resource in (('alpha', 'same'), ('commons', 'public')):
            self.deny(404, 'not_found', lambda: self.store.read_resource('viewer', workspace, 'board', resource))
            self.deny(404, 'not_found', lambda: self.store.authorize('viewer', workspace, 'board', 'read'))
        self.deny(404, 'not_found', lambda: self.store.list_resources('viewer', 'alpha', 'board'))
        self.deny(404, 'not_found', lambda: self.store.get_module('viewer', 'alpha', 'board'))
        self.assertEqual(self.inspect(lambda p: p._memberships), before)
        self.assertEqual(self.pins(), pins)
        self.assertEqual(self.store.read_resource('beta_admin', 'beta', 'board', 'same').text, 'Beta data')

    def test_identity_aba_never_restores_old_membership(self):
        pins = self.pins()
        for changes in ({'subject': 'replacement-viewer'}, {'subject': 'viewer'},
                        {'issuer': 'other-synthetic'}, {'issuer': 'synthetic'}):
            self.change_identity('viewer', **changes)
            self.deny(404, 'not_found', lambda: self.store.read_resource('viewer', 'alpha', 'board', 'same'))
        self.assertEqual(self.inspect(lambda p: p._principals['viewer'].generation), 5)
        self.assertEqual(self.pins(), pins)

    def test_disable_then_reenable_does_not_revive_membership(self):
        pins = self.pins()
        self.change_identity('viewer', active=False)
        self.deny(401, 'unauthenticated', lambda: self.store.list_workspaces('viewer'))
        self.change_identity('viewer', active=True)
        self.deny(404, 'not_found', lambda: self.store.read_resource('viewer', 'alpha', 'board', 'same'))
        self.assertEqual(self.store.list_workspaces('viewer'), ())
        self.assertEqual(self.pins(), pins)

    def test_foreign_realm_pins_never_authorize_same_local_ids(self):
        realm = uuid4().hex
        members, operators = self.pins()
        self.fixture_pin('viewer', members[('alpha', 'viewer')] | {'realm': realm}, workspace='alpha')
        self.fixture_pin('operator', operators['operator'] | {'realm': realm})
        pins = self.pins()
        self.deny(404, 'not_found', lambda: self.store.read_resource('viewer', 'alpha', 'board', 'same'))
        self.deny(403, 'forbidden', lambda: self.store.list_control_workspaces('operator'))
        self.assertEqual([w.id for w in self.store.list_workspaces('viewer')], ['commons'])
        self.assertEqual(self.pins(), pins)

    def test_pin_for_other_principal_cannot_be_relabelled_as_current_member(self):
        grant = self.pins()[0][('alpha', 'viewer')]
        donor = self.inspect(lambda p: p._principals['alpha_admin'])
        grant['principals']['viewer'] = {'issuer': donor.issuer, 'subject': donor.subject,
                                          'generation': donor.generation}
        self.fixture_pin('viewer', grant, workspace='alpha')
        self.deny(404, 'not_found', lambda: self.store.read_resource('viewer', 'alpha', 'board', 'same'))
        self.assertTrue(self.inspect(lambda p: p._membership_current('alpha_admin', 'alpha')))

    def test_stale_operator_loses_control_but_never_gains_private_membership(self):
        pins = self.pins()
        self.assertEqual(len(self.store.list_control_workspaces('operator')), 3)
        self.deny(404, 'not_found', lambda: self.store.read_resource('operator', 'alpha', 'board', 'same'))
        for subject in ('replacement-operator', 'operator'):
            self.change_identity('operator', subject=subject)
            self.assertFalse(self.inspect(lambda p: p._operator_current('operator')))
            self.deny(403, 'forbidden', lambda: self.store.list_control_workspaces('operator'))
            self.deny(403, 'forbidden', lambda: self.store.set_workspace_state(
                'operator', 'alpha', state='suspended', expected_revision=1))
            self.deny(403, 'forbidden', lambda: self.store.create_workspace(
                'operator', name='Not granted', kind='product', initial_admin='outsider'))
        self.assertEqual(self.pins(), pins)

    def test_last_current_admin_is_protected_despite_other_active_admin_row(self):
        self.change_identity('multi', subject='replacement-multi')
        self.assertEqual(self.inspect(lambda p: [m.principal_id for m in p._eligible_admins('alpha')]), ['alpha_admin'])
        for role, state in (('viewer', 'active'), ('admin', 'revoked')):
            self.deny(409, 'last_admin', lambda: self.store.set_membership(
                'alpha_admin', 'alpha', 'alpha_admin', role=role, state=state, expected_revision=1))
        self.deny(404, 'not_found', lambda: self.store.list_memberships('multi', 'alpha'))

    def test_zero_current_admins_load_without_blocking_unrelated_workspace(self):
        self.change_identity('alpha_admin', subject='replacement-alpha-admin')
        self.change_identity('multi', subject='replacement-multi')
        if self.persistent:
            self.store = SQLiteWorkspaceService(self.path)
        self.assertEqual(self.inspect(lambda p: p._eligible_admins('alpha')), [])
        for actor in ('alpha_admin', 'multi'):
            self.deny(404, 'not_found', lambda: self.store.list_memberships(actor, 'alpha'))
            self.deny(404, 'not_found', lambda: self.store.update_resource(
                actor, 'alpha', 'board', 'same', text='denied', expected_revision=1))
        self.assertEqual(self.store.update_resource('beta_admin', 'beta', 'board', 'same',
                                                    text='still available', expected_revision=1).revision, 2)
        self.assertEqual(self.inspect(lambda p: p._memberships[('alpha', 'alpha_admin')].revision), 1)

    def test_ordinary_edits_and_transfer_preserve_exact_pins(self):
        pins = self.pins()
        with patch('packages.workspaces.service.capture_authority', side_effect=AssertionError('ordinary writer recaptured authority')):
            self.store.set_membership('alpha_admin', 'alpha', 'viewer', role='developer',
                                      state='active', expected_revision=1)
            self.store.update_resource('viewer', 'alpha', 'board', 'same', text='current write', expected_revision=1)
            self.store.transfer_admin('alpha_admin', 'alpha', 'viewer', expected_revision=1, replacement_revision=2)
        self.assertEqual(self.pins(), pins)
        self.assertTrue(self.inspect(lambda p: p._membership_current('viewer', 'alpha')))

    def test_stale_member_can_only_be_revoked_without_rebinding(self):
        self.change_identity('viewer', subject='replacement-viewer')
        pins = self.pins()
        for role in ('viewer', 'developer', 'admin'):
            self.deny(409, 'membership_authority_unavailable', lambda: self.store.set_membership(
                'alpha_admin', 'alpha', 'viewer', role=role, state='active', expected_revision=1))
        revoked = self.store.set_membership('alpha_admin', 'alpha', 'viewer', role='viewer',
                                            state='revoked', expected_revision=1)
        self.assertEqual((revoked.state, revoked.revision), ('revoked', 2))
        self.assertEqual(self.pins(), pins)
        self.deny(409, 'membership_revoked', lambda: self.store.set_membership(
            'alpha_admin', 'alpha', 'viewer', role='viewer', state='active', expected_revision=2))

    def test_transfer_cannot_adopt_stale_replacement(self):
        self.change_identity('viewer', subject='replacement-viewer')
        self.deny(409, 'replacement_ineligible', lambda: self.store.transfer_admin(
            'alpha_admin', 'alpha', 'viewer', expected_revision=1, replacement_revision=1))

    def test_explicit_new_workspace_and_membership_grants_capture_current_owner(self):
        self.change_identity('outsider', subject='replacement-outsider')
        workspace = self.store.create_workspace('operator', name='Synthetic fresh grant',
                                                kind='product', initial_admin='outsider')
        self.assertTrue(self.inspect(lambda p: p._membership_current('outsider', workspace.id)))
        self.mutate(lambda p: p._grant_membership(Membership('alpha', 'outsider', 'viewer')))
        self.assertEqual(self.inspect(lambda p: p._membership_authority[('alpha', 'outsider')]),
                         self.inspect(lambda p: capture_authority(p, ('outsider',))))
        self.assertEqual(self.store.read_resource('outsider', 'alpha', 'board', 'same').text, 'Alpha data')

    def test_current_revoked_member_can_rejoin_without_recapture(self):
        pins = self.pins()
        self.store.set_membership('alpha_admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=1)
        self.assertTrue(self.inspect(lambda p: p._membership_authority_current('viewer', 'alpha')))
        self.assertFalse(self.inspect(lambda p: p._membership_current('viewer', 'alpha')))
        with patch('packages.workspaces.service.capture_authority', side_effect=AssertionError('unexpected repin')):
            self.mutate(lambda p: p._grant_membership(Membership('alpha', 'viewer', 'developer', revision=3)))
        self.assertTrue(self.inspect(lambda p: p._membership_current('viewer', 'alpha')))
        self.assertEqual(self.pins(), pins)

    def test_revoked_stale_and_legacy_members_cannot_rejoin(self):
        self.store.set_membership('alpha_admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=1)
        self.change_identity('viewer', subject='replacement-viewer')
        self.deny(409, 'membership_authority_unavailable', lambda: self.mutate(
            lambda p: p._grant_membership(Membership('alpha', 'viewer', 'viewer', revision=3))))
        self.fixture_pin('viewer', None, workspace='alpha')
        self.deny(409, 'membership_authority_unavailable', lambda: self.mutate(
            lambda p: p._grant_membership(Membership('alpha', 'viewer', 'viewer', revision=3))))

    def test_legacy_tombstone_cannot_be_adopted_by_edit_or_transfer(self):
        self.fixture_pin('viewer', None, workspace='alpha')
        self.deny(404, 'not_found', lambda: self.store.read_resource('viewer', 'alpha', 'board', 'same'))
        self.deny(409, 'membership_authority_unavailable', lambda: self.store.set_membership(
            'alpha_admin', 'alpha', 'viewer', role='viewer', state='active', expected_revision=1))
        self.deny(409, 'replacement_ineligible', lambda: self.store.transfer_admin(
            'alpha_admin', 'alpha', 'viewer', expected_revision=1, replacement_revision=1))
        self.store.set_membership('alpha_admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=1)
        self.assertIsNone(self.inspect(lambda p: p._membership_authority[('alpha', 'viewer')]))

    def test_runtime_identity_entry_stays_closed_after_ownership_upgrade(self):
        for options in ({}, {'enabled': True}, {'force': True}, {'active': False}, {'generation': 2}):
            self.deny(503, 'identity_transition_unavailable', lambda: self.store.set_principal(
                'operator', 'viewer', **options))


class MemoryMembershipAuthorityTests(MembershipAuthorityContract, unittest.TestCase):
    def test_strict_persisted_constructor_never_bootstraps_missing_or_legacy_pins(self):
        source = self.store
        arguments = dict(principals=tuple(source._principals.values()),
                         workspaces=tuple(source._workspaces.values()),
                         memberships=tuple(source._memberships.values()),
                         modules=tuple(source._modules.values()), resources=tuple(source._resources.values()),
                         operators=source._operators, realm=source._realm, epoch=source._epoch,
                         membership_authority={key: None for key in source._memberships},
                         operator_authority={actor: None for actor in source._operators})
        with patch('packages.workspaces.service.capture_authority', side_effect=AssertionError('decoder captured authority')):
            loaded = WorkspaceService._from_persisted(**arguments)
            self.assertEqual(loaded._eligible_admins('alpha'), [])
            self.assertEqual(loaded.list_workspaces('alpha_admin'), ())
            for field in ('membership_authority', 'operator_authority'):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    WorkspaceService._from_persisted(**(arguments | {field: {}}))
        self.assertTrue(all(grant is None for grant in loaded._membership_authority.values()))

    def test_malformed_in_memory_grant_fails_closed(self):
        for grant in ({}, {'version': True, 'realm': self.store._realm, 'principals': {}}, None):
            self.store._membership_authority[('alpha', 'viewer')] = deepcopy(grant)
            self.store._operator_authority['operator'] = deepcopy(grant)
            self.deny(404, 'not_found', lambda: self.store.read_resource('viewer', 'alpha', 'board', 'same'))
            self.deny(403, 'forbidden', lambda: self.store.list_control_workspaces('operator'))


class SQLiteMembershipAuthorityTests(MembershipAuthorityContract, unittest.TestCase):
    persistent = True

    def test_reopen_and_ordinary_write_never_call_bootstrap_capture(self):
        pins = self.pins()
        with patch('packages.workspaces.service.capture_authority', side_effect=AssertionError('decoder captured authority')):
            reopened = SQLiteWorkspaceService(self.path)
            reopened.set_membership('alpha_admin', 'alpha', 'viewer', role='developer',
                                    state='active', expected_revision=1)
            reopened.update_resource('viewer', 'alpha', 'board', 'same', text='retained pins', expected_revision=1)
        self.assertEqual(self.pins(), pins)

    def test_persist_refuses_rebinding_or_repair_even_inside_trusted_transaction(self):
        for sidecar in ('member', 'operator'):
            before = self.state()
            def repin(policy):
                grants = policy._membership_authority if sidecar == 'member' else policy._operator_authority
                key = ('alpha', 'viewer') if sidecar == 'member' else 'operator'
                grants[key]['realm'] = uuid4().hex
                policy.update_resource('alpha_admin', 'alpha', 'board', 'same', text='must roll back', expected_revision=1)
            with self.subTest(sidecar=sidecar), self.assertRaises(StorageError) as caught:
                self.mutate(repin)
            self.assertEqual(caught.exception.code, 'invalid_store')
            self.assertEqual(self.state(), before)
        for table in ('membership_authority', 'operator_authority'):
            before = self.state()
            def remove_after_load(policy, connection):
                connection.execute(f'DELETE FROM {table}')
                policy.update_resource('alpha_admin', 'alpha', 'board', 'same', text='must roll back', expected_revision=1)
            with self.subTest(table=table), self.assertRaises(StorageError) as caught:
                self.store._transaction(remove_after_load, write=True, with_connection=True)
            self.assertEqual(caught.exception.code, 'invalid_store')
            self.assertEqual(self.state(), before)

    def test_missing_or_corrupt_sidecars_fail_closed_without_repair(self):
        corruptions = [
            ("DELETE FROM membership_authority WHERE workspace_id='alpha' AND principal_id='viewer'", 'invalid_store'),
            ("DELETE FROM operator_authority WHERE principal_id='operator'", 'invalid_store'),
            ("UPDATE membership_authority SET subject=NULL WHERE principal_id='viewer'", 'invalid_store'),
            ("UPDATE operator_authority SET generation=NULL", 'invalid_store'),
            ("UPDATE membership_authority SET realm='invalid' WHERE principal_id='viewer'", 'invalid_store'),
            ("UPDATE operator_authority SET issuer=''", 'invalid_store'),
            ("UPDATE membership_authority SET generation=0 WHERE principal_id='viewer'", 'invalid_store'),
            ("UPDATE operator_authority SET generation=9007199254740992", 'invalid_store'),
            ("INSERT INTO membership_authority VALUES('alpha','outsider',NULL,NULL,NULL,NULL)", 'invalid_store'),
            ("INSERT INTO operator_authority VALUES('outsider',NULL,NULL,NULL,NULL)", 'invalid_store'),
            ('DROP TABLE membership_authority', 'unsupported_schema'),
            ('DROP TABLE operator_authority', 'unsupported_schema'),
        ]
        for number, (sql, code) in enumerate(corruptions):
            path = self.path.with_name(f'corrupt-{number}.sqlite')
            store = bootstrap(path, two_admins=True)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.execute('PRAGMA ignore_check_constraints=ON')
                db.execute(sql)
            before = snapshot(path)
            for action in (lambda: store.list_workspaces('viewer'),
                           lambda: store.update_resource('alpha_admin', 'alpha', 'board', 'same',
                                                         text='must not persist', expected_revision=1)):
                with self.subTest(sql=sql), self.assertRaises(StorageError) as caught:
                    action()
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(snapshot(path), before)

    def test_unknown_sidecar_trigger_or_index_blocks_reopen_and_operations(self):
        extensions = [
            "CREATE TRIGGER untrusted AFTER INSERT ON membership_authority BEGIN "
            "UPDATE membership_authority SET generation=generation+1; END",
            "CREATE INDEX untrusted ON membership_authority(generation)",
            "CREATE TRIGGER untrusted AFTER INSERT ON operator_authority BEGIN "
            "UPDATE operator_authority SET generation=generation+1; END",
            "CREATE INDEX untrusted ON operator_authority(generation)",
        ]
        for number, sql in enumerate(extensions):
            path = self.path.with_name(f'extended-{number}.sqlite')
            store = bootstrap(path, two_admins=True)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.execute(sql)
            before = snapshot(path)
            operations = (lambda: SQLiteWorkspaceService(path),
                          lambda: store.list_workspaces('viewer'),
                          lambda: store.update_resource('alpha_admin', 'alpha', 'board', 'same',
                                                        text='must not persist', expected_revision=1),
                          lambda: store.create_workspace('operator', name='No unsafe trigger',
                                                         kind='product', initial_admin='outsider'))
            for operation in operations:
                with self.subTest(sql=sql), self.assertRaises(StorageError) as caught:
                    operation()
                self.assertEqual(caught.exception.code, 'unsupported_schema')
                self.assertEqual(snapshot(path), before)

    def test_cross_table_trigger_rebinding_rolls_back_the_whole_write(self):
        extensions = [
            "CREATE TRIGGER untrusted AFTER UPDATE ON memberships BEGIN "
            "UPDATE membership_authority SET generation=generation+1 WHERE principal_id='viewer'; END",
            "CREATE TRIGGER untrusted AFTER INSERT ON audit BEGIN "
            "UPDATE membership_authority SET generation=generation+1 WHERE principal_id='viewer'; END",
            "CREATE TRIGGER untrusted AFTER INSERT ON audit BEGIN "
            "UPDATE operator_authority SET generation=generation+1 WHERE principal_id='operator'; END",
        ]
        for number, sql in enumerate(extensions):
            path = self.path.with_name(f'cross-table-{number}.sqlite')
            store = bootstrap(path, two_admins=True)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.execute(sql)
            before = snapshot(path)
            with self.subTest(sql=sql), self.assertRaises(StorageError) as caught:
                store.set_membership('alpha_admin', 'alpha', 'viewer', role='developer',
                                     state='active', expected_revision=1)
            self.assertEqual(caught.exception.code, 'invalid_store')
            self.assertEqual(snapshot(path), before)
            self.assertEqual(store.read_resource('viewer', 'alpha', 'board', 'same').revision, 1)
            self.assertEqual(store.audit_events('alpha_admin', 'alpha'), ())

    def test_sql_identity_guards_remain_closed_in_schema_three(self):
        before = self.state()
        statements = [
            "UPDATE principals SET subject='replacement' WHERE id='viewer'",
            "UPDATE principals SET generation=generation+1 WHERE id='viewer'",
            "UPDATE principals SET active=0 WHERE id='viewer'",
            "DELETE FROM principals WHERE id='viewer'",
            "INSERT INTO principals VALUES('new','synthetic','new',1,1,1)",
        ]
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            for sql in statements:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)
                self.assertEqual(self.state(), before)

    def worker(self, action):
        code = '''
import sys
from packages.workspaces import SQLiteWorkspaceService, PolicyError
store = SQLiteWorkspaceService(sys.argv[1], timeout=10)
action = sys.argv[2]
if action == 'write':
    store.authorize('multi', 'alpha', 'board', 'write')
connect = store._connect
def traced_connect():
    connection = connect()
    connection.set_trace_callback(lambda sql: print('begin-immediate', flush=True)
                                  if sql == 'BEGIN IMMEDIATE' else None)
    return connection
store._connect = traced_connect
print('ready', flush=True)
assert sys.stdin.readline().strip() == 'go'
try:
    if action == 'edit':
        store.set_membership('alpha_admin', 'alpha', 'viewer', role='developer', state='active', expected_revision=1)
    else:
        store.update_resource('multi', 'alpha', 'board', 'same', text='child write', expected_revision=1)
    print('ok', flush=True)
except PolicyError as error:
    print(error.code, flush=True)
'''
        process = subprocess.Popen([sys.executable, '-c', code, str(self.path), action],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, cwd=Path(__file__).resolve().parents[3])
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        self.assertEqual(process.stdout.readline().strip(), 'ready')
        return process

    def go(self, process):
        process.stdin.write('go\n')
        process.stdin.flush()

    def finish(self, process):
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error)
        return [line for line in output.splitlines() if line != 'begin-immediate']

    def test_independent_writer_waits_for_lock_then_rechecks_current_membership_pin(self):
        process = self.worker('write')  # A valid observation made before the lock.
        pins = self.pins()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self.go(process)
                # SQLite's trace callback acknowledges entry to BEGIN IMMEDIATE,
                # before a write snapshot can be materialized. No timing sleep.
                self.assertEqual(process.stdout.readline().strip(), 'begin-immediate')
                simulate_identity(db, "UPDATE principals SET subject='replacement-multi' WHERE id='multi'")
                db.commit()
            finally:
                db.rollback()
        self.assertEqual(self.finish(process), ['not_found'])
        self.assertEqual(self.store.read_resource('alpha_admin', 'alpha', 'board', 'same').revision, 1)
        self.assertEqual(self.store.audit_events('alpha_admin', 'alpha'), ())
        self.assertEqual(self.pins(), pins)

    def test_independent_membership_cas_has_one_winner_without_repin(self):
        pins = self.pins()
        workers = [self.worker('edit'), self.worker('edit')]
        for process in workers:
            self.go(process)
        self.assertCountEqual([self.finish(process)[0] for process in workers], ['ok', 'stale_revision'])
        self.assertEqual(self.inspect(lambda p: p._memberships[('alpha', 'viewer')].revision), 2)
        self.assertEqual(len(self.store.audit_events('alpha_admin', 'alpha')), 1)
        self.assertEqual(self.pins(), pins)


class SQLiteOwnershipMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_v1_and_v2_migrations_preserve_history_but_never_adopt_grants(self):
        for version in (1, 2):
            path = self.directory / f'legacy-{version}.sqlite'
            source = legacy_policy(path, version)
            before = snapshot(path)
            with self.assertRaises(StorageError):
                SQLiteWorkspaceService(path)
            self.assertEqual(snapshot(path), before)
            with patch('packages.workspaces.service.capture_authority', side_effect=AssertionError('migration granted authority')):
                report = getattr(SQLiteWorkspaceService, f'migrate_v{version}')(path)
                self.assertEqual(report, dict(from_version=version, to_version=3,
                    principals=len(source._principals), identity_mutation_enabled=False, legacy_grants_upgraded=False))
                store = SQLiteWorkspaceService(path)
                def verify(policy):
                    for attribute in ('_memberships', '_operators', '_workspaces', '_modules', '_resources', '_audit', '_epoch'):
                        self.assertEqual(getattr(policy, attribute), getattr(source, attribute))
                    if version == 2:
                        self.assertEqual(policy._realm, source._realm)
                        self.assertEqual(policy._principals, source._principals)
                    self.assertEqual(policy._membership_authority, {key: None for key in source._memberships})
                    self.assertEqual(policy._operator_authority, {actor: None for actor in source._operators})
                    self.assertEqual(policy._eligible_admins('alpha'), [])
                store._transaction(verify, write=False)
                self.assertEqual(store.list_workspaces('alpha_admin'), ())
                for operation, code in ((lambda: store.list_memberships('alpha_admin', 'alpha'), 'not_found'),
                                        (lambda: store.list_control_workspaces('operator'), 'forbidden')):
                    migrated = snapshot(path)
                    with self.assertRaises(PolicyError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(snapshot(path), migrated)
                # Persisting unchanged legacy state is permitted, but is never
                # a reason to capture ownership from the current local IDs.
                store._transaction(lambda p: None, write=True)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                self.assertEqual(db.execute('SELECT * FROM synthetic_module').fetchall(), [('retained',)])
                self.assertEqual(db.execute('SELECT DISTINCT realm,issuer,subject,generation FROM membership_authority').fetchall(),
                                 [(None, None, None, None)])
            migrated = snapshot(path)
            with self.assertRaises(StorageError):
                getattr(SQLiteWorkspaceService, f'migrate_v{version}')(path)
            self.assertEqual(snapshot(path), migrated)

    def test_each_new_migration_write_rolls_back_schema_rows_and_version(self):
        points = ['CREATE TABLE membership_authority', 'CREATE TABLE operator_authority',
                  'INSERT INTO membership_authority(', 'INSERT INTO operator_authority(', 'PRAGMA user_version=3']
        class Injected(sqlite3.Connection):
            needle = ''
            def execute(self, sql, *args):
                if sql.startswith(self.needle):
                    raise sqlite3.OperationalError('synthetic migration fault')
                return super().execute(sql, *args)
        def connect(store):
            return sqlite3.connect(store._uri, uri=True, isolation_level=None, factory=Injected)
        for version in (1, 2):
            for number, point in enumerate(points):
                path = self.directory / f'fault-{version}-{number}.sqlite'
                legacy_policy(path, version)
                before = snapshot(path)
                Injected.needle = point
                with self.subTest(version=version, point=point), patch.object(SQLiteWorkspaceService, '_connect', connect):
                    with self.assertRaises(StorageError):
                        getattr(SQLiteWorkspaceService, f'migrate_v{version}')(path)
                self.assertEqual(snapshot(path), before)
            path = self.directory / f'final-validation-{version}.sqlite'
            legacy_policy(path, version)
            before = snapshot(path)
            with patch.object(SQLiteWorkspaceService, '_load', side_effect=StorageError('invalid_store')):
                with self.assertRaises(StorageError):
                    getattr(SQLiteWorkspaceService, f'migrate_v{version}')(path)
            self.assertEqual(snapshot(path), before)

    def test_migration_final_validation_rejects_injected_sidecar_extension_atomically(self):
        original = SQLiteWorkspaceService._migrate_grant_tombstones
        extensions = [
            "CREATE TRIGGER untrusted AFTER INSERT ON membership_authority BEGIN "
            "UPDATE membership_authority SET generation=2; END",
            'CREATE INDEX untrusted ON operator_authority(generation)',
        ]
        for version in (1, 2):
            for number, sql in enumerate(extensions):
                path = self.directory / f'extension-{version}-{number}.sqlite'
                legacy_policy(path, version)
                before = snapshot(path)
                def extend(connection):
                    original(connection)
                    connection.execute(sql)
                with self.subTest(version=version, sql=sql), patch.object(
                        SQLiteWorkspaceService, '_migrate_grant_tombstones', staticmethod(extend)):
                    with self.assertRaises(StorageError) as caught:
                        getattr(SQLiteWorkspaceService, f'migrate_v{version}')(path)
                self.assertEqual(caught.exception.code, 'unsupported_schema')
                self.assertEqual(snapshot(path), before)

    def test_v2_partial_schema_or_invalid_data_is_never_repaired_or_adopted(self):
        changes = [
            'DROP TRIGGER principal_authority_closed',
            'CREATE INDEX untrusted ON principals(subject)',
            'CREATE TRIGGER untrusted AFTER UPDATE ON metadata BEGIN SELECT 1; END',
            'CREATE TABLE membership_authority (value TEXT) STRICT',
            'UPDATE memberships SET revision=9007199254740992',
        ]
        for number, sql in enumerate(changes):
            path = self.directory / f'invalid-v2-{number}.sqlite'
            legacy_policy(path, 2)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.execute(sql)
            before = snapshot(path)
            with self.subTest(sql=sql), self.assertRaises(StorageError):
                SQLiteWorkspaceService.migrate_v2(path)
            self.assertEqual(snapshot(path), before)


if __name__ == '__main__':
    unittest.main()
