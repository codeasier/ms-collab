"""Trusted Bearer seam only: existing HTTP directories are deliberately unchanged."""
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from packages.directory_cursors import initialize_directory_cursors, ReceiptOnly
from packages.directory_cursors.store import PARTITIONS, RECEIPTS, instant
from packages.directory_cursors.transaction import _SQL
from packages.directory_cursors.tests.support import (Clock, NOW, initialize, app_for, plan_for,
    assertion, invoke, issue_at, resume, projection)
from packages.identity import AuthenticationError
from packages.workspaces import PolicyError, SQLiteWorkspaceService
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant


class BearerReceiptFoundationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'policy.sqlite';self.policy=initialize(self.path)
        initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
        self.clock=Clock();self.auth_clock=Clock()
        self.app=app_for(self.policy,self.auth_clock);self.plan=plan_for(self.policy,self.clock)

    def state(self):
        with sqlite3.connect(self.path) as db:
            return (db.execute(f'SELECT * FROM {PARTITIONS}').fetchall(),db.execute(f'SELECT * FROM {RECEIPTS}').fetchall())

    def test_two_projections_only_and_never_business_persist(self):
        view=Mock(side_effect=projection)
        plan=plan_for(self.policy,self.clock,project=view)
        with patch.object(self.policy,'_persist',side_effect=AssertionError('business persist forbidden')):
            token=invoke(self.app,plan)
        self.assertEqual(view.call_count,2)
        self.assertEqual(len(token),48)
        self.assertEqual(invoke(self.app,self.plan,resume(token)),('conn','pipeline'))

    def test_mode_rejects_write_no_connection_and_additional_commit_hook(self):
        for kw in ({'write':True,'with_connection':True},{'with_connection':False},
                   {'with_connection':True,'before_commit':lambda:None}):
            with self.subTest(kw=tuple(kw)):
                with self.assertRaises(ValueError):
                    self.app._authenticated_transaction(assertion(self.app),issue_at(),receipt_only=self.plan,**kw)
        self.assertEqual(self.state(),([],[]))

    def test_initial_invalid_identity_and_scope_error_precede_arm(self):
        operation=Mock()
        with self.assertRaises(AuthenticationError):
            self.app._authenticated_transaction(object(),operation,with_connection=True,receipt_only=self.plan)
        operation.assert_not_called()
        for workspace,actor,code in (('missing','admin','not_found'),('alpha','outsider','not_found')):
            with self.assertRaises(PolicyError) as caught:
                invoke(self.app,plan_for(self.policy,self.clock,workspace=workspace),issue_at(),actor=actor)
            self.assertEqual(caught.exception.code,code)
        self.assertEqual(self.state(),([],[]))

    def test_w05_conflict_has_no_receipt_effect_and_final_auth_is_checked(self):
        callback=Mock()
        request=assertion(self.app,extra={'HTTP_X_WORKSPACE_ID':'beta'})
        with self.assertRaises(PolicyError) as caught:
            self.app._authenticated_transaction(request,callback,with_connection=True,receipt_only=self.plan)
        self.assertEqual((caught.exception.status,caught.exception.code),(400,'invalid_request'))
        callback.assert_not_called();self.assertEqual(self.state(),([],[]))

    def test_policy_error_restores_receipts_and_partition_then_keeps_only_floor(self):
        def deny(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            self.clock.now=NOW+timedelta(seconds=5)
            raise PolicyError(409,'scope_directory_changed')
        request=assertion(self.app)
        original=self.app._verifier.validate_current
        with patch.object(self.app._verifier,'validate_current',wraps=original) as current:
            with self.assertRaises(PolicyError) as caught:
                self.app._authenticated_transaction(request,deny,with_connection=True,receipt_only=self.plan)
            self.assertEqual(current.call_count,2)
        self.assertEqual(caught.exception.code,'scope_directory_changed')
        floor,rows=self.state();self.assertEqual(rows,[])
        self.assertEqual(floor,[('admin','alpha','pipeline.scopes.read',instant(self.clock.now))])

    def test_callback_error_does_not_bypass_final_bearer_expiry(self):
        def expire(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            self.auth_clock.now=NOW+timedelta(hours=1)
            self.clock.now=NOW+timedelta(seconds=2)
            raise PolicyError(409,'scope_directory_changed')
        with self.assertRaises(AuthenticationError):invoke(self.app,self.plan,expire)
        self.assertEqual(self.state()[1],[])
        self.assertEqual(self.state()[0][0][-1],instant(self.clock.now))

    def test_baseexception_is_rethrown_after_floor_only_commit(self):
        marker=KeyboardInterrupt('synthetic cancellation')
        def interrupt(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            self.clock.now+=timedelta(seconds=4)
            raise marker
        with self.assertRaises(KeyboardInterrupt) as caught:invoke(self.app,self.plan,interrupt)
        self.assertIs(caught.exception,marker);self.assertEqual(self.state()[1],[])
        self.assertEqual(self.state()[0][0][-1],instant(self.clock.now))

    def test_callback_raw_business_dml_commit_and_temp_ddl_are_denied(self):
        statements=("UPDATE modules SET can_read=0", "DELETE FROM memberships", "DELETE FROM audit",
                    "INSERT INTO resources VALUES('alpha','pipelines','forbidden','private',1)",
                    'COMMIT','CREATE TEMP TABLE metadata (id INTEGER,realm TEXT)')
        for statement in statements:
            with self.subTest(kind=statement.split()[0]):
                def write(policy,actor,db,receipts):
                    issue_at()(policy,actor,db,receipts)
                    db.execute(statement)
                with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan,write)
                self.assertEqual((caught.exception.status,caught.exception.code),(503,'scope_directory_unavailable'))
                self.assertEqual(self.state()[1],[])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM modules WHERE can_read=1').fetchone()[0],6)
            self.assertEqual(db.execute('SELECT count(*) FROM memberships').fetchone()[0],9)
            self.assertEqual(db.execute('SELECT count(*) FROM resources').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],0)

    def test_exact_guard_after_auxiliary_mutation_detects_unexpected_row(self):
        # Simulated faulty trusted store method, not a permitted callback capability.
        retained=invoke(self.app,self.plan)
        original=_SQL.permit
        def broken(policy,actor,db,receipts):
            value=issue_at()(policy,actor,db,receipts)
            with original(receipts.sql,('main',RECEIPTS,sqlite3.SQLITE_DELETE)):
                db.execute(f'DELETE FROM {RECEIPTS}')
            return value
        with self.assertRaises(PolicyError):invoke(self.app,self.plan,broken)
        self.assertEqual([row[0] for row in self.state()[1]],[retained])
        self.assertEqual(len(self.state()[0]),1)

    def test_identity_aba_new_assertion_rejects_old_receipt_and_inherits_partition(self):
        token=invoke(self.app,self.plan)
        with sqlite3.connect(self.path) as db:
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='changed-sub' WHERE id='admin'")
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan,resume(token))
        self.assertEqual(caught.exception.code,'scope_directory_changed')
        invoke(self.app,self.plan,issue_at(('conn','new')))
        floor,rows=self.state();self.assertEqual(len(floor),1);self.assertEqual(len(rows),2)

    def test_unrelated_epoch_change_does_not_invalidate_but_module_aba_does(self):
        token=invoke(self.app,self.plan)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE workspaces SET revision=revision+1 WHERE id='beta'")
            db.execute('UPDATE metadata SET epoch=epoch+1')
        self.assertEqual(invoke(self.app,self.plan,resume(token)),('conn','pipeline'))
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE modules SET state='disabled',revision=revision+1 WHERE workspace_id='alpha' AND key='pipelines'")
            db.execute("UPDATE modules SET state='enabled',revision=revision+1 WHERE workspace_id='alpha' AND key='pipelines'")
        with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan,resume(token))
        self.assertEqual(caught.exception.code,'scope_directory_changed')

    def test_real_sqlite_commit_busy_no_success_and_no_floor_persistence_claim(self):
        self.policy._timeout=0
        reader=sqlite3.connect(self.path,isolation_level=None);self.addCleanup(reader.close)
        reader.execute('BEGIN');reader.execute('SELECT * FROM modules').fetchall()
        with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan)
        self.assertEqual(caught.exception.code,'scope_directory_unavailable')
        reader.rollback()
        self.assertEqual(self.state(),([],[]))  # failed commit did NOT persist observed time

    def test_injected_commit_error_before_and_after_real_commit_never_returns_token(self):
        original=_SQL.commit
        for after in (False,True):
            with self.subTest(after_commit=after):
                def fail(sql):
                    if after: original(sql)
                    raise sqlite3.OperationalError('synthetic commit result failure')
                with patch.object(_SQL,'commit',fail):
                    with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan)
                self.assertEqual(caught.exception.code,'scope_directory_unavailable')
                self.assertEqual(len(self.state()[1]),int(after))
        # The latter is a committed but unreturned receipt, not falsely rolled back.

    def test_preexisting_temp_alias_and_trigger_never_allow_receipt_write(self):
        original=self.policy._connect
        def connect():
            db=original();db.execute('CREATE TEMP TABLE synthetic_extra (value TEXT)');return db
        with patch.object(self.policy,'_connect',side_effect=connect):
            with self.assertRaises(PolicyError):invoke(self.app,self.plan)
        self.assertEqual(self.state(),([],[]))

    def test_real_process_waits_for_writer_then_checks_new_membership(self):
        import multiprocessing
        from packages.directory_cursors.tests.support import process_waiting_issue
        ctx=multiprocessing.get_context('spawn');queue=ctx.Queue()
        ready,release,attempting=ctx.Event(),ctx.Event(),ctx.Event()
        writer=sqlite3.connect(self.path,isolation_level=None);writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE memberships SET state='revoked',revision=revision+1 WHERE workspace_id='alpha' AND principal_id='admin'")
        child=ctx.Process(target=process_waiting_issue,args=(str(self.path),ready,release,attempting,queue));child.start()
        try:
            self.assertTrue(ready.wait(15));release.set();self.assertTrue(attempting.wait(15))
            writer.commit()
            self.assertEqual(queue.get(timeout=15),('error','not_found'))
            child.join(15);self.assertEqual(child.exitcode,0)
            self.assertEqual(self.state(),([],[]))
        finally:
            writer.rollback();writer.close();release.set()
            if child.is_alive():child.terminate();child.join(5)
            queue.close();queue.join_thread()

    def test_projection_error_still_observes_and_persists_latest_clock(self):
        calls=[]
        def view(policy,actor,db):
            calls.append(None)
            if len(calls)==2:
                self.clock.now+=timedelta(seconds=9)
                raise PolicyError(409,'scope_directory_changed')
            return projection(policy,actor,db)
        with self.assertRaises(PolicyError) as caught:
            invoke(self.app,plan_for(self.policy,self.clock,project=view))
        self.assertEqual(caught.exception.code,'scope_directory_changed')
        self.assertEqual(len(calls),2)
        self.assertEqual(self.state()[1],[])
        self.assertEqual(self.state()[0][0][-1],instant(self.clock.now))

    def test_wrong_store_handle_and_action_gate_fail_without_receipts(self):
        other=initialize(self.path.parent/'other.sqlite')
        initialize_directory_cursors(other,layout='explicit-factory-v1')
        with self.assertRaises(PolicyError):invoke(self.app,plan_for(other,self.clock))
        self.assertEqual(self.state(),([],[]))
        with self.assertRaises(PolicyError) as caught:
            invoke(self.app,plan_for(self.policy,self.clock,action='artifact.scopes.admin'),actor='viewer')
        self.assertEqual((caught.exception.status,caught.exception.code),(403,'forbidden'))
        self.assertEqual(self.state(),([],[]))

    def test_guard_after_floor_compares_actor_without_new_clock_hook(self):
        from packages.directory_cursors.transaction import _identity_projection
        calls=[]
        def tamper(db,actor):
            value=_identity_projection(db,actor);calls.append(None)
            return value if len(calls)==1 else (None,None)
        with patch('packages.directory_cursors.transaction._identity_projection',side_effect=tamper):
            with self.assertRaises(PolicyError):invoke(self.app,self.plan)
        self.assertEqual(len(calls),2);self.assertEqual(self.state(),([],[]))

    def test_receipt_expiry_during_operation_restores_cleanup_and_commits_floor(self):
        old=invoke(self.app,self.plan)
        self.clock.now=NOW+timedelta(seconds=301)
        def deny(policy,actor,db,receipts):
            issue_at(('conn','replacement'))(policy,actor,db,receipts)
            raise PolicyError(409,'scope_directory_changed')
        with self.assertRaises(PolicyError):invoke(self.app,self.plan,deny)
        self.assertEqual([r[0] for r in self.state()[1]],[old])
        self.assertEqual(self.state()[0][0][-1],instant(self.clock.now))
        def expire(policy,actor,db,receipts):
            token=issue_at(('conn','replacement'))(policy,actor,db,receipts)
            self.clock.now+=timedelta(seconds=300)
            return token
        with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan,expire)
        self.assertEqual(caught.exception.code,'scope_directory_changed')
        self.assertEqual([r[0] for r in self.state()[1]],[old])

    def test_invalid_receipt_clock_does_not_create_partition(self):
        for bad in (None,float('nan'),NOW.replace(tzinfo=None)):
            with self.subTest(kind=type(bad).__name__):
                self.clock.now=bad
                with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan)
                self.assertEqual(caught.exception.code,'scope_directory_unavailable')
                self.assertEqual(self.state(),([],[]))

    def test_floor_cas_zero_rows_is_real_sql_mismatch_and_rolls_back(self):
        original=self.policy._connect
        class Connection:
            def __init__(self,db):self.db=db
            def __getattr__(self,name):return getattr(self.db,name)
            def execute(self,statement,parameters=()):
                if statement.startswith('UPDATE main.directory_cursor_partitions_v1 SET clock_floor_us='):
                    parameters=(*parameters[:-1],-1)  # actual SQLite UPDATE matches no row
                return self.db.execute(statement,parameters)
        with patch.object(self.policy,'_connect',side_effect=lambda:Connection(original())):
            with self.assertRaises(PolicyError) as caught:invoke(self.app,self.plan)
        self.assertEqual(caught.exception.code,'scope_directory_unavailable')
        self.assertEqual(self.state(),([],[]))

    def test_connect_failure_is_value_free_503_before_controller(self):
        from packages.workspace_api.tests.test_application import call
        def dispatch(env):
            return self.app._authenticated_transaction(self.app._verify_request(env),issue_at(),
                with_connection=True,receipt_only=self.plan)
        with patch.object(self.app,'_dispatch',side_effect=dispatch):
            with patch.object(self.policy,'_connect',side_effect=sqlite3.OperationalError('synthetic private path')):
                response=call(self.app,token='admin')
            self.assertEqual((response['status'],response['body']),(503,{'error':'scope_directory_unavailable'}))
            moved=self.path.with_suffix('.temporarily-moved')
            self.path.rename(moved)
            try:
                response=call(self.app,token='admin')  # actual mode=rw SQLite open failure
                self.assertEqual((response['status'],response['body']),(503,{'error':'storage_unavailable'}))
            finally:moved.rename(self.path)
        self.assertEqual(self.state(),([],[]))

    def test_existing_pipeline_http_tuple_pages_are_unchanged_and_unmounted(self):
        from packages.pipeline_api import PipelineApplication
        from packages.pipeline_api.records import encode_snapshots, resource_id
        from packages.pipelines import PipelineScope
        from packages.directory_cursors.tests.support import ISSUER, SyntheticVerifier
        from packages.identity import Provider
        from packages.workspace_api.tests.test_application import call
        with sqlite3.connect(self.path) as db:
            for i in range(26):
                scope=PipelineScope('alpha','conn',f'p{i:02d}')
                db.execute('INSERT INTO resources VALUES(?,?,?,?,1)',('alpha','pipelines',resource_id(scope),encode_snapshots(scope,[])))
        app=PipelineApplication(store=self.policy,
            providers=(Provider('synthetic',ISSUER,'synthetic',SyntheticVerifier(),timedelta(hours=1)),),
            provider_key='synthetic',clock=self.auth_clock)
        first=call(app,'/api/workspaces/alpha/pipelines/scopes',token='admin')
        self.assertEqual(first['status'],200);self.assertEqual(first['body']['data']['next_after'],'conn~p24')
        last=call(app,'/api/workspaces/alpha/pipelines/scopes',token='admin',query='after=conn~p24')
        self.assertEqual(last['status'],200);self.assertEqual(len(last['body']['data']['items']),1)
        self.assertEqual(self.state(),([],[]))

    def test_cached_store_delete_cannot_be_reused_to_modify_another_actor(self):
        from packages.directory_cursors.store import WHERE
        retained=invoke(self.app,self.plan,actor='other')
        def cached_delete(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)  # compiles this exact DELETE in permitted phase
            db.execute(f'DELETE FROM main.{RECEIPTS} WHERE {WHERE} AND expires_us<=?',
                       ('other','alpha','pipeline.scopes.read',253402300799999999))
        with self.assertRaises(PolicyError):invoke(self.app,self.plan,cached_delete)
        self.assertEqual([row[0] for row in self.state()[1]],[retained])

    def test_prearm_raw_write_is_denied_without_receipt_floor(self):
        def write(policy,actor,db,receipts):db.execute('DELETE FROM resources')
        with self.assertRaises(PolicyError):invoke(self.app,self.plan,write)
        self.assertEqual(self.state(),([],[]))
