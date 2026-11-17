"""New auxiliary schema/receipt contracts, discovered by the existing policy CI."""
from datetime import timedelta
import hashlib
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.directory_cursors import ReceiptStore, initialize_directory_cursors
from packages.directory_cursors.store import META, PARTITIONS, RECEIPTS, TTL, SCHEMA, instant
from packages.directory_cursors.tests.support import (Clock, NOW, initialize, app_for, plan_for,
    invoke, issue_at, resume, process_issue, process_resume)
from packages.workspaces import PolicyError, StorageError, SQLiteWorkspaceService


class DirectoryReceiptStoreTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'policy.sqlite'; self.policy=initialize(self.path)
        initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
        self.clock=Clock(); self.app=app_for(self.policy,self.clock); self.plan=plan_for(self.policy,self.clock)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return (db.execute(f'SELECT * FROM {PARTITIONS} ORDER BY actor_id,workspace_id,action').fetchall(),
                    db.execute(f'SELECT * FROM {RECEIPTS} ORDER BY token').fetchall())

    def error(self, operation, code='scope_directory_unavailable', status=503, plan=None):
        with self.assertRaises(PolicyError) as caught:
            invoke(self.app,plan or self.plan,operation)
        self.assertEqual((caught.exception.status,caught.exception.code),(status,code))

    def test_initializer_is_explicit_idempotent_not_constructor_side_effect(self):
        before=self.path.read_bytes()
        initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
        self.assertEqual(self.path.read_bytes(),before)
        other=Path(self.path.parent)/'other.sqlite'; policy=initialize(other)
        before=other.read_bytes()
        with self.assertRaises(PolicyError): ReceiptStore(policy)
        self.assertEqual(other.read_bytes(),before)
        with self.assertRaises(ValueError): initialize_directory_cursors(policy,layout='runtime')
        self.assertEqual(other.read_bytes(),before)

    def test_runtime_markers_rejected_without_ddl_or_changes(self):
        for name in ('installation.json','.runtime.lock'):
            with self.subTest(name=name):
                marker=self.path.parent/name; marker.write_text('synthetic not parsed')
                before=self.path.read_bytes()
                with self.assertRaises(StorageError) as caught:
                    initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
                self.assertEqual(caught.exception.code,'unsupported_layout')
                self.assertEqual(self.path.read_bytes(),before); marker.unlink()
        marker=self.path.parent/'installation.json'; marker.symlink_to('absent-synthetic-target')
        with self.assertRaises(StorageError): initialize_directory_cursors(self.policy,layout='explicit-factory-v1')

    def test_partial_schema_never_repaired(self):
        with sqlite3.connect(self.path) as db: db.execute(f'DROP TABLE {RECEIPTS}')
        before=self.path.read_bytes()
        with self.assertRaises(PolicyError): initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
        self.assertEqual(self.path.read_bytes(),before)

    def test_wrong_realm_extra_index_and_trigger_fail_closed(self):
        for sql in (f"UPDATE {META} SET realm='{'0'*32}'", f'CREATE INDEX synthetic_extra ON {PARTITIONS}(clock_floor_us)',
                    f'CREATE TRIGGER synthetic_extra AFTER INSERT ON {RECEIPTS} BEGIN DELETE FROM {RECEIPTS}; END'):
            with self.subTest(sql_kind=sql.split()[0]):
                backup=self.path.read_bytes()
                with sqlite3.connect(self.path) as db: db.execute(sql)
                self.error(issue_at()); self.assertEqual(self.snapshot(),([],[]))
                self.path.write_bytes(backup)  # disposable fixture only, no live connections

    def test_reuse_fixed_ttl_context_mismatch_and_bounded_partition(self):
        token=invoke(self.app,self.plan)
        self.clock.now += timedelta(seconds=1)
        self.assertEqual(invoke(self.app,self.plan),token)
        rows=self.snapshot()[1]; self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][-2:],(instant(NOW),instant(NOW)+TTL))
        self.assertEqual(invoke(self.app,self.plan,resume(token)),('conn','pipeline'))
        for plan in (plan_for(self.policy,self.clock,workspace='beta'),
                     plan_for(self.policy,self.clock,action='artifact.scopes.read'),
                     plan_for(self.policy,self.clock,action='artifact.scopes.admin')):
            self.error(resume(token),'scope_directory_changed',409,plan)
        with self.assertRaises(PolicyError) as caught: invoke(self.app,self.plan,resume(token),actor='other')
        self.assertEqual(caught.exception.code,'scope_directory_changed')

    def test_expired_error_commits_floor_then_clock_rewind_cannot_revive(self):
        token=invoke(self.app,self.plan)
        self.clock.now=NOW+timedelta(seconds=300)
        self.error(resume(token),'scope_directory_changed',409)
        self.assertEqual(self.snapshot()[0][0][-1],instant(self.clock.now))
        self.clock.now=NOW+timedelta(seconds=299)
        self.error(resume(token))
        self.assertEqual(self.snapshot()[0][0][-1],instant(NOW+timedelta(seconds=300)))
        self.assertEqual(len(self.snapshot()[1]),1)  # error cleanup restored R0

    def test_quota_no_eviction_other_partitions_and_expired_cleanup(self):
        tokens=[invoke(self.app,self.plan,issue_at(('conn',f'p{i:02d}'))) for i in range(32)]
        before=self.snapshot()[1]
        self.error(issue_at(('conn','overflow')))
        self.assertEqual(self.snapshot()[1],before)
        self.assertEqual(invoke(self.app,self.plan,issue_at(('conn','p00'))),tokens[0])
        other=plan_for(self.policy,self.clock,workspace='beta')
        self.assertIsInstance(invoke(self.app,other),str)
        invoke(self.app,self.plan,actor='other')
        self.clock.now += timedelta(seconds=300)
        invoke(self.app,self.plan,issue_at(('conn','fresh')))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(f'SELECT count(*) FROM {RECEIPTS} WHERE actor_id=? AND workspace_id=?',('admin','alpha')).fetchone()[0],1)
            self.assertEqual(db.execute(f'SELECT count(*) FROM {RECEIPTS} WHERE workspace_id=?',('beta',)).fetchone()[0],1)

    def test_actual_separate_process_restart_and_quota_31_to_32(self):
        token=invoke(self.app,self.plan)
        ctx=multiprocessing.get_context('spawn'); queue=ctx.Queue()
        child=ctx.Process(target=process_resume,args=(str(self.path),token,queue));child.start()
        try:
            self.assertEqual(queue.get(timeout=15),('ok',('conn','pipeline')))
            child.join(15);self.assertEqual(child.exitcode,0)
        finally:
            if child.is_alive():child.terminate();child.join(5)
            queue.close();queue.join_thread()
        for i in range(30):invoke(self.app,self.plan,issue_at(('conn',f'p{i:02d}')))
        queue=ctx.Queue();release=ctx.Event();ready=[ctx.Event(),ctx.Event()]
        children=[ctx.Process(target=process_issue,args=(str(self.path),('conn',f'race{i}'),ready[i],release,queue)) for i in range(2)]
        for child in children:child.start()
        try:
            self.assertTrue(all(event.wait(15) for event in ready));release.set()
            outcomes=[queue.get(timeout=15) for _ in children]
            self.assertEqual(sorted(x[0] for x in outcomes),['error','ok'])
            self.assertEqual([x[1] for x in outcomes if x[0]=='error'],['scope_directory_unavailable'])
            for child in children:child.join(15);self.assertEqual(child.exitcode,0)
            self.assertEqual(len(self.snapshot()[1]),32)
        finally:
            release.set()
            for child in children:
                if child.is_alive():child.terminate();child.join(5)
            queue.close();queue.join_thread()

    def test_nul_noncanonical_token_invalid_utf8_and_oversize_are_value_free(self):
        token=invoke(self.app,self.plan)
        for bad in ('conn~pipeline','',token+'\0private',token[:-1]+'!', 'dcr1_'+'_'*43):
            self.error(resume(bad),'scope_directory_changed',409)
        for field,value in (('token',token+'\0private'),('connection_id', 'conn\0private'),('object_id','x'*129),
                            ('authority',b'x'*33),('connection_id',b'\xff'),('connection_id','a'*127+'é')):
            with self.subTest(field=field,type=type(value).__name__):
                backup=self.path.read_bytes()
                with sqlite3.connect(self.path) as db:
                    db.execute('PRAGMA ignore_check_constraints=ON')
                    if type(value) is bytes and field=='connection_id':
                        db.execute(f'UPDATE {RECEIPTS} SET connection_id=CAST(? AS TEXT)',(value,))
                    else:db.execute(f'UPDATE {RECEIPTS} SET {field}=?',(value,))
                self.error(resume(token))
                self.path.write_bytes(backup)

    def test_bound_128_anchor_and_schema_object_sentinel_queries(self):
        token=invoke(self.app,self.plan,issue_at(('a'*128,'b'*128)))
        self.assertEqual(invoke(self.app,self.plan,resume(token)),('a'*128,'b'*128))
        self.error(issue_at(('a'*129,'b')))
        statements=[];original=self.policy._connect
        def connect():
            db=original();db.set_trace_callback(statements.append);return db
        with patch.object(self.policy,'_connect',side_effect=connect):invoke(self.app,self.plan)
        text='\n'.join(statements)
        self.assertIn("GLOB 'directory_cursor_*' LIMIT 5",text)
        self.assertIn('substr(CAST(token AS BLOB),1,49)',text)
        self.assertIn('ORDER BY token LIMIT 33',text)
        self.assertNotIn('COUNT(*)',text.upper())

    def test_global_random_collision_retries_only_three_times_without_overwrite(self):
        token=invoke(self.app,self.plan)
        before=self.snapshot()[1]
        with patch('packages.directory_cursors.transaction.secrets.token_urlsafe',return_value=token[5:]) as random:
            self.error(issue_at(('conn','different-position')))
            self.assertEqual(random.call_count,3)
        self.assertEqual(self.snapshot()[1],before)
