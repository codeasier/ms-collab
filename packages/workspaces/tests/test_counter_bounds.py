"""Safe-integer budgets. Only disposable policy fixtures, never production data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.model import MAX_REVISION as MAX, AuditEvent, revision
from packages.workspaces.tests.test_authority import legacy_policy, snapshot
from packages.workspaces.tests.test_service import fixture


class CountedAudit(list):
    """Synthetic length boundary; never allocate MAX entries or persist this."""
    def __init__(self, count):
        super().__init__()
        self.count = count
    def __len__(self):
        return self.count
    def append(self, item):
        super().append(item)
        self.count += 1


def memory_state(store):
    return (store._principals.copy(), store._workspaces.copy(), store._memberships.copy(),
            store._modules.copy(), store._resources.copy(), store._operators,
            store._epoch, store._realm, tuple(x for x in store._audit), len(store._audit))


class CounterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name); self.number = 0

    def seeded(self, *, sqlite=False, field=None, value=MAX, epoch=1, archive=False):
        source = fixture(two_admins=True)
        if archive:
            source._workspaces['alpha'] = replace(source._workspaces['alpha'], state='archived')
        entries = {
            'member': (source._memberships, ('alpha', 'viewer')),
            'actor': (source._memberships, ('alpha', 'alpha_admin')),
            'replacement': (source._memberships, ('alpha', 'multi')),
            'workspace': (source._workspaces, 'alpha'),
            'module': (source._modules, ('alpha', 'board')),
            'resource': (source._resources, ('alpha', 'board', 'same')),
        }
        if field:
            records, key = entries[field]; records[key] = replace(records[key], revision=value)
        source._epoch = epoch
        if not sqlite:
            return source
        self.number += 1; path = self.directory / f'policy-{self.number}.sqlite'
        store = SQLiteWorkspaceService.initialize(path,
            principals=tuple(source._principals.values()), workspaces=tuple(source._workspaces.values()),
            memberships=tuple(source._memberships.values()), modules=tuple(source._modules.values()),
            resources=tuple(source._resources.values()), operators=source._operators)
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            db.execute('UPDATE metadata SET epoch=?', (epoch,))
        return store

    def state(self, store):
        return snapshot(store._path) if type(store) is SQLiteWorkspaceService else memory_state(store)

    def check_error(self, store, operation, code='counter_exhausted', status=409):
        before = self.state(store)
        with self.assertRaises(PolicyError) as caught:
            operation()
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        self.assertEqual(self.state(store), before)

    def actions(self, store, expected=1):
        return {
            'member': lambda: store.set_membership('alpha_admin', 'alpha', 'viewer', role='viewer', state='revoked', expected_revision=expected),
            'workspace': lambda: store.set_workspace_state('operator', 'alpha', state='suspended', expected_revision=expected),
            'module': lambda: store.set_module_state('alpha_admin', 'alpha', 'board', state='disabling', expected_revision=expected),
            'resource': lambda: store.update_resource('alpha_admin', 'alpha', 'board', 'same', text='new', expected_revision=expected),
            'create': lambda: store.create_workspace('operator', name='Synthetic new', kind='product', initial_admin='viewer',
                expected_initial_admin_selection=store.initial_admin_selection('operator','viewer')['precondition']),
            'install': lambda: store.install_module('alpha_admin', 'alpha', 'new', capabilities=('read',)),
            'transfer': lambda: store.transfer_admin('alpha_admin', 'alpha', 'multi', expected_revision=expected, replacement_revision=expected),
        }

    def test_models_and_preconditions_require_exact_bounded_integers(self):
        source = fixture()
        records = [next(iter(values.values())) for values in
                   (source._workspaces, source._memberships, source._modules, source._resources)]
        for value in (True, False, 0, -1, 1.0, '1', MAX + 1):
            with self.subTest(value=value), self.assertRaises(ValueError): revision(value)
            for record in records:
                with self.subTest(record=type(record).__name__, value=value), self.assertRaises(ValueError):
                    replace(record, revision=value)
            for field in ('sequence', 'revision'):
                values = {'sequence':1,'revision':1}; values[field] = value
                with self.assertRaises(ValueError): AuditEvent(actor_id='a',workspace_id='w',action='resource.update',object_id='r',**values)
            self.check_error(source, self.actions(source, value)['member'], 'invalid_revision', 400)
        for record in records:
            self.assertEqual(replace(record, revision=MAX).revision, MAX)

    def test_each_record_revision_max_minus_one_then_exhaustion(self):
        for sql in (False, True):
            for field in ('member', 'workspace', 'module', 'resource'):
                with self.subTest(sqlite=sql, field=field):
                    store = self.seeded(sqlite=sql, field=field, value=MAX - 1)
                    result = self.actions(store, MAX - 1)[field]()
                    self.assertEqual(result.revision, MAX)
                    # Use a new valid operation where the first changed lifecycle.
                    if field == 'member':
                        operation = lambda: store.set_membership('alpha_admin','alpha','viewer',role='viewer',state='active',expected_revision=MAX)
                        self.check_error(store, operation, 'membership_revoked')
                    elif field == 'workspace':
                        self.check_error(store, lambda: store.set_workspace_state('operator','alpha',state='active',expected_revision=MAX))
                    elif field == 'module':
                        self.check_error(store, lambda: store.set_module_state('alpha_admin','alpha','board',state='disabled',expected_revision=MAX))
                    else:
                        self.check_error(store, self.actions(store, MAX)[field])
                    exhausted = self.seeded(sqlite=sql, field=field)
                    self.check_error(exhausted, self.actions(exhausted, MAX)[field])
                    if sql:
                        reopened = SQLiteWorkspaceService(store._path)
                        self.assertEqual(self.state(reopened), self.state(store))

    def test_same_value_membership_still_advances_and_cannot_reset_at_max(self):
        for sql in (False, True):
            store = self.seeded(sqlite=sql, field='member', value=MAX - 1)
            operation = lambda expected: store.set_membership('alpha_admin','alpha','viewer',role='viewer',state='active',expected_revision=expected)
            self.assertEqual(operation(MAX - 1).revision, MAX)
            self.check_error(store, lambda: operation(MAX))
            self.assertEqual(len(store.audit_events('alpha_admin','alpha')),1)

    def test_epoch_is_preflighted_for_every_policy_mutation_but_not_resource_write(self):
        for sql in (False, True):
            for action in ('member','workspace','module','create','install','transfer'):
                with self.subTest(sqlite=sql,action=action):
                    store = self.seeded(sqlite=sql, epoch=MAX)
                    self.check_error(store, self.actions(store)[action])
                    store = self.seeded(sqlite=sql, epoch=MAX - 1)
                    self.actions(store)[action]()
                    epoch = (store._transaction(lambda p:p._epoch,write=False) if sql else store._epoch)
                    self.assertEqual(epoch, MAX)
            store = self.seeded(sqlite=sql, epoch=MAX)
            self.assertEqual(self.actions(store)['resource']().revision, 2)
            self.assertEqual(store.authorize('alpha_admin','alpha','board','read').policy_epoch,MAX)

    def test_transfer_preflights_both_members_and_both_audit_slots(self):
        for sql in (False, True):
            for field in ('actor','replacement'):
                store = self.seeded(sqlite=sql,field=field)
                self.check_error(store,lambda:store.transfer_admin('alpha_admin','alpha','multi',
                    expected_revision=MAX if field=='actor' else 1,replacement_revision=MAX if field=='replacement' else 1))
            store = self.seeded(sqlite=sql,field='actor',value=MAX-1)
            if sql:
                with closing(sqlite3.connect(store._path,isolation_level=None)) as db:
                    db.execute("UPDATE memberships SET revision=? WHERE workspace_id='alpha' AND principal_id='multi'",(MAX-1,))
            else:
                key=('alpha','multi');store._memberships[key]=replace(store._memberships[key],revision=MAX-1)
            result=store.transfer_admin('alpha_admin','alpha','multi',expected_revision=MAX-1,replacement_revision=MAX-1)
            self.assertEqual([x.revision for x in result],[MAX,MAX])
            self.assertEqual(len(store.audit_events('multi','alpha')),2)
        for count in (MAX-1,MAX):
            store=self.seeded();store._audit=CountedAudit(count)
            self.check_error(store,self.actions(store)['transfer'])
        store=self.seeded();store._audit=CountedAudit(MAX-2)
        self.actions(store)['transfer']()
        self.assertEqual([x.sequence for x in store._audit],[MAX-1,MAX])

    def test_single_event_audit_budget_precedes_every_in_memory_mutation(self):
        for action in self.actions(self.seeded()):
            store=self.seeded();store._audit=CountedAudit(MAX)
            self.check_error(store,self.actions(store)[action])
        store=self.seeded();store._audit=CountedAudit(MAX-1)
        self.actions(store)['member']()
        self.assertEqual(store._audit[-1].sequence,MAX)
        self.check_error(store,self.actions(store)['resource'])

    def test_authorization_archive_last_admin_and_stale_precedence_remain(self):
        for sql in (False,True):
            store=self.seeded(sqlite=sql,field='member',archive=True)
            self.check_error(store,self.actions(store,MAX)['member'])
            self.check_error(store,lambda:store.set_membership('outsider','alpha','viewer',role='viewer',state='revoked',expected_revision=MAX),'not_found',404)
            self.check_error(store,self.actions(store,MAX-1)['member'],'stale_revision',412)
            self.check_error(store,lambda:store.set_membership('alpha_admin','alpha','viewer',role='admin',state='active',expected_revision=MAX),'archive_privilege_increase')
            store=self.seeded(sqlite=sql,field='actor')
            store.set_membership('alpha_admin','alpha','multi',role='developer',state='active',expected_revision=1)
            self.check_error(store,lambda:store.set_membership('alpha_admin','alpha','alpha_admin',role='viewer',state='revoked',expected_revision=MAX),'last_admin')

    def test_competing_membership_writes_only_one_consumes_last_increment(self):
        for sql in (False,True):
            store=self.seeded(sqlite=sql,field='member',value=MAX-1);barrier=Barrier(2)
            def work():
                target=SQLiteWorkspaceService(store._path) if sql else store
                barrier.wait(timeout=5)
                try:
                    return target.set_membership('alpha_admin','alpha','viewer',role='viewer',state='active',expected_revision=MAX-1).revision
                except PolicyError as error:return error.code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:work(),range(2)))
            self.assertCountEqual(results,[MAX,'stale_revision'])
            self.assertEqual(len(store.audit_events('alpha_admin','alpha')),1)

    def test_v2_corrupt_counters_fail_closed_without_repair_or_write(self):
        for table in ('memberships','workspaces','modules','resources','audit'):
            store=self.seeded(sqlite=True)
            self.actions(store)['member']()
            with closing(sqlite3.connect(store._path,isolation_level=None)) as db:
                db.execute(f'UPDATE {table} SET revision=?',(MAX+1,))
            before=self.state(store)
            reopened=SQLiteWorkspaceService(store._path)
            for operation in (lambda:reopened.list_workspaces('alpha_admin'),self.actions(reopened)['resource']):
                with self.subTest(table=table),self.assertRaises(StorageError) as caught:operation()
                self.assertEqual(caught.exception.code,'invalid_store')
                self.assertEqual(self.state(store),before)

    def test_legacy_migration_keeps_max_and_rolls_back_oversized_rows(self):
        for value in (MAX,MAX+1):
            path=self.directory/f'legacy-{value}.sqlite';legacy_policy(path)
            with closing(sqlite3.connect(path,isolation_level=None)) as db:
                db.execute('UPDATE memberships SET revision=?',(value,))
            before=snapshot(path)
            if value==MAX:
                SQLiteWorkspaceService.migrate_v1(path)
                store=SQLiteWorkspaceService(path)
                self.assertTrue(store._transaction(lambda p: all(x.revision==MAX for x in p._memberships.values()),write=False))
                with self.assertRaises(PolicyError) as denied:
                    store.list_memberships('alpha_admin','alpha')
                self.assertEqual(denied.exception.status,404)
            else:
                with self.assertRaises(StorageError):SQLiteWorkspaceService.migrate_v1(path)
                self.assertEqual(snapshot(path),before)

    def test_persist_defensively_rejects_bad_counter_before_any_sql_writes(self):
        store=self.seeded(sqlite=True)
        for field in ('member','epoch','audit_revision','audit_sequence'):
            before=self.state(store)
            def corrupt(policy):
                if field=='member':
                    record=replace(policy._memberships[('alpha','viewer')]);object.__setattr__(record,'revision',MAX+1)
                    policy._memberships[('alpha','viewer')]=record
                elif field=='epoch':policy._epoch=MAX+1
                else:
                    event=AuditEvent(1,'alpha_admin','alpha','resource.update','synthetic',1)
                    object.__setattr__(event,'revision' if field=='audit_revision' else 'sequence',MAX+1)
                    policy._audit.append(event)
            with self.subTest(field=field),self.assertRaises(StorageError):store._transaction(corrupt,write=True)
            self.assertEqual(self.state(store),before)

    def test_multi_write_persist_audit_and_final_guard_failures_roll_back_at_max(self):
        store=self.seeded(sqlite=True,field='actor',value=MAX-1,epoch=MAX-1)
        before=self.state(store)
        persist=SQLiteWorkspaceService._persist
        def fail_after(db,policy,*,audit_start):
            persist(db,policy,audit_start=audit_start)
            raise RuntimeError('synthetic failure')
        with patch.object(SQLiteWorkspaceService,'_persist',staticmethod(fail_after)),self.assertRaises(RuntimeError):
            store.transfer_admin('alpha_admin','alpha','multi',expected_revision=MAX-1,replacement_revision=1)
        self.assertEqual(self.state(store),before)
        def guard():raise PolicyError(401,'unauthenticated')
        with self.assertRaises(PolicyError):store._transaction(lambda p:p.transfer_admin('alpha_admin','alpha','multi',expected_revision=MAX-1,replacement_revision=1),write=True,before_commit=guard)
        self.assertEqual(self.state(store),before)
        with closing(sqlite3.connect(store._path,isolation_level=None)) as db:
            db.execute("CREATE TRIGGER fail_second_audit BEFORE INSERT ON audit WHEN NEW.action='membership.demote' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        before=self.state(store)
        with self.assertRaises(StorageError):store.transfer_admin('alpha_admin','alpha','multi',expected_revision=MAX-1,replacement_revision=1)
        self.assertEqual(self.state(store),before)


if __name__=='__main__':unittest.main()
