"""Real host/session/policy/SQLite directories. Only fixtures synthesize state."""
from dataclasses import replace
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host.tests import fixtures
from packages.application_ui.tests.artifact_fixtures import application, stage, SCOPE
from packages.application_ui.tests.artifact_discovery_fixtures import seed_directory
from packages.application_ui.tests.test_application import request
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.artifact_sync import Connection, IndexPolicy, initialize_indexes
from packages.artifact_sync import records
from packages.artifact_sync.discovery import MAX_SCOPES
from packages.artifact_sync.tests.authority_fixtures import seed_publication, configure_index_fixture
from packages.artifacts import Scope, Snapshot


class ArtifactDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=fixtures.NOW
        self.app,self.policy,self.login,self.exchange=application(self.tmp.name,clock=lambda:self.now)
        self.path=Path(self.tmp.name)/'policy.sqlite';seed_directory(self.policy);self.sign_in()
    def sign_in(self,subject='admin-sub'):
        self.session=fixtures.session(self.login,self.exchange,subject)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
    def call(self,workspace='alpha',*,managed=False,suffix='',**kw):
        return request(self.app,f'/api/workspaces/{workspace}/{"artifact-admin" if managed else "artifacts"}/scopes'+suffix,
            cookies=kw.pop('cookies',self.cookies),**kw)
    def sql(self,sql,args=()):
        with sqlite3.connect(self.path) as db:return db.execute(sql,args).fetchall()
    def configure(self,**changes):
        config=self.policy._transaction(lambda p,c:records.load(c,SCOPE).policy,write=False,with_connection=True)
        configure_index_fixture(self.policy,replace(config,**changes),actor=changes.get('approvers',('admin',))[0])
    def buckets(self,managed=False):return [v['scope']['bucket_id'] for v in self.call(managed=managed)['body']['data']['items']]

    def test_reader_only_public_revision_and_complete_empty_publication(self):
        self.sign_in('reader-sub');before=self.call()
        self.assertEqual(before['status'],200)
        data=before['body']['data'];self.assertEqual(set(data),{'workspace_id','items','next_after'})
        self.assertEqual([v['scope']['bucket_id'] for v in data['items']],['bucket','empty'])
        self.assertTrue(all(set(v)=={'scope','catalog_revision'} for v in data['items']))
        self.assertEqual(data['items'][0]['catalog_revision'],1);self.assertIsNone(data['next_after'])
        stage(self.tmp.name);self.assertEqual(self.call()['body'],before['body'])
        for secret in ('private-candidate','synthetic-reference','synthetic-bucket','readers','observed_at','hidden','unpublished'):
            self.assertNotIn(secret,str(data))
        self.assertEqual(request(self.app,'/api/workspaces/alpha/artifacts/source/empty/quarters',cookies=self.cookies)['body']['data']['quarters'],[])

    def test_reader_neither_reads_nor_decodes_candidate_even_when_corrupt(self):
        self.sql("UPDATE artifact_indexes_v1 SET candidate=? WHERE workspace='alpha'",('private-invalid-json',))
        projections=[];connect=self.policy._connect
        def traced():
            db=connect();db.set_trace_callback(lambda sql:projections.append(sql));return db
        with patch.object(self.policy,'_connect',side_effect=traced):self.assertEqual(self.call()['status'],200)
        selects=[sql for sql in projections if 'SELECT revision,' in sql]
        self.assertTrue(selects);self.assertTrue(all(',NULL,' in sql and 'substr(candidate' not in sql for sql in selects))
        self.assertEqual(self.call(managed=True)['status'],503)

    def test_three_workspaces_identical_names_are_not_a_union(self):
        for workspace in ('alpha','beta','commons'):
            data=self.call(workspace)['body']['data'];self.assertTrue(data['items'])
            self.assertTrue(all(i['scope']['workspace_id']==workspace for i in data['items']))
        self.sign_in('reader-sub')
        for workspace in ('beta','commons'):self.assertEqual(self.call(workspace)['status'],404)
        self.sign_in('outsider-sub');self.assertEqual(self.call()['status'],404)

    def test_status_and_directory_share_capabilities_and_no_implicit_admin_grant(self):
        for item in self.call(managed=True)['body']['data']['items']:
            s=item['scope'];status=request(self.app,f'/api/workspaces/alpha/artifact-admin/{s["connection_id"]}/{s["bucket_id"]}/status',cookies=self.cookies)
            self.assertEqual(item['capabilities'],status['body']['data']['capabilities'])
            self.assertEqual(set(item),{'scope','capabilities'})
        self.assertEqual(self.buckets(True),['bucket','empty','unpublished'])
        self.sign_in('second-sub') # admin without grant on the standard scope
        self.assertEqual(self.buckets(True),['hidden']);self.assertEqual(self.buckets(),[])
        self.sign_in('reader-sub');self.assertEqual(self.call(managed=True)['status'],403)
        # Non-reader approver sees a management scope, not a reader catalog.
        self.sign_in();self.configure(readers=('reader',),approvers=('second',),refreshers=('admin',))
        self.sign_in('second-sub');self.assertIn('bucket',self.buckets(True));self.assertNotIn('bucket',self.buckets())

    def test_read_only_write_only_disabled_host_refresh_capability_matrix(self):
        self.sql("UPDATE modules SET can_write=0 WHERE workspace_id='alpha' AND key='artifacts'")
        item=self.call(managed=True)['body']['data']['items'][0]
        self.assertEqual(item['capabilities'],{'refresh':False,'review':True,'approve':False})
        self.sql("UPDATE modules SET can_write=1,can_read=0 WHERE workspace_id='alpha' AND key='artifacts'")
        self.assertEqual(self.call()['status'],403);self.assertEqual(self.buckets(True),[])
        # The actual opt-in host is used for write-only refresh, not a fake API flag.
        from packages.artifact_refresh.tests.fixtures import setup
        with tempfile.TemporaryDirectory() as d:
            app,policy,login,exchange,pages=setup(d);session=fixtures.session(login,exchange)
            cookies=cookie_header({_SESSION:session.token.value,_CSRF:session.csrf.value})
            with sqlite3.connect(Path(d)/'policy.sqlite') as db:db.execute("UPDATE modules SET can_read=0 WHERE workspace_id='alpha' AND key='artifacts'")
            result=request(app,'/api/workspaces/alpha/artifact-admin/scopes',cookies=cookies)
            self.assertEqual(result['status'],200);self.assertEqual(result['body']['data']['items'][0]['capabilities'],{'refresh':True,'review':False,'approve':False})
            self.assertEqual(pages.calls,[]);self.assertEqual(pages.loads,[])

    def test_availability_precedes_schema_scan_and_status_preserves_lifecycle_error(self):
        self.sql("UPDATE modules SET state='disabled' WHERE workspace_id='alpha' AND key='artifacts'")
        with patch('packages.artifact_sync.discovery.check_schema',side_effect=AssertionError('must not scan')):
            self.assertEqual(self.call()['status'],409);self.assertEqual(self.call(managed=True)['status'],409)
        self.assertEqual(request(self.app,'/api/workspaces/alpha/artifact-admin/source/bucket/status',cookies=self.cookies)['status'],409)

    def test_stale_publication_identity_and_membership_revocation(self):
        self.configure(approvers=('second',));self.sign_in('reader-sub')
        self.policy.set_membership('admin','alpha','second',role='developer',state='active',expected_revision=1)
        self.assertNotIn('bucket',self.buckets())
        self.policy.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
        self.assertEqual(self.call()['status'],404)

    def test_discovery_selection_is_not_authority_after_reader_grant_change(self):
        self.sign_in('reader-sub');selected=self.call()['body']['data']['items'][0]['scope']
        self.configure(readers=('admin',))
        path=f'/api/workspaces/{selected["workspace_id"]}/artifacts/{selected["connection_id"]}/{selected["bucket_id"]}/quarters'
        self.assertEqual(request(self.app,path,cookies=self.cookies)['status'],404)
        self.assertNotIn('bucket',self.buckets())

    def test_developer_and_viewer_grants_never_gain_management_and_archives_are_read_only(self):
        self.policy.set_membership('admin','alpha','reader',role='developer',state='active',expected_revision=1)
        self.sign_in('reader-sub');self.assertEqual(self.call()['status'],200);self.assertEqual(self.call(managed=True)['status'],403)
        self.sign_in();self.sql("UPDATE workspaces SET state='archived' WHERE id='alpha'")
        self.assertEqual(self.call()['status'],200);self.assertEqual(self.call(managed=True)['status'],409)
        self.sql("UPDATE workspaces SET state='suspended' WHERE id='alpha'")
        self.assertEqual(self.call()['status'],409);self.assertEqual(self.call(managed=True)['status'],409)

    def test_cookie_expiry_logout_no_bearer_and_no_identity_transition(self):
        self.assertEqual(self.call(cookies='',extra={'HTTP_X_USER':'admin'})['status'],401)
        self.assertEqual(self.call(extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
        self.login.revoke_subject('admin-sub');self.assertEqual(self.call()['status'],401)
        self.sign_in();self.now=self.session.expires_at;self.assertEqual(self.call()['status'],401)
        self.sign_in()
        from packages.workspaces.tests.identity_fixture import simulate_identity
        with sqlite3.connect(self.path) as db:
            simulate_identity(db, "UPDATE principals SET generation=generation+1 WHERE id='admin'")
        self.assertEqual(self.call()['status'],401)

    def test_true_subject_aba_cannot_revive_old_cookie_or_directory(self):
        self.assertEqual(self.call()['status'],200)
        from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant
        with sqlite3.connect(self.path) as db:
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='temporary-sub' WHERE id='admin'")
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        self.assertEqual(self.call()['body'],{'error':'authentication_required'})
        self.sign_in() # New cookie, same display ID, but no module grant reincarnation.
        self.assertEqual(self.call()['body']['data']['items'],[])
        self.assertEqual(self.call(managed=True)['body']['data']['items'],[])

    def test_bad_readable_publication_scope_fingerprint_schema_fail_without_partial_list(self):
        for sql,args in [
            ("UPDATE artifact_indexes_v1 SET published=? WHERE workspace='alpha' AND bucket='bucket'",('invalid',)),
            ("UPDATE artifact_indexes_v1 SET policy=? WHERE workspace='alpha' AND bucket='hidden'",('invalid',))]:
            with self.subTest(sql=sql):
                original=self.sql('SELECT policy,published FROM artifact_indexes_v1 WHERE workspace=? AND bucket=?',('alpha','hidden' if 'policy=' in sql else 'bucket'))[0]
                self.sql(sql,args);self.assertEqual(self.call()['status'],503)
                self.sql('UPDATE artifact_indexes_v1 SET policy=?,published=? WHERE workspace=? AND bucket=?',(*original,'alpha','hidden' if 'policy=' in sql else 'bucket'))
        self.sql('ALTER TABLE artifact_indexes_v1 ADD COLUMN unexpected TEXT')
        self.assertEqual(self.call()['status'],503)

    def test_full_publication_codec_rejects_misbound_scope_and_fingerprint(self):
        original=self.sql("SELECT published FROM artifact_indexes_v1 WHERE workspace='alpha' AND bucket='bucket'")[0][0]
        for change in ('scope','digest'):
            bound=json.loads(original)
            if change=='scope':
                bound['snapshot']['scope']['workspace_id']='beta'
                bound['provenance']['snapshot_digest']=records.digest(records.dump(bound['snapshot']))
            else:bound['provenance']['snapshot_digest']='0'*64
            self.sql("UPDATE artifact_indexes_v1 SET published=? WHERE workspace='alpha' AND bucket='bucket'",(records.dump(bound),))
            self.assertEqual(self.call()['body'],{'error':'storage_unavailable'})
        self.sql("UPDATE artifact_indexes_v1 SET published=? WHERE workspace='alpha' AND bucket='bucket'",(original,))
        self.sql("UPDATE artifact_indexes_v1 SET connection=? WHERE workspace='alpha' AND bucket='hidden'",('x'*129,))
        self.assertEqual(self.call()['status'],503)

    def test_approver_identity_aba_invalidates_publication_without_reader_revocation(self):
        self.configure(approvers=('second',));self.sign_in('reader-sub')
        from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant
        with sqlite3.connect(self.path) as db:
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='other' WHERE id='second'")
            simulate_identity_with_core_regrant(db,"UPDATE principals SET subject='second-sub' WHERE id='second'")
        self.assertNotIn('bucket',self.buckets())

    def test_expiry_after_directory_read_rejects_whole_response(self):
        from packages.artifact_sync import application as module
        original=module.directory
        def expired(*args,**kwargs):
            result=original(*args,**kwargs)
            self.assertTrue(result['items'])
            self.now=self.session.expires_at
            return result
        with patch.object(module,'directory',side_effect=expired):
            result=self.call()
            self.assertEqual(result['status'],401)
            self.assertEqual(result['body'],{'error':'authentication_required'})

    def test_actual_256_key_boundary_then_257_fails_before_decode(self):
        scopes=[Scope('alpha','more',f'b{i:03d}') for i in range(MAX_SCOPES-4)]
        for offset in range(0,len(scopes),100):
            initialize_indexes(self.policy,[IndexPolicy(Connection(s,'synthetic-bucket','eu-west-101'),'synthetic-reference',('outsider',),('second',),('second',)) for s in scopes[offset:offset+100]])
        self.assertEqual(self.call()['status'],200)
        initialize_indexes(self.policy,[IndexPolicy(Connection(Scope('alpha','more','extra'),'synthetic-bucket','eu-west-101'),'synthetic-reference',('outsider',),('second',),('second',))])
        with patch('packages.artifact_sync.discovery.authorized',side_effect=AssertionError('no partial decode')):
            self.assertEqual(self.call()['body'],{'error':'scope_directory_unavailable'})

    def test_denied_snapshot_corruption_is_never_decoded(self):
        self.sql("UPDATE artifact_indexes_v1 SET published=?,candidate=? WHERE workspace='alpha' AND bucket='hidden'",('invalid','invalid'))
        self.assertEqual(self.call()['status'],200);self.assertEqual(self.call(managed=True)['status'],200)

    def add_scopes(self,count):
        scopes=[Scope('alpha','source',f'p{i:03d}') for i in range(count)]
        for offset in range(0,count,100):
            initialize_indexes(self.policy,[IndexPolicy(Connection(s,'synthetic-bucket','eu-west-101'),'synthetic-reference',('admin',),('admin',),('admin',)) for s in scopes[offset:offset+100]])
        for s in scopes:seed_publication(self.policy,s,Snapshot(s,'2026-01-01T00:00:00Z',()))

    def test_visible_only_pagination_cursor_and_raw_key_capacity(self):
        self.add_scopes(26);first=self.call()['body']['data'];self.assertEqual(len(first['items']),25)
        after=first['next_after'];self.assertRegex(after,r'^dcr1_[A-Za-z0-9_-]{43}$')
        last=self.call(suffix='?after='+after)['body']['data'];self.assertEqual(len(last['items']),3);self.assertIsNone(last['next_after'])
        self.assertEqual(self.call(suffix='?after=source~hidden')['status'],409)
        self.assertEqual(self.call(suffix='?after=source~absent')['status'],409)
        self.sql("UPDATE artifact_indexes_v1 SET published=NULL,published_revision=NULL WHERE bucket=?",(first['items'][-1]['scope']['bucket_id'],))
        self.assertEqual(self.call(suffix='?after='+after)['status'],409)
        # A low cap models overflow without decoding or returning a partial page.
        with patch('packages.artifact_sync.discovery.MAX_SCOPES',3):
            self.assertEqual(self.call()['body'],{'error':'scope_directory_unavailable'})

    def test_snapshot_budget_is_charged_only_after_explicit_grant(self):
        policy_raw,published=self.sql("SELECT policy,published FROM artifact_indexes_v1 WHERE workspace='alpha' AND bucket='bucket'")[0]
        # Enough for policy alone, not for this granted publication.
        with patch('packages.artifact_sync.discovery.MAX_DIRECTORY_BYTES',len(policy_raw.encode())+len(published.encode())-1):
            self.assertEqual(self.call()['status'],503)
        # The same hidden corrupt snapshots do not consume the snapshot budget.
        self.sql("UPDATE artifact_indexes_v1 SET candidate=? WHERE workspace='alpha' AND bucket='bucket'",('x'*1048576,))
        self.assertEqual(self.call()['status'],200)
        self.assertEqual(self.call(managed=True)['status'],503)

    def test_aggregate_budget_and_malformed_queries(self):
        for managed in (False,True):
            with patch('packages.artifact_sync.discovery.MAX_DIRECTORY_BYTES',1):
                self.assertEqual(self.call(managed=managed)['body'],{'error':'scope_directory_unavailable'})
        for suffix in ('?after=source','?after=source~bucket&after=source~empty','?limit=999','?after=source~bucket~other','?after='):
            self.assertEqual(self.call(suffix=suffix)['status'],400 if suffix.startswith('?limit=') else 409)
        self.assertEqual(request(self.app,'/api/workspaces/'+('a'*1001)+'/artifacts/scopes',cookies=self.cookies)['status'],400)
        self.assertEqual(self.call(method='POST',body={},origin=fixtures.ORIGIN,csrf=self.session.csrf.value)['status'],404)

    def test_discovery_is_read_only_and_default_host_admin_gate(self):
        with patch.object(self.app._host._artifact_admin._service,'_credential_loader',side_effect=AssertionError('no provider')):
            before=self.sql('SELECT * FROM artifact_index_events_v1');self.assertEqual(self.call()['status'],200);self.assertEqual(self.call(managed=True)['status'],200)
            self.assertEqual(self.sql('SELECT * FROM artifact_index_events_v1'),before)
        with tempfile.TemporaryDirectory() as d:
            app,*_=application(d,administration=False)
            self.assertEqual(request(app,'/api/workspaces/alpha/artifact-admin/scopes')['status'],404)
