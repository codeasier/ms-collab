"""Disposable authority/legacy retry states, never an enabled identity mutator."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import selectors
import subprocess
import sys
from threading import Event, Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

from packages.workspace_admin_api import initialize_administration, migrate_administration_v1
from packages.workspace_admin_api import requests
from packages.workspace_admin_api.tests import test_application as fixtures
from packages.workspaces import SQLiteWorkspaceService, StorageError
from packages.workspaces.tests.identity_fixture import simulate_identity


class RetryAuthorityTests(unittest.TestCase):
    setUp = fixtures.AdministrationTests.setUp
    request = fixtures.AdministrationTests.request
    create = fixtures.AdministrationTests.create
    install = fixtures.AdministrationTests.install

    def sql(self, query, values=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(query, values).fetchall()

    def retained(self):
        return tuple(self.sql('SELECT * FROM ' + table + ' ORDER BY 1,2') for table in
                     ('workspaces','memberships','modules','audit',requests._TABLE))

    def aba(self, actor='admin', field='subject'):
        with sqlite3.connect(self.path) as db:
            old = db.execute(f'SELECT {field} FROM principals WHERE id=?',(actor,)).fetchone()[0]
            new = 0 if field == 'active' else 'synthetic-transient'
            simulate_identity(db, f'UPDATE principals SET {field}=? WHERE id=?',(new,actor))
            simulate_identity(db, f'UPDATE principals SET {field}=? WHERE id=?',(old,actor))

    def seed(self):
        self.keys = (str(uuid4()),str(uuid4()))
        self.created = self.create(key=self.keys[0])['body']['data']
        self.assertEqual(self.install(key=self.keys[1])['status'],200)

    def conflict(self, operation, key):
        before = self.retained(); result = operation(key=key)
        self.assertEqual(result['status'],409)
        self.assertEqual(result['body'],{'error':'idempotency_conflict'})
        self.assertEqual(self.retained(),before)

    def legacy(self):
        with sqlite3.connect(self.path) as db:
            rows = db.execute('SELECT actor,action,scope,request_key,digest,result_id FROM '+requests._TABLE).fetchall()
            db.execute('DROP TABLE '+requests._TABLE);db.execute(requests._LEGACY_CREATE)
            db.executemany('INSERT INTO '+requests._LEGACY+' VALUES (?,?,?,?,?,?)',rows)
        return rows

    def test_actor_subject_aba_denies_both_old_keys_without_recreation(self):
        self.seed();self.aba()
        self.conflict(self.create,self.keys[0]);self.conflict(self.install,self.keys[1])
        self.assertEqual(len(self.sql('SELECT * FROM workspaces')),4)

    def test_actor_issuer_aba_keeps_old_key_conflict_after_restart(self):
        self.seed();self.aba(field='issuer');self.base_api._store=SQLiteWorkspaceService(self.path)
        self.conflict(self.create,self.keys[0]);self.conflict(self.install,self.keys[1])

    def test_disable_reenable_does_not_adopt_previous_actor_retry(self):
        self.seed();self.aba(field='active')
        self.conflict(self.create,self.keys[0]);self.conflict(self.install,self.keys[1])

    def test_create_target_aba_is_provenance_conflict_not_regrant(self):
        self.seed();self.aba(actor='other')
        self.conflict(self.create,self.keys[0])
        self.assertEqual(self.install(key=self.keys[1])['status'],200)

    def test_inactive_creation_target_denies_retry_after_other_admin_secured(self):
        self.seed()
        with sqlite3.connect(self.path) as db:
            for workspace, in db.execute('SELECT id FROM workspaces').fetchall():
                db.execute("INSERT OR REPLACE INTO memberships VALUES (?, 'viewer','admin','active',1)",(workspace,))
            simulate_identity(db,'UPDATE principals SET active=0 WHERE id=?',('other',))
        self.conflict(self.create,self.keys[0])

    def test_actor_as_initial_admin_has_one_exact_provenance_recipient(self):
        key=str(uuid4());body={'name':'Self','kind':'product','initial_admin':'admin'}
        first=self.create(key=key,body=body)
        stored=json.loads(self.sql('SELECT authority FROM '+requests._TABLE)[0][0])
        self.assertEqual(set(stored['principals']),{'admin'})
        self.assertEqual(self.create(key=key,body=body)['body'],first['body'])

    def test_legal_astral_identity_provenance_fits_budget_and_roundtrips(self):
        with sqlite3.connect(self.path) as db:
            simulate_identity(db,'UPDATE principals SET issuer=?,subject=? WHERE id=?',('😀'*200,'🧪'*200,'other'))
        key=str(uuid4());first=self.create(key=key)
        self.assertEqual(first['status'],200)
        self.assertEqual(self.create(key=key)['body'],first['body'])
        size=len(self.sql('SELECT authority FROM '+requests._TABLE)[0][0])
        self.assertGreater(size,4096);self.assertLess(size,requests._MAX_AUTHORITY)

    def test_foreign_realm_copied_retry_keys_conflict_before_result_lookup(self):
        self.seed();rows=self.sql('SELECT * FROM '+requests._TABLE)
        other=fixtures.AdministrationTests();other.setUp();self.addCleanup(other.doCleanups)
        with sqlite3.connect(other.path) as db:
            db.executemany('INSERT INTO '+requests._TABLE+' VALUES (?,?,?,?,?,?,?)',rows)
        # No corresponding created workspace or installed pipeline exists here.
        for call,key in ((other.create,self.keys[0]),(other.install,self.keys[1])):
            result=call(key=key);self.assertEqual(result['status'],409)
            self.assertEqual(result['body'],{'error':'idempotency_conflict'})
        self.assertEqual(len(other.store.list_control_workspaces('admin')),3)

    def test_new_key_requires_deliberate_request_not_automatic_old_key_reset(self):
        self.seed();self.aba();self.conflict(self.create,self.keys[0])
        self.assertEqual(self.create(key=str(uuid4()))['status'],200)
        self.conflict(self.create,self.keys[0])
        self.assertEqual(len(self.sql('SELECT * FROM '+requests._TABLE)),3)

    def test_current_role_check_precedes_identity_retry_record(self):
        self.seed();self.aba()
        self.sql('DELETE FROM operators')
        self.assertEqual(self.create(key=self.keys[0])['status'],403)
        self.sql("UPDATE memberships SET role='admin' WHERE workspace_id='alpha' AND principal_id='viewer'")
        self.sql("UPDATE memberships SET role='developer' WHERE workspace_id='alpha' AND principal_id='admin'")
        self.assertEqual(self.install(key=self.keys[1])['status'],403)

    def test_malformed_bound_provenance_fails_closed_without_rewrite(self):
        key=str(uuid4());self.create(key=key)
        original=self.sql('SELECT authority FROM '+requests._TABLE)[0][0]
        data=json.loads(original)
        candidates=['{}','null','['*50,original.replace('"version":1','"version":true'),
                    original.replace('"version":1','"version":1,"version":1'),
                    original.replace('"generation":1','"generation":1.0'), 'x'*16385]
        for field in ('realm','principals'):
            changed=dict(data);del changed[field];candidates.append(json.dumps(changed))
        for value in candidates:
            with self.subTest(value=value[:40]):
                self.sql('UPDATE '+requests._TABLE+' SET authority=?',(value,));before=self.retained()
                self.assertEqual(self.create(key=key)['status'],503);self.assertEqual(self.retained(),before)

    def test_nonnull_bad_digest_or_missing_result_never_becomes_new_mutation(self):
        self.seed()
        for column,value,call,key in [('digest','bad',self.create,self.keys[0]),
                 ('result_id','',self.create,self.keys[0]),('result_id',str(uuid4()),self.create,self.keys[0]),
                 ('result_id','absent-module',self.install,self.keys[1])]:
            with self.subTest(column=column,value=value):
                old=self.sql('SELECT '+column+' FROM '+requests._TABLE+' WHERE request_key=?',(key,))[0][0]
                self.sql('UPDATE '+requests._TABLE+' SET '+column+'=? WHERE request_key=?',(value,key));before=self.retained()
                self.assertEqual(call(key=key)['status'],503);self.assertEqual(self.retained(),before)
                self.sql('UPDATE '+requests._TABLE+' SET '+column+'=? WHERE request_key=?',(old,key))

    def test_runtime_coexisting_old_table_and_unknown_version_deny_both_posts(self):
        self.seed()
        for definition,name in ((requests._LEGACY_CREATE,requests._LEGACY),
                                ('CREATE TABLE workspace_admin_requests_v3(x)','workspace_admin_requests_v3')):
            self.sql(definition);before=self.retained()
            self.assertEqual(self.create(key=self.keys[0])['status'],503)
            self.assertEqual(self.install(key=self.keys[1])['status'],503)
            with self.assertRaises(StorageError):initialize_administration(self.store)
            self.assertEqual(self.retained(),before);self.sql('DROP TABLE '+name)

    def test_runtime_attached_index_or_trigger_denies_retry(self):
        self.seed()
        for create,drop in ((f'CREATE INDEX custom_retry ON {requests._TABLE}(digest)','DROP INDEX custom_retry'),
            (f'CREATE TRIGGER custom_retry AFTER INSERT ON {requests._TABLE} BEGIN SELECT 1; END','DROP TRIGGER custom_retry')):
            self.sql(create);before=self.retained()
            self.assertEqual(self.create(key=self.keys[0])['status'],503)
            self.assertEqual(self.install(key=self.keys[1])['status'],503)
            self.assertEqual(self.retained(),before);self.sql(drop)

    def test_v1_requires_explicit_migration_and_all_keys_become_permanent_tombstones(self):
        self.seed();rows=self.legacy();audit=self.sql('SELECT * FROM audit')
        self.assertEqual(self.create(key=self.keys[0])['status'],503)
        with self.assertRaises(StorageError):initialize_administration(self.store)
        self.assertEqual(migrate_administration_v1(self.store),2)
        self.assertEqual(self.sql('SELECT actor,action,scope,request_key,digest,result_id FROM '+requests._TABLE),rows)
        self.assertEqual(self.sql('SELECT authority FROM '+requests._TABLE),[(None,),(None,)])
        self.assertEqual(self.sql('SELECT * FROM audit'),audit)
        self.base_api._store=SQLiteWorkspaceService(self.path)
        self.conflict(self.create,self.keys[0]);self.conflict(self.install,self.keys[1])
        self.assertEqual(self.create(key=self.keys[0],body={'name':'Other','kind':'shared','initial_admin':'admin'})['status'],409)
        self.sql('UPDATE '+requests._TABLE+' SET result_id=?',(str(uuid4()),))
        self.conflict(self.create,self.keys[0]);self.conflict(self.install,self.keys[1])
        self.assertEqual(self.create()['status'],200)

    def test_explicit_empty_v1_upgrade_never_happens_on_initialization(self):
        self.legacy()
        with self.assertRaises(StorageError):initialize_administration(self.store)
        self.assertEqual(migrate_administration_v1(self.store),0)
        self.assertEqual(self.create()['status'],200)

    def test_migration_retains_lost_authority_inactive_actor_and_unavailable_results(self):
        self.seed();rows=self.legacy()
        with sqlite3.connect(self.path) as db:
            for workspace, in db.execute('SELECT id FROM workspaces').fetchall():
                db.execute("INSERT OR REPLACE INTO memberships VALUES (?, 'viewer','admin','active',1)",(workspace,))
            db.execute('DELETE FROM operators')
            db.execute("UPDATE memberships SET state='revoked' WHERE principal_id='admin'")
            db.execute("UPDATE workspaces SET state='archived' WHERE id=?",(self.created['id'],))
            db.execute("UPDATE workspaces SET state='suspended' WHERE id='alpha'")
            simulate_identity(db,'UPDATE principals SET active=0 WHERE id=?',('admin',))
        audit=self.sql('SELECT * FROM audit')
        self.assertEqual(migrate_administration_v1(self.store),2)
        self.assertEqual(self.sql('SELECT actor,action,scope,request_key,digest,result_id FROM '+requests._TABLE),rows)
        self.assertEqual(self.sql('SELECT authority FROM '+requests._TABLE),[(None,),(None,)])
        self.assertEqual(self.sql('SELECT * FROM audit'),audit)

    def test_malformed_legacy_record_aborts_whole_conversion_without_filtering(self):
        self.seed();self.legacy()
        for column,value in [('digest','bad'),('request_key','not-a-uuid'),('result_id',str(uuid4()))]:
            key='workspace.create'
            old=self.sql('SELECT '+column+' FROM '+requests._LEGACY+' WHERE action=?',(key,))[0][0]
            self.sql('UPDATE '+requests._LEGACY+' SET '+column+'=? WHERE action=?',(value,key))
            before=self.sql('SELECT * FROM '+requests._LEGACY)
            with self.assertRaises(StorageError):migrate_administration_v1(self.store)
            self.assertEqual(self.sql('SELECT * FROM '+requests._LEGACY),before)
            self.assertEqual(self.sql('SELECT name FROM sqlite_schema WHERE name=?',(requests._TABLE,)),[])
            self.sql('UPDATE '+requests._LEGACY+' SET '+column+'=? WHERE action=?',(old,key))

    def test_migration_unknown_schema_or_auxiliary_objects_fail_without_write(self):
        self.seed();self.legacy()
        self.sql(f'CREATE INDEX altered_legacy ON {requests._LEGACY}(digest)')
        with self.assertRaises(StorageError):migrate_administration_v1(self.store)
        self.sql('DROP INDEX altered_legacy');self.sql(requests._CREATE)
        with self.assertRaises(StorageError):migrate_administration_v1(self.store)
        self.assertEqual(len(self.sql('SELECT * FROM '+requests._LEGACY)),2)
        self.assertEqual(self.sql('SELECT * FROM '+requests._TABLE),[])

    def test_each_migration_ddl_copy_and_final_validation_failure_roll_back(self):
        self.seed();self.legacy();before=self.sql('SELECT * FROM '+requests._LEGACY)
        connect=self.store._connect
        class FaultConnection:
            def __init__(self,db,step):self.db,self.step=db,step
            def __getattr__(self,name):return getattr(self.db,name)
            def execute(self,sql,*args):
                result=self.db.execute(sql,*args)
                if sql.startswith(self.step):raise sqlite3.OperationalError('synthetic failure after DDL')
                return result
            def executemany(self,sql,*args):
                result=self.db.executemany(sql,*args)
                if sql.startswith(self.step):raise sqlite3.OperationalError('synthetic failure after copy')
                return result
        for step in ('CREATE TABLE '+requests._TABLE,'INSERT INTO '+requests._TABLE,'DROP TABLE '+requests._LEGACY):
            with self.subTest(step=step),patch.object(self.store,'_connect',side_effect=lambda:FaultConnection(connect(),step)):
                with self.assertRaises(StorageError):migrate_administration_v1(self.store)
            self.assertEqual(self.sql('SELECT * FROM '+requests._LEGACY),before)
            self.assertEqual(self.sql('SELECT name FROM sqlite_schema WHERE name=?',(requests._TABLE,)),[])
        for target in ((requests,'_check_schema'),(self.store,'_persist')):
            with patch.object(*target,side_effect=StorageError('synthetic final failure')):
                with self.assertRaises(StorageError):migrate_administration_v1(self.store)
            self.assertEqual(self.sql('SELECT * FROM '+requests._LEGACY),before)
            self.assertEqual(self.sql('SELECT name FROM sqlite_schema WHERE name=?',(requests._TABLE,)),[])

    def test_failure_after_actual_persist_rolls_back_business_retry_and_audit(self):
        before=self.retained();persist=self.store._persist
        def fail(*args,**kwargs):persist(*args,**kwargs);raise StorageError('synthetic post-write failure')
        with patch.object(self.store,'_persist',side_effect=fail):
            self.assertEqual(self.create()['status'],503)
        self.assertEqual(self.retained(),before)

    def test_concurrent_same_key_has_one_bound_record_and_one_audit(self):
        key=str(uuid4())
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(lambda _:self.create(key=key),range(4)))
        self.assertTrue(all(r['status']==200 for r in results))
        self.assertTrue(all(r['body']==results[0]['body'] for r in results))
        self.assertEqual(len(self.sql('SELECT * FROM '+requests._TABLE)),1)
        self.assertEqual(len(self.sql("SELECT * FROM audit WHERE action='workspace.create'")),1)

    def _locked_retry(self, actor):
        self.seed();before=self.retained()
        code="""import sqlite3,sys
