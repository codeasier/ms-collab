"""Real SQLite session/current identity over the unmounted private receipt seam."""
from datetime import timedelta
import sqlite3
import unittest
from unittest.mock import Mock, patch

from packages.directory_cursors import initialize_directory_cursors
from packages.directory_cursors.store import PARTITIONS, RECEIPTS, instant
from packages.directory_cursors.transaction import _SQL
from packages.directory_cursors.tests.support import Clock, NOW, plan_for, issue_at, resume
from packages.identity import AuthenticationError, AuthenticationUnavailable
from packages.session_api import SessionRequest
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests import test_application as fixture
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant


class CookieReceiptFoundationTests(unittest.TestCase):
    make_session=fixture.SessionHTTPTests.make_session
    request=fixture.SessionHTTPTests.request
    write=fixture.SessionHTTPTests.write

    def setUp(self):
        fixture.SessionHTTPTests.setUp(self)
        with sqlite3.connect(self.policy_path) as db:
            for workspace in ('alpha','beta','commons'):
                for module in ('pipelines','artifacts'):
                    db.execute('INSERT INTO modules VALUES(?,?,1,1,\'enabled\',1)',(workspace,module))
        initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
        self.receipt_clock=Clock(); self.plan=plan_for(self.policy,self.receipt_clock)

    def cookie_request(self, *, extra=None):
        env={'wsgi.url_scheme':'https','HTTP_HOST':'app.example.invalid','REQUEST_METHOD':'GET',
             'PATH_INFO':'/api/workspaces/alpha/pipelines/scopes','HTTP_COOKIE':self.cookies}
        if extra:env.update(extra)
        return self.app._verify_request(env)

    def invoke(self, operation=None, *, request=None, app=None, plan=None):
        return (app or self.app)._authenticated_transaction(request or self.cookie_request(),operation or issue_at(),
            with_connection=True,receipt_only=plan or self.plan)

    def state(self):
        with sqlite3.connect(self.policy_path) as db:
            partitions=db.execute(f'SELECT * FROM {PARTITIONS}').fetchall()
            receipts=db.execute(f'SELECT * FROM {RECEIPTS}').fetchall()
        with sqlite3.connect(self.login_path) as db:clock=db.execute('SELECT clock FROM metadata WHERE id=1').fetchone()[0]
        return partitions,receipts,clock

    def test_cookie_get_without_csrf_and_base_session_delegation_preserve_writes(self):
        request=self.cookie_request();self.assertIsNone(request.csrf)
        with patch.object(self.policy,'_persist',side_effect=AssertionError('no business persist')):
            token=self.invoke(request=request)
        wrapper=WorkspaceApplication(session_boundary=self.app)
        self.assertEqual(self.invoke(resume(token),app=wrapper),('conn','pipeline'))
        self.assertEqual(self.write(csrf=None)['status'],401)
        self.assertEqual(self.write()['status'],200)
        self.assertEqual(self.request()['body']['data']['text'],'updated')

    def test_w05_wrapped_cookie_denies_before_arm_without_losing_current_auth(self):
        callback=Mock()
        wrapper=WorkspaceApplication(session_boundary=self.app)
        with self.assertRaises(PolicyError) as caught:
            self.invoke(callback,request=self.cookie_request(extra={'HTTP_X_WORKSPACE_ID':'beta'}),app=wrapper)
        self.assertEqual((caught.exception.status,caught.exception.code),(400,'invalid_request'))
        callback.assert_not_called();self.assertEqual(self.state()[:2],([],[]))

    def test_revoked_and_identity_aba_session_fail_before_arm(self):
        request=self.cookie_request();self.login.logout(self.session.token,self.session.csrf)
        with self.assertRaises(AuthenticationError):self.invoke(request=request)
        self.assertEqual(self.state()[:2],([],[]))
        self.session=self.make_session();self.cookies=fixture.cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
        with sqlite3.connect(self.policy_path) as db:
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='changed-sub' WHERE id='admin'")
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        with self.assertRaises(AuthenticationError):self.invoke()
        self.assertEqual(self.state()[:2],([],[]))

    def test_callback_policyerror_runs_final_session_check_and_commits_only_two_clocks(self):
        def deny(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            self.clock.now+=timedelta(seconds=7);self.receipt_clock.now=self.clock.now
            raise PolicyError(409,'scope_directory_changed')
        original=self.sessions._current_actor
        with patch.object(self.sessions,'_current_actor',wraps=original) as current:
            with self.assertRaises(PolicyError) as caught:self.invoke(deny)
            self.assertEqual(current.call_count,2)
        self.assertEqual(caught.exception.code,'scope_directory_changed')
        partitions,rows,login_floor=self.state();self.assertEqual(rows,[])
        self.assertEqual(partitions[0][-1],instant(self.clock.now));self.assertEqual(login_floor,instant(self.clock.now))
        self.assertEqual(self.policy.audit_events('admin','alpha'),())

    def _assert_final_expiry(self, callback_error):
        def expire(policy,actor,db,receipts):
            token=issue_at()(policy,actor,db,receipts)
            self.clock.now=self.session.expires_at
            if callback_error:raise PolicyError(409,'scope_directory_changed')
            return token
        with self.assertRaises(AuthenticationError):self.invoke(expire)
        self.assertEqual(self.state()[1],[])
        self.assertEqual(self.state()[2],instant(self.session.expires_at))

    def test_final_expiry_after_success_rolls_back_receipts(self):
        self._assert_final_expiry(False)

    def test_final_expiry_after_callback_error_overrides_cursor_error(self):
        self._assert_final_expiry(True)

    def test_clock_rollback_keeps_last_safe_floor_and_does_not_clamp_to_success(self):
        def backwards(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            self.clock.now=NOW-timedelta(microseconds=1)
            self.receipt_clock.now=NOW-timedelta(microseconds=1)
        with self.assertRaises(AuthenticationUnavailable):self.invoke(backwards)
        partitions,rows,login_floor=self.state()
        self.assertEqual(rows,[]);self.assertEqual(partitions[0][-1],instant(NOW));self.assertEqual(login_floor,instant(NOW))

    def test_attached_login_raw_mutation_and_temp_trigger_are_not_allowed(self):
        def logout(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            db.execute('DELETE FROM login.sessions')
        with self.assertRaises(PolicyError):self.invoke(logout)
        self.assertEqual(self.state()[1],[])
        self.assertEqual(self.request()['status'],200)
        original=self.sessions._connect
        def connect():
            db=original()
            db.execute("CREATE TEMP TRIGGER synthetic_clock AFTER UPDATE ON login.metadata BEGIN DELETE FROM sessions; END")
            return db
        with patch.object(self.sessions,'_connect',side_effect=connect):
            with self.assertRaises(AuthenticationUnavailable):self.invoke()
        self.assertEqual(self.request()['status'],200)

    def test_main_and_login_clock_commit_failure_rolls_back_atomically(self):
        before=self.state()
        self.clock.now+=timedelta(seconds=10);self.receipt_clock.now=self.clock.now
        with patch.object(_SQL,'commit',side_effect=sqlite3.OperationalError('synthetic commit failure')):
            with self.assertRaises(PolicyError) as caught:self.invoke()
        self.assertEqual(caught.exception.code,'scope_directory_unavailable')
        self.assertEqual(self.state(),before)

    def test_baseexception_keeps_time_and_rethrows_original(self):
        marker=SystemExit('synthetic cancellation')
        def cancel(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            self.clock.now+=timedelta(seconds=3);self.receipt_clock.now=self.clock.now
            raise marker
        with self.assertRaises(SystemExit) as caught:self.invoke(cancel)
        self.assertIs(caught.exception,marker)
        partitions,rows,login_floor=self.state();self.assertEqual(rows,[])
        self.assertEqual(partitions[0][-1],instant(self.clock.now));self.assertEqual(login_floor,instant(self.clock.now))

    def test_unarmed_scope_failure_does_not_create_floor(self):
        plan=plan_for(self.policy,self.receipt_clock,workspace='beta')
        with self.assertRaises(PolicyError) as caught:self.invoke(plan=plan)
        self.assertEqual((caught.exception.status,caught.exception.code),(404,'not_found'))
        self.assertEqual(self.state()[:2],([],[]))

    def test_actual_cookie_process_restart_receipt_then_logout_denies_reuse(self):
        import multiprocessing
        from packages.directory_cursors.tests.support import process_cookie
        ctx=multiprocessing.get_context('spawn')
        token=None
        for after in (False,True):
            queue=ctx.Queue();entered=ctx.Event();release=ctx.Event();release.set()
            child=ctx.Process(target=process_cookie,args=(str(self.policy_path),str(self.login_path),
                self.session.token.value,entered,release,queue,token if after else None));child.start()
            try:
                result=queue.get(timeout=15)
                self.assertEqual(result[0],'ok');self.assertTrue(entered.is_set())
                if after:self.assertEqual(result[1],('conn','pipeline'))
                else:token=result[1]
                child.join(15);self.assertEqual(child.exitcode,0)
            finally:
                release.set()
                if child.is_alive():child.terminate();child.join(5)
                queue.close();queue.join_thread()
        self.login.logout(self.session.token,self.session.csrf)
        with self.assertRaises(AuthenticationError):self.invoke(resume(token))
        self.assertEqual(len(self.state()[1]),1)

    def test_new_cookie_after_true_aba_rejects_old_receipt_with_same_partition(self):
        token=self.invoke()
        with sqlite3.connect(self.policy_path) as db:
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='changed-sub' WHERE id='admin'")
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        self.session=self.make_session();self.cookies=fixture.cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
        with self.assertRaises(PolicyError) as caught:self.invoke(resume(token))
        self.assertEqual(caught.exception.code,'scope_directory_changed')
        self.invoke(issue_at(('conn','new-generation')))
        self.assertEqual(len(self.state()[0]),1);self.assertEqual(len(self.state()[1]),2)

    def test_cookie_principal_projection_uses_real_pk_query_plan(self):
        from packages.session_api.store import _RECEIPT_PRINCIPAL_SQL
        with sqlite3.connect(self.policy_path) as db:
            details=[row[3].upper() for row in db.execute('EXPLAIN QUERY PLAN '+_RECEIPT_PRINCIPAL_SQL,('admin',)).fetchall()]
        self.assertTrue(any('SEARCH' in detail and 'PRINCIPALS' in detail and '(ID=?)' in detail for detail in details),details)
        self.assertFalse(any('SCAN ' in detail or 'TEMP B-TREE' in detail for detail in details),details)
        statements=[];original=self.sessions._connect
        def connect():
            db=original();db.set_trace_callback(statements.append);return db
        with patch.object(self.sessions,'_connect',side_effect=connect):self.invoke()
        reads=[sql for sql in statements if 'active,generation,authority_epoch FROM main.principals' in sql]
        self.assertTrue(reads)
        self.assertTrue(all("WHERE id='admin'" in sql and 'CAST(id AS BLOB)' not in sql for sql in reads),reads)

    def test_initial_expired_cookie_denies_before_arming_and_preserves_login_time(self):
        self.clock.now=self.session.expires_at;callback=Mock()
        with self.assertRaises(AuthenticationError):self.invoke(callback)
        callback.assert_not_called();self.assertEqual(self.state()[:2],([],[]))
        self.assertEqual(self.state()[2],instant(self.clock.now))

    def test_initial_forged_cookie_does_not_infer_actor_for_clock_projection(self):
        from packages.identity import Credential
        statements=[];original=self.sessions._connect
        def connect():
            db=original();db.set_trace_callback(statements.append);return db
        with patch.object(self.sessions,'_connect',side_effect=connect):
            with self.assertRaises(AuthenticationError):self.invoke(request=SessionRequest(Credential('A'*43)))
        self.assertFalse(any('active,generation,authority_epoch FROM main.principals' in sql for sql in statements))
        self.assertEqual(self.state()[:2],([],[]))

    def test_w05_conflict_still_runs_final_session_auth_and_expiry_wins(self):
        original=self.sessions._current_actor;calls=[];callback=Mock()
        request=self.cookie_request(extra={'HTTP_X_WORKSPACE_ID':'beta'})
        def current(*args):
            calls.append(None)
            actor=original(*args)
            if len(calls)==1:self.clock.now=self.session.expires_at
            return actor
        with patch.object(self.sessions,'_current_actor',side_effect=current):
            with self.assertRaises(AuthenticationError):self.invoke(callback,request=request)
        self.assertEqual(len(calls),2);callback.assert_not_called();self.assertEqual(self.state()[:2],([],[]))
        self.assertEqual(self.state()[2],instant(self.clock.now))

    def test_final_session_guard_baseexception_preserves_both_clocks(self):
        marker=SystemExit('synthetic final session interruption');calls=[]
        original=self.sessions._current_actor
        def current(*args):
            calls.append(None);actor=original(*args)
            if len(calls)==2:raise marker
            return actor
        def work(policy,actor,db,receipts):
            token=issue_at()(policy,actor,db,receipts)
            self.clock.now+=timedelta(seconds=5);self.receipt_clock.now=self.clock.now
            return token
        with patch.object(self.sessions,'_current_actor',side_effect=current):
            with self.assertRaises(SystemExit) as caught:self.invoke(work)
        self.assertIs(caught.exception,marker);self.assertEqual(len(calls),2)
        partitions,rows,login_floor=self.state();self.assertEqual(rows,[])
        self.assertEqual(partitions[0][-1],instant(self.clock.now));self.assertEqual(login_floor,instant(self.clock.now))

    def test_login_clock_real_cas_zero_rows_rolls_back_main_and_login(self):
        original=self.sessions._connect;before=self.state()
        self.clock.now+=timedelta(seconds=2);self.receipt_clock.now=self.clock.now
        class Connection:
            def __init__(self,db):self.db=db
            def __getattr__(self,name):return getattr(self.db,name)
            def execute(self,statement,parameters=()):
                if statement.startswith('UPDATE login.metadata SET clock='):
                    parameters=(*parameters[:-1],-1)  # real SQLite no-match CAS
                return self.db.execute(statement,parameters)
        with patch.object(self.sessions,'_connect',side_effect=lambda:Connection(original())):
            with self.assertRaises(PolicyError) as caught:self.invoke()
        self.assertEqual(caught.exception.code,'scope_directory_unavailable');self.assertEqual(self.state(),before)

    def test_second_interruption_during_clock_finalizer_keeps_original_fatal(self):
        from packages.session_api.store import _ReceiptLoginClock
        original=KeyboardInterrupt('synthetic original interruption');second=SystemExit('synthetic cleanup interruption')
        before=self.state()
        def cancel(policy,actor,db,receipts):
            issue_at()(policy,actor,db,receipts)
            self.clock.now+=timedelta(seconds=3);self.receipt_clock.now=self.clock.now
            raise original
        with patch.object(_ReceiptLoginClock,'persist',side_effect=second) as persist:
            with self.assertRaises(KeyboardInterrupt) as caught:self.invoke(cancel)
            self.assertEqual(persist.call_count,1)
        self.assertIs(caught.exception,original);self.assertEqual(self.state(),before)
