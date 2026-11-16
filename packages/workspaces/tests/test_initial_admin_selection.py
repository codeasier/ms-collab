# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Retained initial-admin observations; synthetic identities, no live login."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from packages.workspaces import PolicyError, SQLiteWorkspaceService, WorkspaceService
from packages.workspaces.authority import capture_authority
from packages.workspaces.tests.identity_fixture import simulate_identity, simulate_identity_with_core_regrant
from packages.workspaces.tests.test_authority import snapshot
from packages.workspaces.tests.test_membership_authority import policy_state
from packages.workspaces.tests.test_service import fixture


class SelectionContract:
    persistent = False

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / 'policy.sqlite'
        self.store = self.new_store(self.path)

    def new_store(self, path):
        source = fixture(two_admins=True)
        arguments = dict(principals=tuple(source._principals.values()),
                         workspaces=tuple(source._workspaces.values()),
                         memberships=tuple(source._memberships.values()),
                         modules=tuple(source._modules.values()), resources=tuple(source._resources.values()),
                         operators=frozenset({'operator', 'outsider'}))
        return SQLiteWorkspaceService.initialize(path, **arguments) if self.persistent else WorkspaceService(**arguments)

    def inspect(self, function):
        return self.store._transaction(function, write=False) if self.persistent else function(self.store)

    def state(self):
        return snapshot(self.path) if self.persistent else policy_state(self.store)

    def proof(self, actor='operator', target='viewer'):
        return self.store.initial_admin_selection(actor, target)['precondition']

    def create(self, proof=None, *, actor='operator', target='viewer'):
        return self.store.create_workspace(actor, name='Explicit synthetic choice', kind='product',
            initial_admin=target, expected_initial_admin_selection=proof)

    def reject(self, status, code, operation):
        before = self.state()
        with self.assertRaises(PolicyError) as caught:
            operation()
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        self.assertEqual(self.state(), before)

    def change(self, actor, field, value):
        # For actor-incarnation tests only, explicitly regrant its operator pin
        # so current-role rejection cannot mask a missing selection comparison.
        if self.persistent:
            helper = simulate_identity_with_core_regrant if actor == 'operator' else simulate_identity
            with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
                helper(db, f'UPDATE principals SET {field}=? WHERE id=?', (value, actor))
        else:
            prior = self.store._principals[actor]
            self.store._epoch += 1
            changes = {field: value, 'authority_epoch': self.store._epoch}
            if field != 'generation':
                changes['generation'] = prior.generation + 1
            self.store._principals[actor] = replace(prior, **changes)
            if actor == 'operator':
                self.store._operator_authority[actor] = capture_authority(self.store, (actor,))

    def test_preview_is_exact_deterministic_and_read_only(self):
        before = self.state()
        with patch('packages.workspaces.service.capture_authority', side_effect=AssertionError('preview granted authority')):
            first = self.store.initial_admin_selection('operator', 'viewer')
            self.assertEqual(first, self.store.initial_admin_selection('operator', 'viewer'))
        self.assertEqual(set(first), {'initial_admin', 'precondition'})
        self.assertEqual(first['initial_admin'], 'viewer')
        self.assertRegex(first['precondition'], r'\Aias1-[0-9a-f]{64}\Z')
        self.assertEqual(self.state(), before)

    def test_missing_bad_syntax_and_wrong_proof_write_nothing(self):
        self.reject(428, 'initial_admin_selection_required', lambda: self.create())
        proof = self.proof()
        for value in ('', 'ias1-' + 'a' * 63, 'ias1-' + 'A' * 64, 'ias2-' + 'a' * 64,
                      ' ' + proof, proof + ' ', proof + '\n', proof + ',' + proof,
                      True, 1, [], {}, proof.encode()):
            with self.subTest(value=repr(value)):
                self.reject(400, 'invalid_initial_admin_selection', lambda: self.create(value))
        self.reject(412, 'initial_admin_selection_changed', lambda: self.create('ias1-' + '0' * 64))

    def test_proof_is_bound_to_target_actor_and_realm(self):
        proof = self.proof()
        self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof, target='multi'))
        self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof, actor='outsider'))
        other = self.new_store(self.directory / 'other.sqlite')
        other_proof = other.initial_admin_selection('operator', 'viewer')['precondition']
        self.assertNotEqual(proof, other_proof)
        self.reject(412, 'initial_admin_selection_changed', lambda: self.create(other_proof))

    def test_target_and_operator_issuer_subject_active_aba_stay_stale(self):
        for actor in ('viewer', 'operator'):
            for field in ('issuer', 'subject', 'active'):
                with self.subTest(actor=actor, field=field):
                    proof = self.proof()
                    original = self.inspect(lambda p: getattr(p._principals[actor], field))
                    changed = False if field == 'active' else 'synthetic-temporary-' + field
                    self.change(actor, field, changed)
                    if field != 'active' or actor != 'operator':
                        self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof))
                    self.change(actor, field, original)
                    self.assertTrue(self.inspect(lambda p: p._operator_current('operator')))
                    self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof))
                    self.assertNotEqual(proof, self.proof())

    def test_generation_alone_invalidates_both_incarnations(self):
        for actor in ('viewer', 'operator'):
            proof = self.proof()
            generation = self.inspect(lambda p: p._principals[actor].generation)
            self.change(actor, 'generation', generation + 1)
            self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof))

    def test_preview_and_create_still_require_current_operator(self):
        proof = self.proof()
        for actor, status, code in (('multi', 403, 'forbidden'), ('missing', 401, 'unauthenticated'),
                                    ('disabled', 401, 'unauthenticated')):
            self.reject(status, code, lambda: self.store.initial_admin_selection(actor, 'viewer'))
            self.reject(status, code, lambda: self.create(proof, actor=actor))
        for target in ('missing', 'disabled', None, []):
            self.reject(404, 'initial_admin_unavailable', lambda: self.store.initial_admin_selection('operator', target))
            self.reject(412, 'initial_admin_selection_changed', lambda: self.create(proof, target=target))

    def test_policy_changes_restart_and_prior_use_do_not_consume_selection(self):
        proof = self.proof()
        self.store.set_membership('alpha_admin', 'alpha', 'viewer', role='developer', state='active', expected_revision=1)
        self.store.update_resource('viewer', 'alpha', 'board', 'same', text='unrelated policy history', expected_revision=1)
        self.store.set_workspace_state('operator', 'commons', state='suspended', expected_revision=1)
        if self.persistent:
            self.store = SQLiteWorkspaceService(self.path)
        self.assertEqual(self.proof(), proof)
        first = self.create(proof)
        second = self.create(proof)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(self.proof(), proof)
        for created in (first, second):
            self.assertEqual(self.store.list_memberships('viewer', created.id)[0].role, 'admin')
            self.assertEqual([e.action for e in self.store.audit_events('viewer', created.id)], ['workspace.create'])

    def test_create_never_silently_prepares_or_refreshes_selection(self):
        proof = self.proof()
        with patch.object(WorkspaceService, 'initial_admin_selection', side_effect=AssertionError('implicit prepare')):
            self.reject(428, 'initial_admin_selection_required', lambda: self.create())
            created = self.create(proof)
        self.assertIn(created, self.store.list_workspaces('viewer'))