from packages.workspaces.tests.identity_fixture import simulate_identity
with sqlite3.connect(sys.argv[1]) as db:
 db.execute('BEGIN IMMEDIATE')
 simulate_identity(db,'UPDATE principals SET subject=? WHERE id=?',('temporary',sys.argv[2]))
 simulate_identity(db,'UPDATE principals SET subject=? WHERE id=?',(sys.argv[2]+'-sub',sys.argv[2]))
 print('locked',flush=True)
 if sys.stdin.readline().strip()!='commit':raise RuntimeError('no synthetic commit')
"""
        child=subprocess.Popen([sys.executable,'-c',code,str(self.path),actor],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout,selectors.EVENT_READ)
                self.assertTrue(ready.select(timeout=5),'synthetic writer did not become ready')
            self.assertEqual(child.stdout.readline().strip(),'locked')
            entered=Event();connect=self.store._connect;result=[]
            def observed():
                db=connect()
                db.set_trace_callback(lambda sql: entered.set() if sql=='BEGIN IMMEDIATE' else None)
                return db
            with patch.object(self.store,'_connect',side_effect=observed):
                worker=Thread(target=lambda:result.append(self.create(key=self.keys[0])));worker.start()
                self.assertTrue(entered.wait(2));self.assertTrue(worker.is_alive())
                out,err=child.communicate('commit\n',timeout=5)
                self.assertEqual((child.returncode,err),(0,''));worker.join(5)
                self.assertFalse(worker.is_alive());self.assertEqual(result[0]['status'],409)
            self.assertEqual(self.retained(),before)
        finally:
            if child.poll() is None:child.kill()
            child.communicate(timeout=5)

    def test_separate_process_lock_wait_observes_committed_actor_aba_before_retry(self):
        self._locked_retry('admin')

    def test_separate_process_lock_wait_observes_committed_target_aba_before_retry(self):
        self._locked_retry('other')

    def test_duplicate_create_in_two_independent_processes_has_one_result(self):
        key=str(uuid4())
        code="""import json,sys
