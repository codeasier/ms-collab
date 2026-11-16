# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic byte grant epochs and reviewed-parent CAS. No cloud requests."""
from dataclasses import replace
import sqlite3
import unittest

import test_download as fixture_cases
from packages.artifact_download import regrant_downloads, DownloadInterrupted
from packages.artifact_download import records
from packages.artifact_sync import regrant_index,initialize_indexes
from packages.artifact_sync import records as index
from packages.workspaces import PolicyError,StorageError
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant as simulate_identity
from fixtures import POLICY,SCOPE,user,page


class ByteAuthorityTests(unittest.TestCase):
    setUp=fixture_cases.DownloadTests.setUp
    call=fixture_cases.DownloadTests.call
    leases=fixture_cases.DownloadTests.leases

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT * FROM artifact_download_policies_v1').fetchall(),db.execute('SELECT * FROM audit').fetchall()

    def preconditions(self):
        def get(policy,db):
            old=records.load(db,SCOPE);parent=index.load(db,SCOPE)
            return dict(expected_grant_revision=old.grant_revision,expected_policy_digest=index.digest(old.raw),
                expected_parent_grant_revision=parent.grant_revision,expected_parent_policy_digest=index.digest(parent.policy_raw))
        return self.store._transaction(get,write=False,with_connection=True)

    def regrant(self,config=None,conditions=None):
        return regrant_downloads(self.store,config or fixture_cases.GRANT,actor_id='admin',**(conditions or self.preconditions()))

    def test_same_byte_regrant_twice_never_restores_old_stream_or_cas(self):
        stream=self.call(consume=False)['stream'];old=self.preconditions()
        first=self.regrant();second=self.regrant()
        self.assertEqual((first.grant_revision,second.grant_revision),(2,3))
        with self.assertRaises(PolicyError) as error:self.regrant(conditions=old)
        self.assertEqual(error.exception.status,412)
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertEqual(self.call()['status'],200)

    def test_parent_regrant_requires_reviewed_new_parent_and_explicit_byte_regrant(self):
        old=self.preconditions();before=self.snapshot()
        regrant_index(self.store,POLICY,actor_id='admin',expected_revision=3,expected_grant_revision=1)
        with self.assertRaises(PolicyError) as error:self.regrant(conditions=old)
        self.assertEqual(error.exception.status,412)
        self.assertEqual(self.snapshot()[0],before[0])
        self.listing.calls=[]
        self.listing.pages=[page(((fixture_cases.KEY,fixture_cases.BINDING.modified_at,len(fixture_cases.DATA)),))]
        candidate=self.service.refresh(user('admin'),SCOPE,expected_revision=4)
        self.service.approve(user('approver'),SCOPE,expected_revision=5,candidate_digest=candidate['candidate_digest'])
        self.assertEqual(self.call(revision=6)['status'],404)
        self.regrant();self.assertEqual(self.call(revision=6)['status'],200)

    def test_parent_routing_race_cannot_silently_adopt_new_origin_or_credential(self):
        old=self.preconditions()
        regrant_index(self.store,replace(POLICY,credential_ref='replacement-reference'),actor_id='admin',expected_revision=3,expected_grant_revision=1)
        before=self.snapshot()
        with self.assertRaises(PolicyError):self.regrant(conditions=old)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.loads,[]);self.assertEqual(self.objects.calls,[])

    def test_legacy_bytes_denied_and_initializer_cannot_upgrade(self):
        with sqlite3.connect(self.path) as db:db.execute('UPDATE artifact_download_policies_v1 SET policy=?',(records.policy_text(fixture_cases.GRANT),))
        before=self.snapshot()
        self.assertEqual(self.call()['status'],503)
        with self.assertRaises((StorageError,ValueError)):records.initialize_downloads(self.store,(fixture_cases.GRANT,))
        self.assertEqual(self.snapshot(),before)
        def legacy(policy,db):
            old=records.load(db,SCOPE,allow_legacy=True);parent=index.load(db,SCOPE)
            return dict(expected_grant_revision=0,expected_policy_digest=index.digest(old.raw),expected_parent_grant_revision=parent.grant_revision,expected_parent_policy_digest=index.digest(parent.policy_raw))
        args=self.store._transaction(legacy,write=False,with_connection=True)
        self.assertEqual(self.regrant(conditions=args).grant_revision,1)
        self.assertEqual(self.call()['status'],200)

    def test_reader_aba_and_reactivation_do_not_restore_byte_authority(self):
        with sqlite3.connect(self.path) as db:
            old=db.execute("SELECT subject FROM principals WHERE id='reader'").fetchone()[0]
            simulate_identity(db,"UPDATE principals SET subject='replacement' WHERE id='reader'")
            simulate_identity(db,"UPDATE principals SET subject=? WHERE id='reader'",(old,))
        self.assertEqual(self.call()['status'],404)
        with self.assertRaises((ValueError,StorageError)):records.initialize_downloads(self.store,(fixture_cases.GRANT,))
        self.assertEqual(self.loads,[]);self.assertEqual(self.leases(),[])

    def test_regrant_audit_failure_keeps_existing_owned_lease_and_grant(self):
        stream=self.call(consume=False)['stream'];before=self.snapshot();leases=self.leases()
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER deny_byte_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(StorageError):self.regrant()
        self.assertEqual(self.snapshot(),before);self.assertEqual(self.leases(),leases)
        stream.close();self.assertEqual(self.leases()[0][1],'interrupted')

    def test_byte_grant_max_does_not_block_valid_read_but_cannot_regrant(self):
        import json
        with sqlite3.connect(self.path) as db:
            value=json.loads(db.execute('SELECT policy FROM artifact_download_policies_v1').fetchone()[0]);value['grant_revision']=index.MAX_REVISION
            db.execute('UPDATE artifact_download_policies_v1 SET policy=?',(index.dump(value),))
        before=self.snapshot()
        with self.assertRaises(PolicyError) as error:self.regrant()
        self.assertEqual(error.exception.status,409);self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.call()['status'],200)

    def test_role_aba_between_acquisition_and_output_cancels_stream(self):
        stream=self.call(consume=False)['stream']
        self.store.set_membership('admin','alpha','reader',role='developer',state='active',expected_revision=1)
        self.store.set_membership('admin','alpha','reader',role='viewer',state='active',expected_revision=2)
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertEqual(self.leases()[0][1],'interrupted')


if __name__=='__main__':unittest.main()