class MemoryInitialAdminSelectionTests(SelectionContract, unittest.TestCase):
    pass


class SQLiteInitialAdminSelectionTests(SelectionContract, unittest.TestCase):
    persistent = True

    def test_two_processes_waiting_for_writer_lock_reject_changed_incarnations(self):
        for actor in ('viewer', 'operator'):
            with self.subTest(changed=actor):
                self.locked_selection_change(actor)

    def locked_selection_change(self, actor):
        # Each contender retains the pre-lock observation. The parent owns
        # a third connection's reservation until BOTH child writers attempt it.
        proof = self.proof()
        code = '''
import sys
from packages.workspaces import SQLiteWorkspaceService, PolicyError
store = SQLiteWorkspaceService(sys.argv[1], timeout=10)
connect = store._connect
def observed():
    db = connect()
    db.set_trace_callback(lambda sql: print('waiting', flush=True) if sql == 'BEGIN IMMEDIATE' else None)
    return db
store._connect = observed
print('ready', flush=True)
assert sys.stdin.readline().strip() == 'go'
try:
    store.create_workspace('operator', name='Locked selection', kind='product', initial_admin='viewer',
                           expected_initial_admin_selection=sys.argv[2])
    print('created', flush=True)
except PolicyError as error:
    print(str(error.status) + ' ' + error.code, flush=True)
'''
        children = []
        def line(child):
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=10), 'child did not reach barrier')
            return child.stdout.readline().strip()
        try:
            for _ in range(2):
                child = subprocess.Popen([sys.executable, '-c', code, str(self.path), proof],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=Path(__file__).resolve().parents[3])
                children.append(child)
                self.assertEqual(line(child), 'ready')
            with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
                db.execute('BEGIN IMMEDIATE')
                for child in children:
                    child.stdin.write('go\n'); child.stdin.flush()
                for child in children:
                    self.assertEqual(line(child), 'waiting')
                helper = simulate_identity_with_core_regrant if actor == 'operator' else simulate_identity
                helper(db, 'UPDATE principals SET subject=? WHERE id=?', ('synthetic-temporary', actor))
                helper(db, 'UPDATE principals SET subject=? WHERE id=?', (actor, actor))
                # Capture expected committed content while contenders are
                # still blocked, before either can run after the release.
                before = (db.execute('PRAGMA user_version').fetchone()[0], tuple(db.iterdump()))
                db.commit()
            for child in children:
                output, error = child.communicate(timeout=15)
                self.assertEqual((child.returncode, error), (0, ''))
                self.assertEqual(output.strip(), '412 initial_admin_selection_changed')
            self.assertEqual(self.state(), before)
            self.assertEqual(len(self.store.list_control_workspaces('operator')), 3)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=10)
