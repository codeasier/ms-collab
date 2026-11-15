# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Disposable identities only; no production mutation or provider connection."""
import json
import multiprocessing
from pathlib import Path
import sqlite3
import shutil
import tempfile
import unittest
from unittest.mock import patch

import test_smoke as fixtures
from packages.gui_smoke import (SmokeConnection, initialize, initialize_connections,
    migrate_v1, publish_snapshot, regrant_connection)
from packages.gui_smoke import store as records
from packages.gui_smoke.grants import load_grant
from packages.gui_smoke.model import MAX_REVISION
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError


def competing(path,configured,barrier,results,action):
    policy=SQLiteWorkspaceService(path);barrier.wait(10)
    try:
        if action=='regrant':regrant_connection(policy,configured,actor='admin',expected_grant_revision=1,expected_snapshot_revision=1,clock=lambda:fixtures.NOW)
        else:publish_snapshot(policy,configured,actor='writer',tasks=(fixtures.task(),),observed_at=fixtures.NOW.isoformat(),expected_revision=1,expected_grant_revision=1,clock=lambda:fixtures.NOW)
        results.put(200)
    except PolicyError as error:results.put(error.status)


class AuthorityTests(unittest.TestCase):
    setUp=fixtures.SmokeTests.setUp
    application=fixtures.SmokeTests.application
    publish=fixtures.SmokeTests.publish
    sql=fixtures.SmokeTests.sql

    def state(self):
        with sqlite3.connect(self.path) as db:return '\n'.join(db.iterdump())

    def regrant(self,**kw):
        args=dict(actor='admin',expected_grant_revision=1,expected_snapshot_revision=1,clock=lambda:fixtures.NOW)
        args.update(kw);return regrant_connection(self.store,self.configured,**args)

    def test_publisher_aba_never_recaptures_but_historical_reader_remains_authorized(self):
        for sql in ("UPDATE principals SET subject='writer-other' WHERE id='writer'",
                    "UPDATE principals SET subject='writer' WHERE id='writer'",
                    "UPDATE principals SET active=0 WHERE id='writer'",
                    "UPDATE principals SET active=1 WHERE id='writer'"):
            self.sql(sql);before=self.state()
            with self.assertRaises(PolicyError):self.publish(expected_revision=1,observed_at=fixtures.NOW.isoformat())
            with self.assertRaises(PolicyError):initialize_connections(self.store,(self.configured,))
            self.assertEqual(fixtures.request(self.app)['status'],200)
            self.assertEqual(before,self.state())
        self.regrant();self.publish(expected_revision=2,expected_grant_revision=2,observed_at=fixtures.NOW.isoformat())
        self.assertEqual(fixtures.request(self.app)['status'],200)

    def test_publisher_membership_loss_only_denies_new_publication(self):
        self.store.set_membership('admin','alpha','writer',role='viewer',state='active',expected_revision=1)
        with self.assertRaises(PolicyError):self.publish(expected_revision=1,observed_at=fixtures.NOW.isoformat())
        self.assertEqual(fixtures.request(self.app)['status'],200)

    def test_reader_aba_blocks_publication_until_explicit_regrant(self):
        self.sql("UPDATE principals SET subject='other' WHERE id='alice'")
        self.sql("UPDATE principals SET subject='alice' WHERE id='alice'")
        self.assertEqual(fixtures.request(self.app)['status'],404)
        self.assertEqual(fixtures.request(self.app,token='synthetic-bob')['status'],200)
        with self.assertRaises(PolicyError):self.publish(expected_revision=1,observed_at=fixtures.NOW.isoformat())
        self.regrant();self.publish(expected_revision=2,expected_grant_revision=2,observed_at=fixtures.NOW.isoformat())
        self.assertEqual(fixtures.request(self.app)['status'],200)

    def test_twice_identical_regrant_never_reuses_paging_or_publication_preconditions(self):
        self.assertEqual(self.regrant()['revision'],2)
        self.assertEqual(self.regrant(expected_grant_revision=2,expected_snapshot_revision=2)['revision'],3)
        data=fixtures.request(self.app)['body']['data'];self.assertFalse(data['available']);self.assertEqual(data['source']['revision'],3)
        summary=fixtures.request(self.app,'/summary',query='revision=3')['body']['data']
        self.assertEqual(summary['source'],data['source']);self.assertEqual(sum(summary['states'].values()),0)
        for suffix in ('/tasks','/summary'):
            self.assertEqual(fixtures.request(self.app,suffix,query='revision=1')['status'],412)
        for snapshot,grant in ((1,1),(3,1),(1,3),(2,2)):
            before=self.state()
            with self.assertRaises(PolicyError):self.publish(expected_revision=snapshot,expected_grant_revision=grant,observed_at=fixtures.NOW.isoformat())
            self.assertEqual(before,self.state())
        self.publish(expected_revision=3,expected_grant_revision=3,observed_at=fixtures.NOW.isoformat())
        self.assertEqual(fixtures.request(self.app)['body']['data']['source']['revision'],4)

    def test_regrant_requires_current_admin_and_both_preconditions(self):
        for actor in ('writer','alice','operator','unknown'):
            before=self.state()
            with self.assertRaises(PolicyError):self.regrant(actor=actor)
            self.assertEqual(before,self.state())
        for key in ('expected_grant_revision','expected_snapshot_revision'):
            for value,status in ((None,428),(True,400),(-1,400),(MAX_REVISION+1,400),(0,412)):
                before=self.state()
                with self.assertRaises(PolicyError) as caught:self.regrant(**{key:value})
                self.assertEqual(caught.exception.status,status);self.assertEqual(before,self.state())

    def test_inactive_verified_actor_is_not_an_admin_grant(self):
        for workspace in ('alpha','beta','commons'):
            self.store.set_membership('admin',workspace,'outsider',role='admin',state='active',expected_revision=1)
        self.sql("UPDATE principals SET active=0 WHERE id='admin'");before=self.state()
        with self.assertRaises(PolicyError):self.regrant()
        self.assertEqual(before,self.state())

    def test_weakened_grant_schema_is_not_recreated_or_adopted(self):
        self.sql('DROP TABLE gui_smoke_grants')
        self.sql('CREATE TABLE gui_smoke_grants (workspace_id,record)');before=self.state()
        for operation in (lambda:initialize(self.store),lambda:migrate_v1(self.store),lambda:initialize_connections(self.store,(self.configured,))):
            with self.assertRaises(StorageError):operation()
            self.assertEqual(before,self.state())
        self.assertEqual(fixtures.request(self.app)['status'],503)

    def test_grant_corruption_foreign_realm_and_missing_pins_fail_without_repair(self):
        original=self.sql('SELECT record FROM gui_smoke_grants')[0][0]
        for mutate in (lambda r:r.pop('authority'),lambda r:r['authority'].update(realm='0'*32),
                       lambda r:r['authority']['principals']['writer'].pop('generation'),lambda r:r.update(version=True)):
            value=json.loads(original);mutate(value);self.sql('UPDATE gui_smoke_grants SET record=?',(json.dumps(value,sort_keys=True,separators=(',',':')),));before=self.state()
            self.assertIn(fixtures.request(self.app)['status'],(404,503))
            with self.assertRaises((PolicyError,StorageError)):self.publish(expected_revision=1,observed_at=fixtures.NOW.isoformat())
            with self.assertRaises((PolicyError,StorageError)):initialize_connections(self.store,(self.configured,))
            self.assertEqual(before,self.state())
        self.sql('UPDATE gui_smoke_grants SET record=?',(original,))

    def test_restart_noop_never_changes_grant_or_snapshot_counters(self):
        before=self.state();initialize(SQLiteWorkspaceService(self.path));initialize_connections(SQLiteWorkspaceService(self.path),(self.configured,))
        migrate_v1(SQLiteWorkspaceService(self.path));self.assertEqual(before,self.state())
        changed=SmokeConnection(self.scope,frozenset({'bob'}),self.configured.publishers)
        with self.assertRaises(PolicyError):initialize_connections(self.store,(changed,))
        self.assertEqual(before,self.state())

    def test_mixed_formats_and_snapshot_grant_counter_mismatches_not_repair_inputs(self):
        original=self.sql('SELECT record FROM gui_smoke_snapshots')[0][0]
        changes=(lambda r:r.update(grant_revision=2),lambda r:r.update(grant_digest='0'*64),
                 lambda r:r.update(revision=2),lambda r:r['scope'].update(repository_id='other'),
                 lambda r:(r.update(version=2),r.pop('grant_revision'),r.pop('grant_digest')))
        for mutate in changes:
            value=json.loads(original);mutate(value);self.sql('UPDATE gui_smoke_snapshots SET record=?',(json.dumps(value),));before=self.state()
            with self.assertRaises(StorageError):self.regrant()
            with self.assertRaises(StorageError):self.publish(expected_revision=1,observed_at=fixtures.NOW.isoformat())
            self.assertEqual(before,self.state())
        self.sql('UPDATE gui_smoke_snapshots SET record=?',(original,));self.sql('DELETE FROM gui_smoke_grants');before=self.state()
        with self.assertRaises(StorageError):self.regrant(expected_grant_revision=0)
        with self.assertRaises(PolicyError):initialize_connections(self.store,(self.configured,))
        self.assertEqual(before,self.state())

    def test_semantically_stale_v3_can_be_explicitly_cleared(self):
        self.sql("UPDATE principals SET subject='elsewhere' WHERE id='alice'")
        self.sql("UPDATE principals SET subject='alice' WHERE id='alice'")
        self.regrant();data=fixtures.request(self.app)['body']['data']
        self.assertFalse(data['available']);self.assertEqual(data['items'],[]);self.assertIsNone(data['source']['observed_at'])

    def test_regrant_and_publication_audit_failures_roll_back_all_rows(self):
        self.sql("CREATE TRIGGER fail_gui_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        before=self.state()
        with self.assertRaises(StorageError):self.regrant()
        self.assertEqual(before,self.state())
        with self.assertRaises(StorageError):self.publish(expected_revision=1,observed_at=fixtures.NOW.isoformat())
        self.assertEqual(before,self.state())

    def test_snapshot_and_grant_limits_preserve_current_reads_without_partial_changes(self):
        original=self.sql('SELECT record FROM gui_smoke_snapshots')[0][0];raw=json.loads(original)
        raw['grant_revision']=MAX_REVISION
        self.sql('UPDATE gui_smoke_grants SET grant_revision=?',(MAX_REVISION,));self.sql('UPDATE gui_smoke_snapshots SET record=?',(json.dumps(raw),))
        before=self.state();self.assertEqual(fixtures.request(self.app)['status'],200)
        with self.assertRaises(PolicyError) as caught:self.regrant(expected_grant_revision=MAX_REVISION)
        self.assertEqual(caught.exception.status,409);self.assertEqual(before,self.state())
        raw['revision']=MAX_REVISION
        self.sql('UPDATE gui_smoke_grants SET snapshot_revision=?',(MAX_REVISION,));self.sql('UPDATE gui_smoke_snapshots SET record=?,revision=?',(json.dumps(raw),MAX_REVISION))
        before=self.state();self.assertEqual(fixtures.request(self.app)['status'],200)
        with self.assertRaises(PolicyError) as caught:self.publish(expected_revision=MAX_REVISION,expected_grant_revision=MAX_REVISION,observed_at=fixtures.NOW.isoformat())
        self.assertEqual(caught.exception.status,409);self.assertEqual(before,self.state())

    def test_explicit_legacy_schema_migration_ddl_and_final_validation_rollback(self):
        self.sql('DROP TABLE gui_smoke_grants');before=self.state()
        with patch.object(records.grants,'_SCHEMA',records.grants._SCHEMA+' INVALID'):
            with self.assertRaises(StorageError):migrate_v1(self.store)
        self.assertEqual(before,self.state())
        with patch.object(records,'validate_schema',side_effect=StorageError('synthetic')):
            with self.assertRaises(StorageError):migrate_v1(self.store)
        self.assertEqual(before,self.state())
        migrate_v1(self.store);after=self.state();migrate_v1(self.store);self.assertEqual(after,self.state())
        self.assertEqual(self.sql('SELECT * FROM gui_smoke_grants'),[])

    def test_two_process_publication_and_regrant_competitions_have_one_cas_winner(self):
        context=multiprocessing.get_context('spawn')
        for actions in (('publish','regrant'),('regrant','regrant'),('publish','publish')):
            with self.subTest(actions=actions),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'copy.sqlite';shutil.copyfile(self.path,path)
                barrier=context.Barrier(2);queue=context.Queue()
                with sqlite3.connect(path) as db:before=db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
                children=[context.Process(target=competing,args=(str(path),self.configured,barrier,queue,action)) for action in actions]
                for child in children:child.start()
                for child in children:child.join(20);self.assertEqual(child.exitcode,0)
                self.assertEqual(sorted(queue.get(timeout=5) for _ in children),[200,412]);queue.close()
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0],before+1)
                    self.assertEqual(db.execute('SELECT snapshot_revision FROM gui_smoke_grants').fetchall(),[(2,)])

    def test_public_and_audit_dtos_do_not_expose_grant_records(self):
        self.regrant();text=json.dumps(fixtures.request(self.app))+str(self.sql('SELECT * FROM audit'))
        for private in (fixtures.ISSUER,'grant_digest','policy_realm','publisher_authority','principals','credential_ref'):
            self.assertNotIn(private,text)
