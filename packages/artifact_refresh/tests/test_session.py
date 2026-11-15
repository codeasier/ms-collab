"""Real cookie/session transactions; no HTTP-asserted actor or provider URL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import io,json,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from packages.application_host import ApplicationHost,HostConfig
from packages.application_host.tests import fixtures as host
from packages.artifact_refresh import ArtifactRefreshConfiguration,ConfiguredArtifactRefresh,RefreshCleanupError,initialize_refresh
from packages.artifact_refresh import service
from packages.artifact_sync import ArtifactSynchronizer,ProviderError
from packages.artifact_sync.tests.fixtures import credential
from packages.session_api.application import _SESSION,_CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.workspaces.tests.identity_fixture import simulate_identity
from fixtures import setup

BASE='/api/workspaces/alpha/artifact-admin/source/bucket/'

class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=host.NOW;self.tick=10.
        self.app,self.policy,self.login,self.exchange,self.pages=setup(self.tmp.name,clock=lambda:self.now)
        self.controller=self.app._artifact_admin._refresher;self.controller._monotonic=lambda:self.tick
        self.s=host.session(self.login,self.exchange);self.path=Path(self.tmp.name)/'policy.sqlite'
    def call(self,action='refresh',*,session=None,body=None,extra=None,app=None,revision=1,scope='alpha'):
        session=session or self.s;raw=json.dumps(body if body is not None else {}).encode()
        env={'PATH_INFO':BASE.replace('/alpha/',f'/{scope}/')+action,'REQUEST_METHOD':'GET' if action in ('status','review') else 'POST',
            'QUERY_STRING':'','SCRIPT_NAME':'','HTTP_HOST':'app.example.invalid','wsgi.url_scheme':'https','HTTP_ORIGIN':host.ORIGIN,
            'HTTP_COOKIE':cookie_header({_SESSION:session.token.value,_CSRF:session.csrf.value}),'HTTP_X_CSRF_TOKEN':session.csrf.value,
            'HTTP_IF_MATCH':f'"{revision}"','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        env.update(extra or {});result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result['body']=json.loads(b''.join((app or self.app)(env,start)));return result
    def sql(self,query,args=()):
        with sqlite3.connect(self.path) as db:return db.execute(query,args).fetchall()
    def identity(self, statement):
        with sqlite3.connect(self.path) as db:simulate_identity(db, statement)
    def state(self):return self.sql('SELECT revision,candidate,published,published_revision FROM artifact_indexes_v1 WHERE workspace=?',('alpha',))[0]
    def leases(self):return self.sql('SELECT actor,state,result_revision FROM artifact_refresh_leases_v1 ORDER BY rowid')
    def revoke(self):self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
    def catalog(self,session=None):return self.call('quarters',session=session,extra={'PATH_INFO':'/api/workspaces/alpha/artifacts/source/bucket/quarters','REQUEST_METHOD':'GET'})
    def test_private_refresh_then_complete_explicit_approval(self):
        before=self.state();self.assertTrue(self.call('status')['body']['data']['capabilities']['refresh'])
        r=self.call();self.assertEqual(r['status'],200,r);self.assertEqual(r['body']['data']['publication'],'pending')
        self.assertEqual(self.state()[2:],before[2:]);self.assertEqual(self.leases(),[('admin','complete',2)])
        reader=host.session(self.login,self.exchange,'reader-sub');self.assertEqual(self.catalog(reader)['body']['data']['quarters'],[])
        self.assertEqual(self.call('review',revision=2,session=reader)['status'],403)
        review=self.call('review',revision=2)['body']['data'];self.assertEqual(review['count'],1)
        body={k:review[k] for k in ('candidate_digest','readers')};body['confirmation']='approve_complete_candidate_to_reviewed_readers'
        self.assertEqual(self.call('approve',revision=2,body=body)['status'],200)
        self.assertIsNone(self.state()[1]);self.assertNotEqual(self.state()[2],before[2]);self.assertTrue(self.catalog(reader)['body']['data']['quarters'])
        self.assertNotIn('WWW-Authenticate',r['headers']);self.assertEqual(r['headers']['Vary'],'Cookie, Origin')
    def test_unauthorized_transport_fields_never_load_or_lease(self):
        variants=({'HTTP_COOKIE':''},{'HTTP_X_CSRF_TOKEN':''},{'HTTP_ORIGIN':'https://other.invalid'},{'HTTP_HOST':'other.invalid'},
            {'wsgi.url_scheme':'http'},{'SCRIPT_NAME':'/p'},{'HTTP_AUTHORIZATION':'Bearer synthetic'}, {'HTTP_SEC_FETCH_SITE':'cross-site'})
        for extra in variants:self.assertEqual(self.call(extra=extra)['status'],401)
        for body in ({'url':'https://other.invalid'},{'actor':'admin'},{'credential':'synthetic'},{'provider':'obs'}):self.assertEqual(self.call(body=body)['status'],400)
        for who in ('reader','second','outsider'):
            s=host.session(self.login,self.exchange,who+'-sub');self.assertIn(self.call(session=s)['status'],(403,404))
        for scope in ('beta','commons','missing'):self.assertEqual(self.call(scope=scope)['status'],404)
        self.assertEqual(self.call(revision=99)['status'],412);self.assertEqual(self.pages.loads,[]);self.assertEqual(self.leases(),[])
    def test_disabled_missing_config_and_schema_fail_before_loader(self):
        disabled=ApplicationHost(config=replace(self.app._config,artifact_refresh=False),sessions=self.app._boundary._sessions,clock=lambda:self.now)
        self.assertFalse(self.call('status',app=disabled)['body']['data']['capabilities']['refresh']);self.assertEqual(self.call(app=disabled)['status'],503)
        with self.assertRaises(ValueError):ApplicationHost(config=self.app._config,sessions=self.app._boundary._sessions)
        with self.assertRaises(ValueError):ApplicationHost(config=replace(self.app._config,artifact_refresh=False),sessions=self.app._boundary._sessions,artifact_refresh=ArtifactRefreshConfiguration(self.pages.load))
        for value in (1,'true',None):
            with self.assertRaises(ValueError):replace(self.app._config,artifact_refresh=value)
        with self.assertRaises(ValueError):HostConfig(host.ORIGIN,('artifacts',),artifact_refresh=True)
        self.sql('DROP TABLE artifact_refresh_leases_v1');self.assertEqual(self.call()['status'],503);self.assertEqual(self.pages.loads,[])
    def test_schema_downgrade_rejected_not_migrated(self):
        self.sql('DROP TABLE artifact_refresh_leases_v1');self.sql('CREATE TABLE artifact_refresh_leases_v1(id TEXT)')
        with self.assertRaises(Exception):initialize_refresh(self.policy)
        self.assertEqual(self.call()['status'],503);self.assertEqual(self.pages.loads,[])
    def test_loader_logout_rechecks_before_get_and_cleans_owned_lease(self):
        self.pages.load_hook=lambda:self.login.logout(self.s.token,self.s.csrf)
        self.assertEqual(self.call()['status'],401);self.assertEqual(self.pages.calls,[]);self.assertEqual(self.leases(),[('admin','failed',None)])
    def test_loader_scope_binding_denied_without_get(self):
        self.app._artifact_admin._service._credential_loader=lambda *args:credential(origin='https://wrong.example.invalid')
        self.assertEqual(self.call()['status'],502);self.assertEqual(self.pages.calls,[]);self.assertEqual(self.leases()[0][1],'failed')
    def test_page_revoke_and_no_candidate_or_audit(self):
        before=self.state();self.pages.page_hook=self.revoke
        self.assertEqual(self.call()['status'],404);self.assertEqual(self.state(),before);self.assertEqual(self.leases()[0][1],'failed')
        self.assertEqual(self.sql('SELECT count(*) FROM artifact_index_events_v1')[0][0],0)
    def test_page_logout_expiry_rebind_module_archive_grant_policy_and_revision(self):
        mutations=(lambda t:t.login.logout(t.s.token,t.s.csrf),lambda t:setattr(t,'now',t.s.expires_at),
            lambda t:t.identity("UPDATE principals SET subject='changed' WHERE id='admin'"),
            lambda t:t.sql("UPDATE modules SET state='disabled' WHERE workspace_id='alpha' AND key='artifacts'"),
            lambda t:t.policy.set_workspace_state('admin','alpha',state='archived',expected_revision=1),
            lambda t:t.sql("UPDATE artifact_indexes_v1 SET policy=replace(policy,'\"refreshers\":[\"admin\"]','\"refreshers\":[\"second\"]') WHERE workspace='alpha'"),
            lambda t:t.sql("UPDATE artifact_indexes_v1 SET policy=replace(policy,'\"readers\":[\"admin\",\"reader\"]','\"readers\":[\"admin\"]') WHERE workspace='alpha'"),
            lambda t:t.sql("UPDATE artifact_indexes_v1 SET revision=revision+1 WHERE workspace='alpha'"))
        for change in mutations:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                app,p,l,e,pages=setup(d,clock=lambda:self.now);s=host.session(l,e)
                old=(self.app,self.policy,self.login,self.s,self.path,self.pages,self.now)
                self.app,self.policy,self.login,self.s,self.path,self.pages=app,p,l,s,Path(d)/'policy.sqlite',pages
                try:
                    before=self.state();pages.page_hook=lambda:change(self);r=self.call()
                    self.assertIn(r['status'],(401,403,404,409,412),r);self.assertEqual(self.state()[1:3],before[1:3]);self.assertEqual(self.leases()[0][1],'failed')
                finally:self.app,self.policy,self.login,self.s,self.path,self.pages,self.now=old
    def test_page_identity_aba_rejects_original_session_and_cleans_lease(self):
        before=self.state()
        subject=self.sql("SELECT subject FROM principals WHERE id='admin'")[0][0]
        def change():
            with sqlite3.connect(self.path) as db:
                simulate_identity(db, "UPDATE principals SET subject='synthetic-replacement' WHERE id='admin'")
                simulate_identity(db, "UPDATE principals SET subject=? WHERE id='admin'", (subject,))
        self.pages.page_hook=change
        self.assertEqual(self.call()['status'],401)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.leases(),[('admin','failed',None)])
        self.assertEqual(self.sql('SELECT count(*) FROM artifact_index_events_v1')[0][0],0)
        self.assertEqual(len(self.pages.loads),1);self.assertEqual(len(self.pages.calls),1)
        self.assertEqual(self.sql("SELECT generation FROM principals WHERE id='admin'")[0][0],3)

    def test_final_commit_deadline_and_session_expiry_roll_back_candidate_audit_lease(self):
        original=self.policy._persist
        def persist(db,policy,**kw):
            original(db,policy,**kw)
            if db.execute("SELECT 1 FROM artifact_refresh_leases_v1 WHERE state='complete'").fetchone():self.tick+=30
        with patch.object(self.policy,'_persist',side_effect=persist):self.assertEqual(self.call()['status'],504)
        self.assertIsNone(self.state()[1]);self.assertEqual(self.leases(),[('admin','failed',None)]);self.assertEqual(self.sql('SELECT count(*) FROM artifact_index_events_v1')[0][0],0)
    def test_admission_persist_expiry_rolls_back_before_secret(self):
        original=self.policy._persist
        def persist(db,policy,**kw):original(db,policy,**kw);setattr(self,'now',self.s.expires_at)
        with patch.object(self.policy,'_persist',side_effect=persist):self.assertEqual(self.call()['status'],401)
        self.assertEqual(self.leases(),[]);self.assertEqual(self.pages.loads,[])
    def test_final_audit_failure_rolls_back_and_redacts(self):
        self.sql("CREATE TRIGGER fail_audit BEFORE INSERT ON artifact_index_events_v1 BEGIN SELECT RAISE(ABORT,'synthetic-private-marker'); END")
        r=self.call();self.assertEqual(r['status'],503);self.assertNotIn('synthetic-private-marker',str(r));self.assertIsNone(self.state()[1]);self.assertEqual(self.leases()[0][1],'failed')
    def test_timeout_after_provider_does_not_publish(self):
        self.pages.page_hook=lambda:setattr(self,'tick',self.tick+30)
        self.assertEqual(self.call()['status'],504);self.assertIsNone(self.state()[1]);self.assertEqual(self.leases()[0][1],'failed')
    def test_cleanup_unknown_retains_reservation(self):
        self.pages.page_hook=lambda:(_ for _ in ()).throw(RefreshCleanupError())
        self.assertEqual(self.call()['status'],502);self.assertEqual(self.leases(),[('admin','active',None)])
        self.tick+=99;self.now+=timedelta(seconds=99)
        self.assertEqual(self.call()['status'],429);self.assertEqual(len(self.pages.calls),1)
    def test_concurrent_same_actor_and_scope_and_late_recovery_fence(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        self.pages.page_hook=lambda:(entered.set(),release.wait(5))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.call);self.assertTrue(entered.wait(2));self.assertEqual(self.call()['status'],429)
            # Synthetic trusted recovery marker to prove late worker fencing;
            # real recovery additionally requires proof worker/resources terminated.
            self.sql("UPDATE artifact_refresh_leases_v1 SET state='failed' WHERE state='active'")
            release.set();self.assertIn(first.result()['status'],(412,503));self.assertIsNone(self.state()[1])
    def test_archive_before_admission_zero_provider(self):
        self.policy.set_workspace_state('admin','alpha',state='archived',expected_revision=1)
        self.assertEqual(self.call()['status'],409);self.assertEqual(self.pages.loads,[]);self.assertEqual(self.leases(),[])

    def test_independent_authorities_share_global_actor_scope_capacity(self):
        from packages.artifact_sync.records import dump
        with sqlite3.connect(self.path) as db:
            for actor in ('admin','second','outsider'):
                for scope in ('alpha','beta','commons'):
                    db.execute("INSERT OR REPLACE INTO memberships VALUES (?,?, 'admin','active',1)",(scope,actor))
            for scope,raw in db.execute('SELECT workspace,policy FROM artifact_indexes_v1').fetchall():
                value=json.loads(raw);value['refreshers']=['admin','outsider','second']
                db.execute('UPDATE artifact_indexes_v1 SET policy=? WHERE workspace=?',(dump(value),scope))
        entered=threading.Barrier(3);release=threading.Event();self.addCleanup(release.set)
        self.pages.page_hook=lambda:(entered.wait(timeout=3),release.wait(5))
        config=ArtifactRefreshConfiguration(self.pages.load,self.pages)
        other=ApplicationHost(config=self.app._config,sessions=self.app._boundary._sessions,clock=lambda:self.now,artifact_refresh=config)
        second=host.session(self.login,self.exchange,'second-sub');third=host.session(self.login,self.exchange,'outsider-sub')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(self.call);b=pool.submit(self.call,session=second,scope='beta',app=other)
            entered.wait(timeout=3)
            self.assertEqual(self.call(session=third,scope='commons')['status'],429)
            self.assertEqual(self.call(session=second,scope='alpha')['status'],429)
            self.assertEqual(self.call(scope='commons')['status'],429)
            self.assertEqual(len(self.pages.calls),2)
            # Expired but blocked acquisitions still occupy resources/capacity.
            self.now+=timedelta(seconds=31)
            self.assertEqual(self.call(session=third,scope='commons')['status'],429)
            release.set();self.assertEqual(a.result()['status'],504);self.assertEqual(b.result()['status'],504)
        self.assertTrue(all(r[1]=='failed' for r in self.leases()))
    def test_approval_during_refresh_wins_and_new_candidate_never_inherits_it(self):
        self.assertEqual(self.call()['status'],200);review=self.call('review',revision=2)['body']['data']
        body={k:review[k] for k in ('candidate_digest','readers')};body['confirmation']='approve_complete_candidate_to_reviewed_readers'
        self.now+=timedelta(seconds=1);self.pages.rows=(('builds/next.txt','2026-01-01T23:00:00Z',3),)
        results=[];self.pages.page_hook=lambda:results.append(self.call('approve',revision=2,body=body)['status'])
        self.assertEqual(self.call(revision=2)['status'],412);self.assertEqual(results,[200])
        record=self.state();self.assertIsNone(record[1]);self.assertIn('builds/new.txt',record[2]);self.assertNotIn('builds/next.txt',record[2]);self.assertEqual(record[3],3)
    def test_expired_cleanup_unknown_does_not_free_capacity(self):
        def fail():self.tick+=31;raise RefreshCleanupError()
        self.pages.page_hook=fail
        self.assertEqual(self.call()['status'],504);self.assertEqual(self.leases(),[('admin','active',None)])
        self.assertEqual(self.call()['status'],429)
    def test_candidate_persist_session_expiry_rolls_back_all_business(self):
        original=self.policy._persist
        def persist(db,policy,**kw):
            original(db,policy,**kw)
            if db.execute("SELECT 1 FROM artifact_refresh_leases_v1 WHERE state='complete'").fetchone():self.now=self.s.expires_at
        with patch.object(self.policy,'_persist',side_effect=persist):self.assertEqual(self.call()['status'],401)
        self.assertIsNone(self.state()[1]);self.assertEqual(self.leases(),[('admin','failed',None)]);self.assertEqual(self.sql('SELECT count(*) FROM artifact_index_events_v1')[0][0],0)
    def test_xml_failure_is_not_persisted_and_redacted(self):
        self.pages.list_page=lambda *a,**k:b'<!DOCTYPE secret><secret>do-not-leak</secret>'
        r=self.call();self.assertEqual(r['status'],502);self.assertNotIn('do-not-leak',str(r));self.assertIsNone(self.state()[1]);self.assertEqual(self.leases()[0][1],'failed')

if __name__=='__main__':unittest.main()
