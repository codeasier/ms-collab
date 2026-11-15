"""Real SQLite policy/index transactions, synthetic verified identities and XML."""
from dataclasses import replace
from datetime import timedelta
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.artifact_sync import ArtifactSynchronizer, IndexedArtifactApplication, IndexPolicy, ProviderError, initialize_indexes
from packages.artifact_sync import records
from packages.artifacts import Scope
from packages.artifacts_ui import ArtifactBrowserUI
from packages.identity import AuthenticationError
from packages.workspaces import PolicyError, StorageError
from fixtures import BASE, CONFIG, NOW, POLICY, SCOPE, Transport, Verifier, call, credential, initialize, page, provider, user


class IndexTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'policy.sqlite3';self.store=initialize(self.path)
        initialize_indexes(self.store,(POLICY,))
        self.verifier=Verifier();self.now=NOW;self.loader_calls=[]
        self.transport=Transport()
        def loader(ref,scope,origin):
            self.loader_calls.append((ref,scope,origin));return credential(scope,origin)
        self.loader=loader
        self.args=dict(store=self.store,providers=(provider(self.verifier),),provider_key='test',clock=lambda:self.now)
        self.service=ArtifactSynchronizer(**self.args,credential_loader=self.loader,transport=self.transport)
        self.app=IndexedArtifactApplication(**self.args)

    def refresh(self,revision=1,who='admin'):
        return self.service.refresh(user(who),SCOPE,expected_revision=revision)

    def approve(self,result=None):
        result=result or self.refresh()
        return self.service.approve(user('approver'),SCOPE,expected_revision=result['revision'],candidate_digest=result['candidate_digest'])

    def state(self):
        with sqlite3.connect(self.path) as c:
            return c.execute('SELECT * FROM artifact_indexes_v1').fetchall(),c.execute('SELECT * FROM artifact_index_events_v1').fetchall()

    def query(self,**kw):
        return call(self.app,'/query',method='POST',body={'quarter':'2026Q1','definition':{'files_only':True}},**kw)

    def next_page(self,rows=()):
        self.now+=timedelta(minutes=1);self.transport.calls=[];self.transport.pages=[page(rows)]

    def test_refresh_is_private_candidate_until_explicit_full_review_approval(self):
        result=self.refresh()
        self.assertEqual(result['revision'],2);self.assertEqual(result['publication'],'pending')
        self.assertEqual(call(self.app)['status'],404)
        review=self.service.review(user('approver'),SCOPE,expected_revision=2)
        self.assertEqual(review['candidate_digest'],result['candidate_digest'])
        self.assertEqual(review['snapshot'].artifacts[0].key,'builds/one.txt')
        self.assertEqual(review['readers'],('admin','reader'))
        self.assertNotIn('synthetic-secret',repr(review))
        self.approve(result)
        result=self.query();self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['data']['catalog_revision'],3)
        self.assertEqual(result['body']['data']['items'][0]['key'],'builds/one.txt')
        self.assertFalse(result['body']['data']['source']['download_available'])
        self.assertEqual([r[5] for r in self.state()[1]],['refresh','approve'])

    def test_durable_restart_and_browser_wrapper_use_same_index(self):
        self.approve()
        app=IndexedArtifactApplication(**self.args)
        self.assertEqual(call(app)['status'],200)
        wrapper=ArtifactBrowserUI(api=app,origin='http://127.0.0.1:8123')
        result=call(wrapper,extra={'HTTP_HOST':'127.0.0.1:8123','SERVER_NAME':'127.0.0.1','SERVER_PORT':'8123','wsgi.url_scheme':'http'})
        self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['data']['catalog_revision'],3)

    def test_real_loopback_http_published_index(self):
        self.approve()
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server('127.0.0.1',0,self.app,handler_class=Quiet)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('GET',BASE+'/quarters',headers={'Authorization':'Bearer synthetic-reader'})
            response=conn.getresponse();self.assertEqual(response.status,200)
            self.assertEqual(json.loads(response.read())['data']['quarters'],['2026Q1']);conn.close()
        finally:server.shutdown();thread.join();server.server_close()

    def test_non_admin_and_missing_explicit_grants_deny_before_secret_or_network(self):
        for who in ('reader','developer','approver','other'):
            with self.subTest(who=who),self.assertRaises(PolicyError):self.refresh(who=who)
        self.assertEqual(self.loader_calls,[]);self.assertEqual(self.transport.calls,[])

    def test_read_grants_not_inferred_from_membership_or_operator(self):
        self.approve()
        for who in ('developer','approver','other'):
            self.assertEqual(call(self.app,token=who)['status'],404)
        self.assertEqual(call(self.app,token=None,extra={'REMOTE_USER':'admin','HTTP_X_USER':'admin'})['status'],401)

    def test_cross_scope_and_shared_space_are_not_fallbacks(self):
        self.approve()
        for base in (BASE.replace('alpha','beta'),BASE.replace('alpha','commons'),BASE.replace('/source/','/other/'),BASE.replace('/bucket','/other')):
            r=call(self.app,base=base);self.assertEqual((r['status'],r['body']),(404,{'error':'not_found'}))
        common=Scope('commons','source','bucket')
        initialize_indexes(self.store,(replace(POLICY,connection=replace(CONFIG,scope=common)),))
        self.assertEqual(call(self.app,base=BASE.replace('alpha','commons'))['status'],404)
        self.assertEqual(self.query()['body']['data']['total'],1)

    def test_named_view_no_broad_fallback(self):
        self.approve()
        self.assertEqual(call(self.app,'/views/files',query='quarter=2026Q1')['status'],200)
        self.assertEqual(call(self.app,'/views/missing',query='quarter=2026Q1')['status'],404)
        self.assertEqual(call(self.app,'/download')['status'],404)

    def test_approval_current_admin_and_explicit_grant_and_exact_digest(self):
        result=self.refresh();before=self.state()
        for who in ('admin','reader','developer'):
            with self.assertRaises(PolicyError):self.service.approve(user(who),SCOPE,expected_revision=2,candidate_digest=result['candidate_digest'])
        with self.assertRaises(PolicyError):self.service.approve(user('approver'),SCOPE,expected_revision=2,candidate_digest='0'*64)
        self.assertEqual(self.state(),before)
        self.store.set_membership('admin','alpha','approver',role='viewer',state='active',expected_revision=1)
        with self.assertRaises(PolicyError):self.approve(result)
        self.assertEqual(self.state(),before)

    def test_stale_revision_fails_before_network(self):
        for revision in (None,True,0,2):
            with self.assertRaises(PolicyError):self.refresh(revision)
        self.assertEqual(self.loader_calls,[]);self.assertEqual(self.transport.calls,[])

    def test_revoke_during_verification_denies(self):
        self.verifier.hook=lambda:self.store.set_membership('approver','alpha','admin',role='admin',state='revoked',expected_revision=1)
        with self.assertRaises(PolicyError):self.refresh()
        self.assertEqual(self.loader_calls,[])

    def test_revoke_after_page_prevents_publication(self):
        before=self.state()
        self.transport.hook=lambda:self.store.set_membership('approver','alpha','admin',role='admin',state='revoked',expected_revision=1)
        with self.assertRaises(PolicyError):self.refresh()
        self.assertEqual(self.state(),before)

    def test_revoke_before_next_page_prevents_additional_network(self):
        self.transport.pages=[page((('builds/a','2026-01-01T00:00:00Z',1),),truncated=True,next_marker='builds/a')]
        self.transport.hook=lambda:self.store.set_membership('approver','alpha','admin',role='admin',state='revoked',expected_revision=1)
        with self.assertRaises(PolicyError):self.refresh()
        self.assertEqual(len(self.transport.calls),1)

    def test_demotion_disable_archive_expiry_during_network_roll_back(self):
        changes=(lambda:self.store.set_membership('approver','alpha','admin',role='developer',state='active',expected_revision=1),
                 lambda:self.store.set_module_state('admin','alpha','artifacts',state='disabling',expected_revision=1),
                 lambda:self.store.set_workspace_state('admin','alpha',state='archived',expected_revision=1),
                 lambda:setattr(self,'now',NOW+timedelta(hours=3)))
        for change in changes:
            with self.subTest(change=change):
                # A fresh fixture keeps policy revisions independent.
                self.setUp();before=self.state();self.transport.hook=change
                with self.assertRaises((PolicyError,AuthenticationError)):self.refresh()
                self.assertEqual(self.state(),before)

    def test_credential_binding_and_secret_provider_failure_do_not_change_state(self):
        for loader in (lambda *args:credential(Scope('beta','source','bucket')),lambda *args:(_ for _ in ()).throw(RuntimeError('DO_NOT_LEAK'))):
            service=ArtifactSynchronizer(**self.args,credential_loader=loader,transport=self.transport)
            before=self.state()
            with self.assertRaises(ProviderError) as error:service.refresh(user(),SCOPE,expected_revision=1)
            self.assertNotIn('DO_NOT_LEAK',str(error.exception));self.assertEqual(self.state(),before)
        self.assertEqual(self.transport.calls,[])

    def test_policy_change_during_network_detected_even_without_revision_change(self):
        before=self.state()
        def change():
            with sqlite3.connect(self.path) as c:
                c.execute('UPDATE artifact_indexes_v1 SET policy=?',(records.policy_text(replace(POLICY,credential_ref='other-ref')),))
        self.transport.hook=change
        with self.assertRaises((PolicyError,StorageError)):self.refresh()
        self.assertEqual(self.state()[0][0][3],1);self.assertEqual(self.state()[1],before[1])

    def test_concurrent_refresh_cas_does_not_overwrite_winner(self):
        original=self.transport.hook
        second=ArtifactSynchronizer(**self.args,credential_loader=self.loader,transport=Transport())
        self.transport.hook=lambda:second.refresh(user(),SCOPE,expected_revision=1)
        with self.assertRaisesRegex(PolicyError,'stale_revision'):self.refresh()
        self.assertEqual(self.state()[0][0][3],2);self.assertEqual(len(self.state()[1]),1)

    def test_incremental_merge_retains_missing_objects_and_keeps_published_until_approval(self):
        self.approve()
        self.next_page((('builds/two.txt','2026-01-02T00:00:00Z',2),))
        result=self.refresh(3)
        self.assertEqual(self.query()['body']['data']['total'],1)
        self.assertEqual(result['retained'],2)
        self.approve(result);self.assertEqual(self.query()['body']['data']['total'],2)
        self.next_page();result=self.refresh(5);self.approve(result)
        self.assertEqual(self.query()['body']['data']['total'],2)

    def test_pending_incremental_refresh_requires_new_digest(self):
        first=self.refresh();self.next_page((('builds/two','2026-01-02T00:00:00Z',4),))
        second=self.refresh(2)
        with self.assertRaises(PolicyError):self.approve(first)
        self.approve(second);self.assertEqual(self.query()['body']['data']['total'],2)

    def test_equal_or_older_observation_and_metadata_regression_rejected(self):
        self.approve();before=self.state();self.transport.calls=[]
        with self.assertRaisesRegex(ProviderError,'stale_observation'):self.refresh(3)
        for stamp,size in (('2026-01-01T22:59:59Z',12),('2026-01-01T23:00:00Z',13)):
            self.next_page((('builds/one.txt',stamp,size),))
            with self.assertRaisesRegex(ProviderError,'moving_or_invalid_listing'):self.refresh(3)
        self.assertEqual(self.state(),before)

    def test_failed_listing_retains_index_and_does_not_add_audit(self):
        self.approve();before=self.state();self.next_page();self.transport.pages=[b'<bad/>']
        with self.assertRaises(ProviderError):self.refresh(3)
        self.assertEqual(self.state(),before)

    def test_audit_failure_rolls_back_index_write(self):
        before=self.state()
        original=records.save
        def fail(*args,**kwargs):
            original(*args,**kwargs);raise StorageError('synthetic_failure')
        with patch('packages.artifact_sync.service.save',side_effect=fail),self.assertRaises(StorageError):self.refresh()
        self.assertEqual(self.state(),before)

    def test_expiry_after_write_rolls_back_index_and_audit(self):
        before=self.state();original=records.save
        def expire(*args,**kwargs):
            result=original(*args,**kwargs);self.now=NOW+timedelta(hours=3);return result
        with patch('packages.artifact_sync.service.save',side_effect=expire),self.assertRaises(AuthenticationError):self.refresh()
        self.assertEqual(self.state(),before)

    def test_corrupted_published_scope_fails_redacted_and_denied_actor_not_parsed(self):
        self.approve()
        with sqlite3.connect(self.path) as c:
            text=c.execute('SELECT published FROM artifact_indexes_v1').fetchone()[0]
            c.execute('UPDATE artifact_indexes_v1 SET published=?',(text.replace('"workspace_id":"alpha"','"workspace_id":"beta"'),))
        self.assertEqual(call(self.app)['body'],{'error':'storage_unavailable'})
        self.assertEqual(call(self.app,token='developer')['body'],{'error':'not_found'})

    def test_pending_private_metadata_not_parsed_or_visible_to_readers(self):
        self.approve()
        with sqlite3.connect(self.path) as c:c.execute('UPDATE artifact_indexes_v1 SET candidate=?',('{PRIVATE_MALFORMED',))
        self.assertEqual(call(self.app)['status'],200)
        self.assertNotIn('PRIVATE',repr(call(self.app)))
        with self.assertRaises(StorageError):self.service.review(user('approver'),SCOPE,expected_revision=3)

    def test_same_snapshot_read_then_next_request_observes_revocation(self):
        self.approve()
        with sqlite3.connect(self.path) as c:c.execute('PRAGMA journal_mode=WAL')
        original=records.decode_snapshot;ran=[]
        def revoke(raw,config):
            if not ran:
                ran.append(1);self.store.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
            return original(raw,config)
        with patch('packages.artifact_sync.records.decode_snapshot',side_effect=revoke):
            self.assertEqual(call(self.app)['status'],200)
        self.assertEqual(call(self.app)['status'],404)

    def test_repeat_initialization_safe_but_policy_overwrite_rejected(self):
        self.approve();before=self.state();initialize_indexes(self.store,(POLICY,))
        self.assertEqual(self.state(),before)
        with self.assertRaises(ValueError):initialize_indexes(self.store,(replace(POLICY,readers=('admin',)),))
        self.assertEqual(self.state(),before)

    def test_schema_degradation_and_changes_fail_at_runtime(self):
        self.approve()
        with sqlite3.connect(self.path) as c:
            c.execute('DROP TABLE artifact_index_events_v1')
            c.execute('CREATE TABLE artifact_index_events_v1(sequence,actor,workspace,connection,bucket,action,revision,digest)')
        self.assertEqual(call(self.app)['status'],503)
        with self.assertRaises(StorageError):initialize_indexes(self.store,(POLICY,))

    def test_noncanonical_schema_version_and_unknown_fields_rejected(self):
        self.approve();original=self.state()[0][0][4]
        for raw in (original.replace('"schema":1','"schema":2'),original.replace('"schema":1','"schema":true'),original[:-1]+',"secret":"BAD"}'):
            with sqlite3.connect(self.path) as c:c.execute('UPDATE artifact_indexes_v1 SET policy=?',(raw,))
            result=call(self.app);self.assertEqual(result['status'],503);self.assertNotIn('BAD',repr(result))

    def test_cumulative_index_limit_fails_without_pruning(self):
        from packages.artifacts import Artifact, Snapshot
        previous = Snapshot(SCOPE, '2026-01-01T23:59:00Z', tuple(
            Artifact(f'builds/key-{i:03}', '2026-01-01T23:00:00Z', 1) for i in range(500)))
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE artifact_indexes_v1 SET candidate=?', (records.snapshot_text(previous),))
        before = self.state()
        with self.assertRaisesRegex(ProviderError, 'index_limit_or_invalid_result'):
            self.refresh()
        self.assertEqual(self.state(), before)

    def test_dedicated_index_exceeds_legacy_resource_limit(self):
        from packages.artifacts import Artifact, Snapshot
        previous = Snapshot(SCOPE, '2026-01-01T23:59:00Z', tuple(
            Artifact(f'builds/long-metadata-key-{i:03}', '2026-01-01T23:00:00Z', 1) for i in range(200)))
        raw = records.snapshot_text(previous)
        self.assertGreater(len(raw), 10000)
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE artifact_indexes_v1 SET candidate=?', (raw,))
        result = self.refresh(); self.approve(result)
        self.assertEqual(self.query()['body']['data']['total'], 201)

    def test_no_sync_write_http_route_or_actor_parameter(self):
        with self.assertRaises(TypeError):self.service({},lambda *args:None)
        self.assertEqual(call(self.app,'/refresh',method='POST',body={})['status'],404)
        self.assertEqual(call(self.app,'/query',method='POST',body={'quarter':'2026Q1','definition':{},'actor':'admin'})['status'],400)


if __name__=='__main__':unittest.main()
