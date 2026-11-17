"""Real standalone Bearer adapters and spawned SQLite workers; synthetic only."""
from datetime import timedelta
from pathlib import Path
import multiprocessing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.artifact_admin import ArtifactAdministrationAPI
from packages.artifact_sync import ArtifactSynchronizer, IndexedArtifactApplication, Connection, IndexPolicy, initialize_indexes
from packages.artifact_sync import records
from packages.artifacts import Scope, Snapshot
from packages.directory_cursors import ReceiptStore, initialize_directory_cursors
from packages.directory_cursors.tests.support import initialize, NOW, ISSUER, Clock, SyntheticVerifier
from packages.identity import Provider
from packages.pipeline_api import PipelineApplication, encode_snapshots, resource_id
from packages.pipelines import PipelineScope
from packages.workspaces import SQLiteWorkspaceService
from packages.workspace_api.tests.test_application import call

NAMES = ('pipelines', 'artifacts', 'artifact-admin')


def forbidden(*args, **kwargs):
    raise AssertionError('directory must not acquire upstream data or credentials')


def adapters(policy, clock, *, enabled=True):
    args=dict(store=policy, providers=(Provider('synthetic',ISSUER,'synthetic',SyntheticVerifier(),timedelta(hours=1)),),
              provider_key='synthetic', clock=clock)
    receipts=ReceiptStore(policy,clock=clock) if enabled else None
    service=ArtifactSynchronizer(**args,credential_loader=forbidden)
    return (PipelineApplication(**args,directory_cursors=receipts),
            IndexedArtifactApplication(**args,directory_cursors=receipts),
            ArtifactAdministrationAPI(service=service,directory_cursors=receipts))


def provision(path):
    policy=initialize(path)
    scopes=[Scope(w,'source',f'p{i:03d}') for w in ('alpha','beta','commons') for i in range(26)]
    initialize_indexes(policy,[IndexPolicy(Connection(s,'synthetic-bucket','eu-west-101'),'synthetic-ref',
                               ('admin','other'),('admin','other'),('admin','other')) for s in scopes])
    def publish(current,db):
        for scope in scopes:
            record=records.load(db,scope)
            raw=records.snapshot_text(Snapshot(scope,'2025-12-31T00:00:00Z',()))
            proof=records.capture_publication(current,'admin',record,raw)
            db.execute('UPDATE artifact_indexes_v1 SET published=?,published_revision=revision WHERE workspace=? AND connection=? AND bucket=?',
                       (records.publication_text(raw,proof),*records.scope_key(scope)))
    policy._transaction(publish,write=True,with_connection=True)
    with sqlite3.connect(path) as db:
        for s in scopes:
            scope=PipelineScope(s.workspace_id,s.connection_id,s.bucket_id)
            db.execute('INSERT INTO resources VALUES(?,?,?,?,1)',(s.workspace_id,'pipelines',resource_id(scope),encode_snapshots(scope,[])))
    initialize_directory_cursors(policy,layout='explicit-factory-v1')
    return policy


def process_request(path, namespace, after, queue, ready=None, release=None, attempting=None):
    try:
        policy=SQLiteWorkspaceService(path,timeout=5);apps=adapters(policy,Clock())
        if attempting is not None:
            original=policy._connect
            def connect():
                db=original();db.set_trace_callback(lambda sql:attempting.set() if sql=='BEGIN IMMEDIATE' else None)
                return db
            policy._connect=connect
        if ready is not None:
            ready.set()
            if not release.wait(10):raise RuntimeError('synthetic barrier timeout')
        result=call(apps[NAMES.index(namespace)],f'/api/workspaces/alpha/{namespace}/scopes',token='admin',
                    query='' if after is None else 'after='+after)
        queue.put(('ok',result))
    except Exception as error:queue.put(('error',type(error).__name__))


class BearerDirectoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'policy.sqlite';self.policy=provision(self.path)
        self.clock=Clock();self.apps=adapters(self.policy,self.clock)

    def call(self,index,workspace='alpha',after=None,token='admin',query=None):
        return call(self.apps[index],f'/api/workspaces/{workspace}/{NAMES[index]}/scopes',token=token,
                    query=query if query is not None else '' if after is None else 'after='+after)

    def test_three_real_adapters_opaque_two_pages_and_no_business_writes(self):
        with sqlite3.connect(self.path) as db:
            before=(db.execute('SELECT * FROM resources').fetchall(),db.execute('SELECT * FROM artifact_indexes_v1').fetchall(),db.execute('SELECT * FROM audit').fetchall())
        with patch.object(self.policy,'_persist',side_effect=forbidden):
            for i in range(3):
                first=self.call(i);self.assertEqual(first['status'],200,first)
                token=first['body']['data']['next_after'];self.assertRegex(token,r'^dcr1_[A-Za-z0-9_-]{43}$')
                self.assertEqual(len(first['body']['data']['items']),25)
                last=self.call(i,after=token);self.assertEqual(last['status'],200,last)
                self.assertEqual(len(last['body']['data']['items']),1);self.assertIsNone(last['body']['data']['next_after'])
                self.assertEqual(self.call(i,after=token)['body'],last['body'])
                self.assertEqual(first['headers']['Cache-Control'],'no-store')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(before,(db.execute('SELECT * FROM resources').fetchall(),db.execute('SELECT * FROM artifact_indexes_v1').fetchall(),db.execute('SELECT * FROM audit').fetchall()))

    def test_three_spaces_principal_and_action_receipt_replay_are_uniform(self):
        tokens=[self.call(i)['body']['data']['next_after'] for i in range(3)]
        for i,token in enumerate(tokens):
            for workspace in ('beta','commons'):
                self.assertEqual(self.call(i,workspace,after=token)['body'],{'error':'scope_directory_changed'})
            self.assertEqual(self.call(i,after=token,token='other')['body'],{'error':'scope_directory_changed'})
            for j in range(3):
                if i!=j:self.assertEqual(self.call(j,after=token)['body'],{'error':'scope_directory_changed'})
            self.assertEqual(self.call(i,after=token)['status'],200)

    def test_auth_and_scope_precede_old_tuple_and_malformed_query(self):
        for i in range(3):
            for query in ('after=source~p024','after=','after=%XX','after=x&after=y'):
                self.assertEqual(self.call(i,query=query,token=None)['status'],401)
                self.assertEqual(self.call(i,workspace='missing',query=query)['status'],404)
                self.assertEqual(self.call(i,query=query,token='outsider')['status'],404)
                self.assertEqual(self.call(i,query=query)['body'],{'error':'scope_directory_changed'})
        self.clock.now=NOW+timedelta(hours=1)
        for i in range(3):self.assertEqual(self.call(i,query='after=%XX')['status'],401)

    def test_real_adapter_final_auth_expiry_discards_receipt_and_keeps_floor(self):
        from packages.pipeline_api import application as pipeline
        original=pipeline.directory
        def expire(*args,**kwargs):
            result=original(*args,**kwargs);self.clock.now=NOW+timedelta(hours=1);return result
        with patch.object(pipeline,'directory',side_effect=expire):
            self.assertEqual(self.call(0)['body'],{'error':'authentication_required'})
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT * FROM directory_cursor_receipts_v1').fetchall(),[])
            self.assertEqual(db.execute('SELECT count(*) FROM directory_cursor_partitions_v1').fetchone()[0],1)

    def run_process(self,namespace,after=None):
        ctx=multiprocessing.get_context('spawn');queue=ctx.Queue()
        child=ctx.Process(target=process_request,args=(str(self.path),namespace,after,queue));child.start()
        try:
            kind,result=queue.get(timeout=15);child.join(15)
            self.assertFalse(child.is_alive());self.assertEqual(child.exitcode,0);self.assertEqual(kind,'ok',result)
            return result
        finally:
            if child.is_alive():child.terminate();child.join(5)
            queue.close();queue.join_thread()

    def test_all_three_adapters_cross_process_issuance_close_and_restart_resume(self):
        # Each request reconstructs all adapters/stores in a new spawned process.
        for namespace in NAMES:
            first=self.run_process(namespace);self.assertEqual(first['status'],200,first)
            last=self.run_process(namespace,first['body']['data']['next_after'])
            self.assertEqual(last['status'],200,last);self.assertEqual(len(last['body']['data']['items']),1)

    def test_real_writer_wait_rechecks_revocation_before_cursor_lookup(self):
        token=self.call(0)['body']['data']['next_after'];ctx=multiprocessing.get_context('spawn')
        ready,release,attempting=ctx.Event(),ctx.Event(),ctx.Event();queue=ctx.Queue()
        child=ctx.Process(target=process_request,args=(str(self.path),'pipelines',token,queue,ready,release,attempting));child.start()
        try:
            self.assertTrue(ready.wait(10))
            with sqlite3.connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE');release.set();self.assertTrue(attempting.wait(10))
                db.execute("UPDATE memberships SET state='revoked',revision=revision+1 WHERE workspace_id='alpha' AND principal_id='admin'")
                db.commit()
            kind,result=queue.get(timeout=15);child.join(15)
            self.assertFalse(child.is_alive());self.assertEqual(child.exitcode,0);self.assertEqual(kind,'ok')
            self.assertEqual((result['status'],result['body']),(404,{'error':'not_found'}))
        finally:
            release.set()
            if child.is_alive():child.terminate();child.join(5)
            queue.close();queue.join_thread()
