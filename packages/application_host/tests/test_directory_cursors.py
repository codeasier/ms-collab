"""Explicit-factory support matrix; no migration of an installed runtime."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host import ApplicationHost
from packages.application_host.tests import fixtures
from packages.application_ui.tests.test_application import request
from packages.directory_cursors import ReceiptStore, initialize_directory_cursors
from packages.directory_cursors.store import SCHEMA
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.workspaces import SQLiteWorkspaceService, StorageError, PolicyError

PATHS = ('pipelines', 'artifacts', 'artifact-admin')


class DirectoryFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.host, self.policy, self.login, self.exchange = fixtures.setup(self.temp.name, directory_cursors=False)
        self.session = fixtures.session(self.login, self.exchange)
        self.cookies = cookie_header({_SESSION:self.session.token.value, _CSRF:self.session.csrf.value})

    def app(self, store=None, admin=True):
        return ApplicationHost(config=replace(self.host._config, artifact_administration=admin),
                               sessions=self.host._boundary._sessions, clock=lambda:fixtures.NOW,
                               directory_cursors=store)

    def call(self, app, namespace, workspace='alpha', suffix='', cookies=None):
        return request(app, f'/api/workspaces/{workspace}/{namespace}/scopes'+suffix,
                       cookies=self.cookies if cookies is None else cookies)

    def objects(self):
        with sqlite3.connect(self.policy._path) as db:
            return db.execute("SELECT name,sql FROM sqlite_schema WHERE name GLOB 'directory_cursor_*' ORDER BY name").fetchall()

    def test_absent_configuration_and_schema_close_only_directories_without_ddl(self):
        app=self.app()
        for namespace in PATHS:
            self.assertEqual(self.call(app,namespace)['body'], {'error':'scope_directory_unavailable'})
            self.assertEqual(self.call(app,namespace)['status'],503)
        for path in (fixtures.READS[1],fixtures.READS[2],'/api/workspaces/alpha/artifact-admin/source/bucket/status'):
            self.assertEqual(request(app,path,cookies=self.cookies)['status'],200)
        self.assertEqual(self.objects(),[])

    def test_complete_schema_does_not_implicitly_enable_any_factory(self):
        initialize_directory_cursors(self.policy,layout='explicit-factory-v1');before=self.objects()
        app=self.app()
        for namespace in PATHS:
            self.assertEqual(self.call(app,namespace)['status'],503)
        self.assertEqual(self.objects(),before)
        with sqlite3.connect(self.policy._path) as db:
            self.assertEqual(db.execute('SELECT * FROM directory_cursor_partitions_v1').fetchall(),[])

    def test_explicit_same_database_handle_enables_three_actions_without_business_persist(self):
        initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
        store=ReceiptStore(self.policy,clock=lambda:fixtures.NOW);app=self.app(store);before=self.objects()
        with patch.object(self.policy,'_persist',side_effect=AssertionError('no business persist')):
            for namespace in PATHS:
                response=self.call(app,namespace)
                self.assertEqual(response['status'],200,response)
        self.assertEqual(self.objects(),before)
        with sqlite3.connect(self.policy._path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM directory_cursor_partitions_v1').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM artifact_index_events_v1').fetchone()[0],0)

    def test_current_auth_scope_module_and_mount_precede_closed_configuration(self):
        app=self.app()
        for namespace in PATHS:
            self.assertEqual(self.call(app,namespace,suffix='?after=%XX',cookies='')['status'],401)
            self.assertEqual(self.call(app,namespace,workspace='missing',suffix='?after=%XX')['status'],404)
            self.assertEqual(self.call(app,namespace,workspace='beta',suffix='?after=%XX')['status'],404)
        self.assertEqual(self.call(self.app(admin=False),'artifact-admin',cookies='')['status'],404)
        with sqlite3.connect(self.policy._path) as db:
            db.execute("UPDATE modules SET can_read=0,can_write=0 WHERE workspace_id='alpha'")
        for namespace in PATHS:
            self.assertEqual(self.call(app,namespace,suffix='?after=%XX')['status'],403)
        self.assertEqual(self.objects(),[])

    def test_absent_configuration_still_runs_final_current_session_guard(self):
        from packages.identity import AuthenticationError
        app=self.app();sessions=self.host._boundary._sessions;original=sessions._current_actor
        for namespace in PATHS:
            count=0
            def current(*args,**kwargs):
                nonlocal count
                count+=1
                actor=original(*args,**kwargs)
                if count==2:raise AuthenticationError()
                return actor
            with patch.object(sessions,'_current_actor',side_effect=current):
                result=self.call(app,namespace)
            self.assertEqual(count,2)
            self.assertEqual((result['status'],result['body']),(401,{'error':'authentication_required'}))
        self.assertEqual(self.objects(),[])

    def test_missing_partial_schema_constructor_and_runtime_loss_never_repair(self):
        with self.assertRaises(PolicyError):ReceiptStore(self.policy)
        self.assertEqual(self.objects(),[])
        initialize_directory_cursors(self.policy,layout='explicit-factory-v1')
        app=self.app(ReceiptStore(self.policy,clock=lambda:fixtures.NOW))
        with sqlite3.connect(self.policy._path) as db:db.execute('DROP TABLE directory_cursor_meta_v1')
        before=self.objects()
        with self.assertRaises(PolicyError):ReceiptStore(self.policy)
        for namespace in PATHS:self.assertEqual(self.call(app,namespace)['status'],503)
        self.assertEqual(self.objects(),before)
        self.assertEqual(request(self.app(),fixtures.READS[1],cookies=self.cookies)['status'],200)

    def test_other_database_handle_is_rejected_after_current_scope_without_writes(self):
        with tempfile.TemporaryDirectory() as other:
            _,policy,_,_=fixtures.setup(other)
            app=self.app(ReceiptStore(policy,clock=lambda:fixtures.NOW))
            for namespace in PATHS:
                self.assertEqual(self.call(app,namespace)['body'],{'error':'scope_directory_unavailable'})
                self.assertEqual(self.call(app,namespace,workspace='missing')['status'],404)
            self.assertEqual(self.objects(),[])
        with self.assertRaises(ValueError):self.app(object())


class DirectoryRuntimeExclusionTests(unittest.TestCase):
    def test_old_runtime_unchanged_initializer_refused_and_backup_restore_still_valid(self):
        from packages.service_runtime import initialize,check,create_application,backup,verify_backup,restore
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);config=root/'config.json';state=root/'state'
            value={'version':1,'origin':fixtures.ORIGIN,'data_directory':str(state),
                   'modules':['pipelines','artifacts'],'artifact_administration':True,
                   'login':{'mode':'disabled','issuer':fixtures.ISSUER,'audience':'synthetic-client'}}
            config.write_text(json.dumps(value));config.chmod(0o600);initialize(config)
            before={n:sha256((state/n).read_bytes()).hexdigest() for n in ('policy.sqlite','login.sqlite','installation.json')}
            policy=SQLiteWorkspaceService(state/'policy.sqlite')
            with self.assertRaises(StorageError):initialize_directory_cursors(policy,layout='explicit-factory-v1')
            self.assertEqual(before,{n:sha256((state/n).read_bytes()).hexdigest() for n in before})
            self.assertFalse(check(config)['login_enabled'])
            app=create_application(config)
            try:self.assertEqual(request(app,'/api/workspaces/alpha/pipelines/scopes')['status'],401)
            finally:app.close()
            backup(config,root/'backup');verify_backup(config,root/'backup')
            value['data_directory']=str(root/'restored');config.write_text(json.dumps(value))
            restore(config,root/'backup');self.assertFalse(check(config)['login_enabled'])

    def test_accidentally_added_runtime_receipt_schema_rejected_without_digest_change(self):
        from packages.service_runtime import initialize,check,create_application,backup,RuntimeFailure
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);config=root/'config.json';state=root/'state'
            value={'version':1,'origin':fixtures.ORIGIN,'data_directory':str(state),
                   'modules':['pipelines'],'artifact_administration':False,
                   'login':{'mode':'disabled','issuer':fixtures.ISSUER,'audience':'synthetic-client'}}
            config.write_text(json.dumps(value));config.chmod(0o600);initialize(config)
            self.assertFalse(check(config)['login_enabled'])
            # Deliberate temporary corruption, NOT calling/bypassing initializer.
            with sqlite3.connect(state/'policy.sqlite') as db:
                for sql in SCHEMA:db.execute(sql)
                realm=db.execute('SELECT realm FROM metadata').fetchone()[0]
                db.execute('INSERT INTO directory_cursor_meta_v1 VALUES(1,1,?)',(realm,))
            before={p.name:p.read_bytes() for p in state.iterdir()}
            for operation in (lambda:check(config),lambda:create_application(config),lambda:backup(config,root/'bad-backup')):
                with self.assertRaises(RuntimeFailure):operation()
            self.assertEqual(before,{p.name:p.read_bytes() for p in state.iterdir()})