from packages.workspace_api.tests import test_application as f
from packages.workspace_admin_api import WorkspaceAdministrationApplication
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import SQLiteWorkspaceService
api=WorkspaceApplication(store=SQLiteWorkspaceService(sys.argv[1]),providers=(f.Provider('test',f.ISSUER,'test-api',f.SyntheticVerifier()),),provider_key='test',clock=lambda:f.NOW)
app=WorkspaceAdministrationApplication(api=api)
print('ready',flush=True)
if sys.stdin.readline().strip()!='go':raise RuntimeError('no start')
result=f.call(app,'/api/control/workspaces',method='POST',body={'name':'Concurrent','kind':'product','initial_admin':'other'},extra={'HTTP_IDEMPOTENCY_KEY':sys.argv[2]})
print(json.dumps(result),flush=True)
"""
        children=[]
        try:
            for _ in range(2):
                child=subprocess.Popen([sys.executable,'-c',code,str(self.path),key],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                children.append(child)
                with selectors.DefaultSelector() as ready:
                    ready.register(child.stdout,selectors.EVENT_READ)
                    self.assertTrue(ready.select(timeout=5))
                self.assertEqual(child.stdout.readline().strip(),'ready')
            for child in children:child.stdin.write('go\n');child.stdin.flush()
            results=[]
            for child in children:
                out,err=child.communicate(timeout=5)
                self.assertEqual((child.returncode,err),(0,''));results.append(json.loads(out))
            self.assertEqual([r['status'] for r in results],[200,200])
            self.assertEqual(results[0]['body'],results[1]['body'])
            self.assertEqual(len(self.sql('SELECT * FROM '+requests._TABLE)),1)
            self.assertEqual(len(self.sql("SELECT * FROM audit WHERE action='workspace.create'")),1)
        finally:
            for child in children:
                if child.poll() is None:child.kill()
                child.communicate(timeout=5)
