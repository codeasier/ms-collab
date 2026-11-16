# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Disposable synthetic scopes and test-only directory transitions, never live."""
from dataclasses import replace
import json
import multiprocessing
import sqlite3
from threading import Event, Thread
import unittest

from packages.community_api import regrant_policy, replace_policy
from packages.community_api.records import read, encode, config_text
from packages.community_api.tests import test_application as fixtures
from packages.community_sync import GitCodeProvider, Connection
from packages.community_sync.tests.test_sync import row
from packages.workspaces import SQLiteWorkspaceService, PolicyError, StorageError
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant as simulate_identity


def regrant_worker(path,barrier,queue):
    try:
        store=SQLiteWorkspaceService(path);barrier.wait(10)
        result=regrant_policy(store,fixtures.config(),expected_revision=1,actor_id='admin')
        queue.put(('ok',result.grant_revision))
    except PolicyError as e:queue.put(('denied',e.status))
    except Exception as e:queue.put(('unexpected',type(e).__name__))


class CommunityAuthorityTests(unittest.TestCase):
    setUp=fixtures.CommunityTests.setUp
    state=fixtures.CommunityTests.state
    refresh=fixtures.CommunityTests.refresh
    approve=fixtures.CommunityTests.approve

    def sql(self,sql,args=()):
        with sqlite3.connect(self.path) as db:return db.execute(sql,args).fetchall()

    def transition(self,actor='reader',*,inactive=False):
        with sqlite3.connect(self.path) as db:
            if inactive:
                simulate_identity(db,"UPDATE principals SET active=0 WHERE id=?",(actor,))
                simulate_identity(db,"UPDATE principals SET active=1 WHERE id=?",(actor,))
            else:
                old=db.execute('SELECT subject FROM principals WHERE id=?',(actor,)).fetchone()[0]
                simulate_identity(db,"UPDATE principals SET subject='replacement' WHERE id=?",(actor,))
                simulate_identity(db,"UPDATE principals SET subject=? WHERE id=?",(old,actor))

    def regrant(self,expected=None):
        return regrant_policy(self.store,fixtures.config(),expected_revision=expected or self.state().revision,actor_id='admin')

    def test_current_module_approval_does_not_override_unbound_workspace_membership(self):
        from packages.community_api.records import approval_current
        self.refresh();self.approve()
        self.sql("UPDATE membership_authority SET realm=NULL,issuer=NULL,subject=NULL,generation=NULL WHERE workspace_id='alpha' AND principal_id='admin'")
        state=self.state()
        self.assertFalse(self.store._transaction(lambda p:approval_current(p,state),write=False))
        self.assertEqual(fixtures.call(self.app)['body']['data']['items'],[])

    def test_reader_aba_after_fresh_membership_does_not_restore_old_grants(self):
        self.refresh();self.approve();self.transition()
        self.store.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE memberships SET state='active',revision=revision+1 WHERE workspace_id='alpha' AND principal_id='reader'")
        self.assertTrue(self.store._transaction(lambda p:p._membership_current('reader','alpha'),write=False))
        self.assertEqual(fixtures.call(self.app)['status'],404)
        with self.assertRaises(PolicyError):self.store.set_principal('admin','reader',active=True)

    def test_reactivation_does_not_restore_reader_grant(self):
        self.refresh();self.approve();self.transition(inactive=True)
        self.assertEqual(fixtures.call(self.app)['status'],404)

    def test_regular_edit_and_noop_never_recapture_stale_identity(self):
        self.transition();before=self.state()
        result=replace_policy(self.store,fixtures.config(),expected_revision=1,actor_id='admin')
        self.assertEqual(result,before)
        result=replace_policy(self.store,replace(fixtures.config(),credential_ref='new-ref'),expected_revision=1,actor_id='admin')
        self.assertEqual(result.authority['principals']['reader']['generation'],1)
        self.assertEqual(result.grant_revision,2)
        self.assertEqual(fixtures.call(self.app)['status'],404)

    def test_remove_aba_readd_uses_retired_pin_not_current_identity(self):
        removed=replace_policy(self.store,replace(fixtures.config(),reader_ids=()),expected_revision=1,actor_id='admin')
        self.assertEqual(removed.retired_principals['reader']['generation'],1)
        self.transition()
        restored=replace_policy(self.store,fixtures.config(),expected_revision=2,actor_id='admin')
        self.assertEqual(restored.authority['principals']['reader']['generation'],1)
        self.assertEqual(fixtures.call(self.app)['status'],404)
        fresh=self.regrant();self.assertEqual(fresh.authority['principals']['reader']['generation'],3)
        self.assertFalse(fresh.retired_principals)

    def test_explicit_regrant_is_monotonic_even_for_identical_dto_and_clears_content(self):
        self.refresh();self.approve();self.transition()
        state=self.regrant();self.assertEqual(state.grant_revision,2)
        self.assertIsNone(state.snapshot);self.assertFalse(state.approvals)
        self.assertEqual(state.authority['principals']['reader']['generation'],3)
        second=self.regrant();self.assertEqual(second.grant_revision,3)
        with self.assertRaises(PolicyError) as e:self.regrant(expected=state.revision)
        self.assertEqual(e.exception.status,412)
        self.assertEqual(fixtures.call(self.app)['body']['data']['items'],[])
        self.refresh();self.approve();self.assertEqual(len(fixtures.call(self.app)['body']['data']['items']),2)

    def test_legacy_record_read_and_normal_edit_cannot_adopt_it(self):
        self.refresh();self.approve();old=self.state()
        self.sql("UPDATE community_connections SET config=?,approvals=? WHERE workspace_id='alpha' AND connection_id='source'",(encode(old.policy.as_dict()),encode(sorted(old.approvals))))
        before=self.sql("SELECT * FROM community_connections")
        self.assertEqual(fixtures.call(self.app)['status'],503)
        with self.assertRaises(StorageError):replace_policy(self.store,fixtures.config(),expected_revision=3,actor_id='admin')
        with self.assertRaises(StorageError):fixtures.initialize(self.store,self.policies)
        self.assertEqual(self.sql("SELECT * FROM community_connections"),before)
        state=regrant_policy(self.store,fixtures.config(),expected_revision=3,actor_id='admin')
        self.assertEqual((state.grant_revision,state.revision),(1,4));self.assertIsNone(state.snapshot);self.assertFalse(state.approvals)

    def test_partial_and_foreign_realm_grant_fail_closed_without_repair(self):
        raw=self.state().config_raw;value=json.loads(raw)
        for change in ('missing','foreign'):
            changed=json.loads(raw)
            if change=='missing':changed['authority']['principals']['reader'].pop('generation')
            else:changed['authority']['realm']='0'*32
            self.sql("UPDATE community_connections SET config=? WHERE workspace_id='alpha' AND connection_id='source'",(encode(changed),))
            result=fixtures.call(self.app)
            self.assertEqual(result['status'],503 if change=='missing' else 404)
            self.assertEqual(self.sql("SELECT config FROM community_connections WHERE workspace_id='alpha' AND connection_id='source'")[0][0],encode(changed))
        self.sql("UPDATE community_connections SET config=? WHERE workspace_id='alpha' AND connection_id='source'",(raw,))

    def test_approver_demotion_restore_requires_fresh_approval(self):
        self.refresh();self.approve()
        self.store.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
        self.assertEqual(fixtures.call(self.app)['body']['data']['items'],[])
        self.store.set_membership('second','alpha','admin',role='admin',state='active',expected_revision=2)
        self.assertEqual(fixtures.call(self.app)['body']['data']['items'],[])
        review=fixtures.call(self.app,'/review',token='test-admin')
        self.assertEqual(review['status'],200);self.assertTrue(all(not x['approved'] for x in review['body']['data']['items']))
        self.assertEqual(self.approve()['status'],200)
        self.assertEqual(len(fixtures.call(self.app)['body']['data']['items']),2)

    def test_archive_reads_and_module_pause_restore_have_explicit_semantics(self):
        self.refresh();self.approve()
        self.store.set_module_state('admin','alpha','community',state='disabling',expected_revision=1)
        self.assertEqual(fixtures.call(self.app)['status'],409)
        self.store.set_module_state('admin','alpha','community',state='disabled',expected_revision=2)
        self.store.set_module_state('admin','alpha','community',state='enabled',expected_revision=3)
        self.assertEqual(len(fixtures.call(self.app)['body']['data']['items']),2)
        # Trusted fixture core currently has no operator; archive with raw fixture
        # state update tests retained-read semantics, not an administration route.
        self.sql("UPDATE workspaces SET state='archived',revision=revision+1 WHERE id='alpha'")
        self.assertEqual(len(fixtures.call(self.app)['body']['data']['items']),2)
        self.assertEqual(self.approve()['status'],409)

    def test_audit_failure_rolls_back_regrant_clear_and_counters(self):
        self.refresh();self.approve();before=self.sql('SELECT * FROM community_connections')
        self.sql("CREATE TRIGGER synthetic_fail BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(StorageError):self.regrant()
        self.assertEqual(self.sql('SELECT * FROM community_connections'),before)

    def test_two_process_regrant_cas_has_one_winner_and_one_audit(self):
        ctx=multiprocessing.get_context('fork');barrier=ctx.Barrier(2);queue=ctx.Queue()
        children=[ctx.Process(target=regrant_worker,args=(str(self.path),barrier,queue)) for _ in range(2)]
        try:
            for child in children:child.start()
            values=[queue.get(timeout=15) for _ in children]
            for child in children:child.join(10);self.assertEqual(child.exitcode,0)
            self.assertEqual(sorted(values),[('denied',412),('ok',2)])
            self.assertEqual(len(self.store.audit_events('admin','alpha')),1)
        finally:
            for child in children:
                if child.is_alive():child.kill();child.join()
            queue.close()

    def test_loader_aba_prevents_provider_dispatch(self):
        original=self.app._credential_loader
        def loader(*args):
            result=original(*args);self.transition('admin');return result
        self.app._credential_loader=loader
        self.assertEqual(self.refresh()['status'],404)
        self.assertEqual(self.provider.calls,0);self.assertIsNone(self.state().snapshot)

    def test_blocked_first_page_aba_prevents_second_dispatch_and_publication(self):
        entered=Event();release=Event();calls=[];result=[]
        class Transport:
            def get(inner,path,credential,connection):
                calls.append(path);entered.set()
                if not release.wait(10):raise RuntimeError('test timeout')
                return [row(i) for i in range(1,11)]
        self.app._provider=GitCodeProvider(Transport())
        thread=Thread(target=lambda:result.append(self.refresh()))
        thread.start()
        try:
            self.assertTrue(entered.wait(10));self.transition('admin');release.set();thread.join(10)
            self.assertFalse(thread.is_alive());self.assertEqual(result[0]['status'],404)
            self.assertEqual(len(calls),1);self.assertIsNone(self.state().snapshot)
        finally:release.set();thread.join(10)

    def test_membership_and_module_aba_cancel_inflight_refresh(self):
        def member_aba():
            self.store.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
            self.store.set_membership('second','alpha','admin',role='admin',state='active',expected_revision=2)
        self.provider.hook=member_aba
        self.assertEqual(self.refresh()['status'],409)
        self.assertIsNone(self.state().snapshot)
        def module_aba():
            self.store.set_module_state('admin','alpha','community',state='disabling',expected_revision=1)
            self.store.set_module_state('admin','alpha','community',state='disabled',expected_revision=2)
            self.store.set_module_state('admin','alpha','community',state='enabled',expected_revision=3)
        self.provider.hook=module_aba
        self.assertEqual(self.refresh()['status'],409)
        self.assertIsNone(self.state().snapshot)

    def test_regrant_during_provider_work_invalidates_original_read(self):
        self.provider.hook=lambda:self.regrant(expected=1)
        self.assertEqual(self.refresh()['status'],412)
        self.assertIsNone(self.state().snapshot);self.assertEqual(self.state().grant_revision,2)


    def test_row_exhaustion_rejects_before_io_and_preserves_all_state(self):
        self.refresh();self.approve()
        self.sql("UPDATE community_connections SET revision=9007199254740991 WHERE workspace_id='alpha' AND connection_id='source'")
        before=self.sql('SELECT * FROM community_connections')
        audit=self.sql('SELECT * FROM audit');calls=self.provider.calls;loads=len(self.load_calls)
        self.assertEqual(self.approve()['status'],409)
        self.assertEqual(self.refresh()['status'],409)
        with self.assertRaises(PolicyError) as error:self.regrant()
        self.assertEqual(error.exception.status,409)
        self.assertEqual(self.sql('SELECT * FROM community_connections'),before)
        self.assertEqual(self.sql('SELECT * FROM audit'),audit)
        self.assertEqual((self.provider.calls,len(self.load_calls)),(calls,loads))
        self.assertEqual(fixtures.call(self.app)['status'],200)

    def test_grant_exhaustion_and_invalid_recipients_roll_back(self):
        value=json.loads(self.state().config_raw);value['grant_revision']=9007199254740991
        self.sql("UPDATE community_connections SET config=? WHERE workspace_id='alpha' AND connection_id='source'",(encode(value),))
        before=self.sql('SELECT * FROM community_connections');audit=self.sql('SELECT * FROM audit')
        with self.assertRaises(PolicyError) as error:self.regrant()
        self.assertEqual(error.exception.status,409)
        with self.assertRaises(PolicyError) as error:
            replace_policy(self.store,replace(fixtures.config(),credential_ref='changed'),expected_revision=1,actor_id='admin')
        self.assertEqual(error.exception.status,409)
        bad=replace(fixtures.config(),reader_ids=('missing',))
        for method in (replace_policy,regrant_policy):
            with self.assertRaises(PolicyError) as error:method(self.store,bad,expected_revision=1,actor_id='admin')
            self.assertEqual(error.exception.status,400)
        self.assertEqual(self.sql('SELECT * FROM community_connections'),before)
        self.assertEqual(self.sql('SELECT * FROM audit'),audit)

    def test_retired_history_bound_is_explicit_not_silent_pin_eviction(self):
        value=json.loads(self.state().config_raw)
        binding=value['authority']['principals']['reader']
        value['retired_principals']={f'retired-{i}':binding for i in range(2997)}
        self.sql("UPDATE community_connections SET config=? WHERE workspace_id='alpha' AND connection_id='source'",(encode(value),))
        before=self.sql('SELECT * FROM community_connections')
        bad=replace(fixtures.config(),reader_ids=('reader','admin','dev','second'))
        with self.assertRaises(PolicyError) as error:
            replace_policy(self.store,bad,expected_revision=1,actor_id='admin')
        self.assertEqual(error.exception.status,400)
        self.assertEqual(self.sql('SELECT * FROM community_connections'),before)
        self.assertFalse(self.sql('SELECT * FROM audit'))
        # Explicit regrant resets bounded retirement history; it is not eviction.
        self.assertEqual(self.regrant().retired_principals,{})


if __name__=='__main__':unittest.main()
