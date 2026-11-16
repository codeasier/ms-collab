# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic persistent grants, raw approval provenance and explicit migrations."""
from dataclasses import replace
import json
import multiprocessing
import sqlite3
import unittest
from unittest.mock import patch

import test_service as fixture_cases
from fixtures import POLICY,SCOPE,NOW,user,call
from packages.artifact_sync import records,regrant_index,initialize_indexes,ProviderError
from packages.workspaces import PolicyError,StorageError,SQLiteWorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant as simulate_identity


def race_regrant(path,barrier,queue):
    try:
        store=SQLiteWorkspaceService(path);barrier.wait(10)
        result=regrant_index(store,POLICY,actor_id='admin',expected_revision=1,expected_grant_revision=1)
        queue.put(('ok',result.grant_revision))
    except PolicyError as e:queue.put(('denied',e.status))
    except Exception as e:queue.put(('unexpected',type(e).__name__))


class IndexAuthorityTests(unittest.TestCase):
    setUp=fixture_cases.IndexTests.setUp
    refresh=fixture_cases.IndexTests.refresh
    approve=fixture_cases.IndexTests.approve
    state=fixture_cases.IndexTests.state
    next_page=fixture_cases.IndexTests.next_page
    query=fixture_cases.IndexTests.query

    def sql(self,query,args=()):
        with sqlite3.connect(self.path) as db:return db.execute(query,args).fetchall()

    def record(self):
        return self.store._transaction(lambda p,c:records.load(c,SCOPE),write=False,with_connection=True)

    def regrant(self,config=POLICY):
        old=self.record()
        return regrant_index(self.store,config,actor_id='admin',expected_revision=old.revision,expected_grant_revision=old.grant_revision)

    def aba(self,actor):
        with sqlite3.connect(self.path) as db:
            subject=db.execute('SELECT subject FROM principals WHERE id=?',(actor,)).fetchone()[0]
            simulate_identity(db,"UPDATE principals SET subject='changed' WHERE id=?",(actor,))
            simulate_identity(db,'UPDATE principals SET subject=? WHERE id=?',(subject,actor))

    def test_current_publication_pin_does_not_override_unbound_membership(self):
        self.approve();record=self.record()
        actor=record.provenance['actor']
        self.sql("UPDATE membership_authority SET realm=NULL,issuer=NULL,subject=NULL,generation=NULL WHERE workspace_id=? AND principal_id=?",(SCOPE.workspace_id,actor))
        self.assertFalse(self.store._transaction(lambda p:records.publication_current(p,record),write=False))

    def test_current_membership_does_not_restore_reader_or_approver_aba(self):
        self.approve();self.aba('reader')
        self.assertEqual(call(self.app)['status'],404)
        self.aba('approver')
        self.assertEqual(call(self.app,token='admin')['status'],404)
        with self.assertRaises(PolicyError):self.service.review(user('approver'),SCOPE,expected_revision=3)
        self.regrant();self.next_page()
        result=self.refresh(4);self.approve(result)
        self.assertEqual(call(self.app)['status'],200)
        with self.assertRaises(PolicyError) as error:self.store.set_principal('admin','reader',active=True)
        self.assertEqual(error.exception.status,503)

    def test_same_dto_regrant_advances_and_old_cas_and_initializer_do_not_reset(self):
        self.approve();first=self.regrant();second=self.regrant()
        self.assertEqual((first.grant_revision,second.grant_revision),(2,3))
        self.assertIsNone(second.published_raw);self.assertIsNone(second.candidate)
        before=self.state();initialize_indexes(self.store,(POLICY,));self.assertEqual(self.state(),before)
        with self.assertRaises(PolicyError):
            regrant_index(self.store,POLICY,actor_id='admin',expected_revision=first.revision,expected_grant_revision=first.grant_revision)
        self.assertEqual(self.state(),before)

    def test_legacy_config_and_publication_not_adopted_by_reads_or_initializer(self):
        self.approve();old=self.record()
        self.sql('UPDATE artifact_indexes_v1 SET policy=?,published=?',(records.policy_text(old.policy),old.published))
        before=self.state()
        self.assertEqual(call(self.app)['status'],503)
        with self.assertRaises((ValueError,StorageError)):initialize_indexes(self.store,(POLICY,))
        self.assertEqual(self.state(),before)
        new=regrant_index(self.store,POLICY,actor_id='admin',expected_revision=3,expected_grant_revision=0)
        self.assertEqual((new.revision,new.grant_revision),(4,1));self.assertIsNone(new.published_raw)

    def test_mixed_legacy_policy_and_bound_publication_cannot_reset_known_authority(self):
        self.approve();old=self.record()
        self.sql('UPDATE artifact_indexes_v1 SET policy=?',(records.policy_text(old.policy),))
        before=self.state();audit=self.sql('SELECT * FROM audit')
        with self.assertRaises(PolicyError) as error:
            regrant_index(self.store,POLICY,actor_id='admin',expected_revision=3,expected_grant_revision=0)
        self.assertEqual(error.exception.status,400)
        self.assertEqual(self.state(),before);self.assertEqual(self.sql('SELECT * FROM audit'),audit)
        # The inverse mixed shape is not accepted as a current bound record either.
        self.sql('UPDATE artifact_indexes_v1 SET policy=?,published=?',(old.policy_raw,old.published))
        before=self.state()
        with self.assertRaises(PolicyError):self.regrant()
        self.assertEqual(self.state(),before);self.assertEqual(self.sql('SELECT * FROM audit'),audit)

    def test_initializer_cannot_recapture_stale_and_foreign_grants(self):
        self.aba('reader');before=self.state()
        with self.assertRaises((ValueError,StorageError)):initialize_indexes(self.store,(POLICY,))
        self.assertEqual(self.state(),before)
        value=json.loads(self.record().policy_raw);value['authority']['realm']='0'*32
        self.sql('UPDATE artifact_indexes_v1 SET policy=?',(records.dump(value),))
        self.assertEqual(call(self.app)['status'],404)
        self.assertEqual(self.record().policy_raw,records.dump(value))

    def test_membership_aba_hides_approval_refresh_keeps_exact_old_raw_until_new_approval(self):
        self.approve();raw=self.record().published_raw
        self.store.set_membership('admin','alpha','approver',role='developer',state='active',expected_revision=1)
        self.store.set_membership('admin','alpha','approver',role='admin',state='active',expected_revision=2)
        self.assertEqual(call(self.app)['status'],404)
        self.next_page();result=self.refresh(3)
        self.assertEqual(self.record().published_raw,raw)
        self.assertEqual(call(self.app)['status'],404)
        self.assertEqual(self.service.review(user('approver'),SCOPE,expected_revision=4)['candidate_digest'],result['candidate_digest'])
        self.approve(result);self.assertEqual(call(self.app)['status'],200)
        self.assertNotEqual(self.record().published_raw,raw)

    def test_empty_publication_still_needs_proof_and_bad_envelope_not_repaired(self):
        self.transport.pages=[__import__('fixtures').page(())];self.approve()
        value=json.loads(self.record().published_raw);self.assertEqual(value['snapshot']['artifacts'],[])
        value.pop('provenance');self.sql('UPDATE artifact_indexes_v1 SET published=?',(records.dump(value),))
        before=self.state();loads=len(self.loader_calls)
        self.assertEqual(call(self.app)['status'],503)
        with self.assertRaises(StorageError):self.service.review(user('approver'),SCOPE,expected_revision=3)
        with self.assertRaises(StorageError):self.refresh(3)
        self.assertEqual(self.state(),before);self.assertEqual(len(self.loader_calls),loads)

    def test_row_max_and_grant_max_do_not_overflow_or_dispatch(self):
        self.sql('UPDATE artifact_indexes_v1 SET revision=?',(records.MAX_REVISION,))
        before=self.state()
        with self.assertRaises(PolicyError) as error:self.refresh(records.MAX_REVISION)
        self.assertEqual(error.exception.status,409);self.assertEqual(self.loader_calls,[])
        with self.assertRaises(PolicyError):self.regrant()
        self.assertEqual(self.state(),before)
        self.sql('UPDATE artifact_indexes_v1 SET revision=1')
        value=json.loads(self.record().policy_raw);value['grant_revision']=records.MAX_REVISION
        self.sql('UPDATE artifact_indexes_v1 SET policy=?',(records.dump(value),))
        with self.assertRaises(PolicyError):self.regrant()
        # Exhausted grant counter alone does not prevent ordinary authorized work.
        self.assertEqual(self.refresh()['revision'],2)

    def test_audit_failure_rolls_back_clear_grant_and_revision(self):
        self.approve();before=self.state()
        self.sql("CREATE TRIGGER fail_grant BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(StorageError):self.regrant()
        self.assertEqual(self.state(),before)

    def test_two_process_exact_regrant_cas_has_one_winner(self):
        ctx=multiprocessing.get_context('fork');barrier=ctx.Barrier(2);queue=ctx.Queue()
        workers=[ctx.Process(target=race_regrant,args=(str(self.path),barrier,queue)) for _ in range(2)]
        try:
            for w in workers:w.start()
            result=[queue.get(timeout=15) for _ in workers]
            for w in workers:w.join(10);self.assertEqual(w.exitcode,0)
            self.assertEqual(sorted(result),[('denied',412),('ok',2)])
            self.assertEqual(len(self.sql('SELECT * FROM audit')),1)
        finally:
            for w in workers:
                if w.is_alive():w.kill();w.join()
            queue.close()

    def test_bearer_page_guard_cancels_role_aba_without_second_page(self):
        def aba():
            self.store.set_membership('approver','alpha','admin',role='developer',state='active',expected_revision=1)
            self.store.set_membership('approver','alpha','admin',role='admin',state='active',expected_revision=2)
        self.transport.hook=aba;before=self.state()
        with self.assertRaises(PolicyError):self.refresh()
        self.assertEqual(len(self.transport.calls),1);self.assertEqual(self.state(),before)

    def test_largest_legal_proof_fits_reserved_budget_and_exact_oversize_rejects(self):
        from packages.artifacts import Snapshot,Artifact
        snapshot=records.snapshot_text(Snapshot(SCOPE,'2026-01-01T00:00:00Z',(),0))
        principal='\U0001f600'*200
        proof={'actor':principal,'authority':{'version':1,'realm':'a'*32,
            'principals':{principal:{'issuer':principal,'subject':principal,'generation':records.MAX_REVISION}}},
            'membership_revision':records.MAX_REVISION,'policy_digest':'a'*64,'snapshot_digest':records.digest(snapshot)}
        self.assertLess(len(records.publication_text(snapshot,proof))-len(snapshot),records.PUBLICATION_RESERVE)
        rows=[];raw=snapshot
        for n in range(500):
            proposed=(*rows,Artifact(f'builds/{n}/'+'\U0001f600'*200,'2026-01-01T00:00:00Z',0))
            try:next_raw=records.snapshot_text(Snapshot(SCOPE,'2026-01-01T00:00:00Z',proposed,0))
            except ValueError:break
            rows=list(proposed);raw=next_raw
        self.assertGreater(len(raw),records._MAX_BYTES-records.PUBLICATION_RESERVE)
        proof['snapshot_digest']=records.digest(raw)
        with self.assertRaises(ValueError):records.publication_text(raw,proof)

    def test_archive_and_module_restore_keep_valid_historical_publication_only(self):
        self.approve()
        self.store.set_module_state('admin','alpha','artifacts',state='disabling',expected_revision=1)
        self.assertEqual(call(self.app)['status'],409)
        self.store.set_module_state('admin','alpha','artifacts',state='disabled',expected_revision=2)
        self.store.set_module_state('admin','alpha','artifacts',state='enabled',expected_revision=3)
        self.assertEqual(call(self.app)['status'],200)
        self.store.set_workspace_state('admin','alpha',state='archived',expected_revision=1)
        self.assertEqual(call(self.app)['status'],200)
        with self.assertRaises(PolicyError):self.refresh(3)

    def test_new_candidate_budget_rejection_is_atomic(self):
        before=self.state()
        with patch('packages.artifact_sync.service.snapshot_text',return_value='x'*(records._MAX_BYTES-records.PUBLICATION_RESERVE+1)):
            with self.assertRaises(ProviderError):self.refresh()
        self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main()
