"""Synthetic authority ABA, realm and explicit schema migration regressions."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential
from packages.login_state import LoginCoordinator, SQLiteLoginStore
from packages.login_state.core import _APP, _SCHEMA_V1, _micros, _digest
from packages.session_api import SessionPolicyStore, SessionRequest
from packages.session_api.tests import test_application as fixtures
from packages.session_api.tests.test_application import BINDING, NOW, BASE
from packages.workspaces.tests.identity_fixture import simulate_identity
from packages.workspaces.tests.test_authority import snapshot
from packages.workspace_api.tests.test_application import initialize


class BoundAuthorityTests(unittest.TestCase):
    setUp = fixtures.SessionHTTPTests.setUp
    make_session = fixtures.SessionHTTPTests.make_session
    request = fixtures.SessionHTTPTests.request

    def transition(self, statement):
        with sqlite3.connect(self.policy_path) as db: simulate_identity(db, statement)

    def complete(self, coordinator, attempt, code):
        return coordinator.complete(parameters=(("state", attempt.state.value), ("code", code)), browser_binding=attempt.browser_binding, callback_uri=BINDING.callback_uri)

    def test_sessions_pin_exact_principal_generation_and_realm(self):
        snapshot = self.login.inspect_session(self.session.token)
        self.assertEqual((snapshot.principal_id, snapshot.principal_generation), ('admin', 1))
        self.assertEqual(snapshot.policy_realm, self.policy._transaction(lambda p:p._realm, write=False))
        self.assertEqual(self.request()['status'], 200)

    def test_issuer_subject_aba_does_not_restore_old_session(self):
        self.transition("UPDATE principals SET subject='replacement' WHERE id='admin'")
        self.assertEqual(self.request()['status'], 401)
        self.transition("UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        self.assertEqual(self.request()['status'], 401)
        with self.assertRaises(AuthenticationError): self.login.inspect_session(self.session.token)
        fresh = self.make_session()
        self.assertEqual(self.login.inspect_session(fresh.token).principal_generation, 3)

    def test_disable_reenable_does_not_restore_old_session(self):
        session = self.make_session('viewer-sub')
        self.transition("UPDATE principals SET active=0 WHERE id='viewer'")
        with self.assertRaises(AuthenticationError): self.login.inspect_session(session.token)
        self.transition("UPDATE principals SET active=1 WHERE id='viewer'")
        with self.assertRaises(AuthenticationError): self.login.inspect_session(session.token)
        self.assertEqual(self.login.inspect_session(self.make_session('viewer-sub').token).principal_generation, 3)

    def test_pending_attempt_cannot_cross_subject_aba_during_exchange(self):
        coordinator = LoginCoordinator(self.login, exchange=self.exchange)
        attempt = coordinator.begin(); code = self.exchange.authorize(attempt.code_challenge)
        original = self.exchange.exchange_bound_code
        def exchange(**kwargs):
            self.transition("UPDATE principals SET subject='replacement' WHERE id='admin'")
            self.transition("UPDATE principals SET subject='admin-sub' WHERE id='admin'")
            return original(**kwargs)
        with patch.object(self.exchange, 'exchange_bound_code', exchange), self.assertRaises(AuthenticationError):
            self.complete(coordinator, attempt, code)
        # Consumed attempts remain spent even if identity was restored.
        with self.assertRaises(AuthenticationError): self.complete(coordinator, attempt, code)
        self.assertEqual(self.exchange.calls, 2)
        with sqlite3.connect(self.login_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 1)

    def test_unrelated_policy_mutation_does_not_deny_pending_login(self):
        coordinator = LoginCoordinator(self.login, exchange=self.exchange)
        attempt = coordinator.begin(); code = self.exchange.authorize(attempt.code_challenge)
        self.policy.set_membership('admin','alpha','viewer',role='viewer',state='revoked',expected_revision=1)
        session = self.complete(coordinator, attempt, code)
        self.assertEqual(self.login.inspect_session(session.token).principal_generation, 1)

    def test_partial_future_or_mismatched_session_authority_fails_closed(self):
        values = [('principal_id',None), ('principal_generation',None), ('policy_realm',None),
                  ('principal_id','viewer'), ('principal_generation',2), ('principal_generation',0),
                  ('principal_generation',9007199254740992), ('policy_realm','0'*32)]
        for column, value in values:
            session = self.make_session()
            with sqlite3.connect(self.login_path) as db:
                db.execute(f'UPDATE sessions SET {column}=? WHERE token=?',(value,_digest(session.token.value)))
            with self.subTest(column=column,value=value), self.assertRaises(AuthenticationError):
                self.login.inspect_session(session.token)

    def test_partial_future_or_mismatched_attempt_authority_never_calls_exchange(self):
        for column, value in [('policy_realm',None), ('policy_realm','0'*32), ('policy_epoch',None), ('policy_epoch',0), ('policy_epoch',2), ('policy_epoch',9007199254740992)]:
            c = LoginCoordinator(self.login, exchange=self.exchange); attempt = c.begin(); code = self.exchange.authorize(attempt.code_challenge)
            with sqlite3.connect(self.login_path) as db:
                db.execute(f'UPDATE attempts SET {column}=? WHERE state=?',(value,_digest(attempt.state.value)))
            before = self.exchange.calls
            with self.subTest(column=column,value=value), self.assertRaises(AuthenticationError): self.complete(c,attempt,code)
            self.assertEqual(self.exchange.calls,before)

    def test_unbound_store_cannot_use_bound_session(self):
        store = SQLiteLoginStore(self.login_path,binding=BINDING,clock=self.clock)
        with self.assertRaises(AuthenticationError): store.inspect_session(self.session.token)
        with self.assertRaises(AuthenticationError): store.logout(self.session.token,self.session.csrf)

    def test_binding_cannot_switch_policy_and_new_realm_rejects_old_session(self):
        other = initialize(self.policy_path.with_name('other.sqlite'))
        with self.assertRaises(ValueError): SessionPolicyStore(policy=other,login=self.login)
        fresh_process = SQLiteLoginStore(self.login_path,binding=BINDING,clock=self.clock)
        SessionPolicyStore(policy=other,login=fresh_process)
        with self.assertRaises(AuthenticationError): fresh_process.inspect_session(self.session.token)
        # A pending attempt also cannot move into another policy realm.
        attempt = self.login.begin()
        with self.assertRaises(AuthenticationError): fresh_process._consume(attempt.state,attempt.browser_binding,BINDING.callback_uri)

    def test_schema_guard_removal_rejected_before_issuance(self):
        with sqlite3.connect(self.policy_path) as db: db.execute('DROP TRIGGER principal_authority_closed')
        with self.assertRaises(AuthenticationUnavailable): self.login.begin()


class LoginMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'login.sqlite'
        self.policy = initialize(self.path.with_name('policy.sqlite'))

    def legacy(self,path=None):
        path = path or self.path
        now = _micros(NOW)
        with closing(sqlite3.connect(path,isolation_level=None)) as db:
            for sql in _SCHEMA_V1: db.execute(sql)
            db.execute(f'PRAGMA application_id={_APP}');db.execute('PRAGMA user_version=1')
            db.execute('INSERT INTO metadata VALUES(1,?,?)',(BINDING.fingerprint(),now))
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?)',(_digest('s'*43),_digest('b'*43),'v'*43,now,now+300_000_000))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(_digest('t'*43),_digest('c'*43),'admin-sub',now,now+1800_000_000))

    def migrate(self,path=None): return SQLiteLoginStore.migrate_v1(path or self.path,binding=BINDING,clock=lambda:NOW)

    def test_explicit_migration_retains_rows_but_never_adopts_legacy_authority(self):
        self.legacy(); before=snapshot(self.path)
        with self.assertRaises(AuthenticationUnavailable): SQLiteLoginStore(self.path,binding=BINDING,clock=lambda:NOW)
        self.assertEqual(snapshot(self.path),before)
        self.assertEqual(self.migrate(),dict(from_version=1,to_version=2,retained_attempts=1,retained_sessions=1,legacy_authority_upgraded=False))
        store=SQLiteLoginStore(self.path,binding=BINDING,clock=lambda:NOW)
        # Legacy standalone mode remains a protocol primitive, not workspace authority.
        self.assertIsNone(store.inspect_session(Credential('t'*43)).principal_id)
        SessionPolicyStore(policy=self.policy,login=store)
        with self.assertRaises(AuthenticationError):store.inspect_session(Credential('t'*43))
        with self.assertRaises(AuthenticationError):store._consume(Credential('s'*43),Credential('b'*43),BINDING.callback_uri)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT principal_id,principal_generation,policy_realm FROM sessions').fetchall(),[(None,None,None)])
        before=snapshot(self.path)
        with self.assertRaises(AuthenticationUnavailable): self.migrate()
        self.assertEqual(snapshot(self.path),before)

    def test_legacy_corruption_or_unknown_schema_never_blessed(self):
        variants=["UPDATE sessions SET csrf='invalid'", "UPDATE sessions SET subject=''", "UPDATE sessions SET expires=created",
                  "UPDATE attempts SET verifier='invalid'", "UPDATE attempts SET created=created+1,expires=expires+1",
                  "UPDATE metadata SET binding='other'", "UPDATE metadata SET clock=clock+1",
                  "CREATE TRIGGER untrusted AFTER DELETE ON sessions BEGIN SELECT 1; END", "CREATE INDEX untrusted ON sessions(subject)"]
        for i,sql in enumerate(variants):
            path=self.path.with_name(f'bad-{i}.sqlite');self.legacy(path)
            with closing(sqlite3.connect(path,isolation_level=None)) as db:db.execute(sql)
            before=snapshot(path)
            with self.subTest(sql=sql),self.assertRaises(AuthenticationUnavailable):self.migrate(path)
            self.assertEqual(snapshot(path),before)

    def test_each_login_ddl_failure_rolls_back(self):
        original=sqlite3.connect
        class Injected(sqlite3.Connection):
            needle=None
            def execute(self,sql,*args):
                if sql.startswith(self.needle):raise sqlite3.OperationalError('synthetic fault')
                return super().execute(sql,*args)
        points=['ALTER TABLE attempts ADD COLUMN policy_realm','ALTER TABLE attempts ADD COLUMN policy_epoch',
                'ALTER TABLE sessions ADD COLUMN principal_id','ALTER TABLE sessions ADD COLUMN principal_generation',
                'ALTER TABLE sessions ADD COLUMN policy_realm','PRAGMA user_version=2']
        for i,point in enumerate(points):
            path=self.path.with_name(f'fault-{i}.sqlite');self.legacy(path);before=snapshot(path);Injected.needle=point
            def connect(*args,**kwargs):return original(*args,**kwargs,factory=Injected)
            with self.subTest(point=point),patch('packages.login_state.core.sqlite3.connect',connect),self.assertRaises(AuthenticationUnavailable):self.migrate(path)
            self.assertEqual(snapshot(path),before)

    def test_missing_path_not_created_by_migration(self):
        with self.assertRaises(AuthenticationUnavailable):self.migrate()
        self.assertFalse(self.path.exists())


if __name__=='__main__':unittest.main()
