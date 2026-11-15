"""Synthetic real-policy tests for the new management surface."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from packages.artifact_admin import ArtifactAdministrationAPI, ArtifactAdministrationUI
from packages.artifact_sync import ArtifactSynchronizer, IndexedArtifactApplication, initialize_indexes
from packages.artifact_sync.records import policy_text
from packages.artifact_sync.tests.authority_fixtures import configure_index_fixture
from packages.artifact_sync.tests.fixtures import NOW, POLICY, SCOPE, Transport, Verifier, call as read_call, credential, initialize, provider, user

BASE='/api/workspaces/alpha/artifact-admin/source/bucket'
CONFIRM='approve_complete_candidate_to_reviewed_readers'


class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'synthetic.sqlite3';self.store=initialize(self.path)
        initialize_indexes(self.store,(POLICY,))
        self.verifier=Verifier();self.now=NOW;self.loads=[];self.transport=Transport()
        def loader(ref,scope,origin):self.loads.append(scope);return credential(scope,origin)
        args=dict(store=self.store,providers=(provider(self.verifier),),provider_key='test',clock=lambda:self.now)
        self.service=ArtifactSynchronizer(**args,credential_loader=loader,transport=self.transport)
        self.api=ArtifactAdministrationAPI(service=self.service)
        self.read=IndexedArtifactApplication(**args)
        self.ui=ArtifactAdministrationUI(api=self.api,origin='https://collab.example.invalid')

    def call(self,action='status',*,who='admin',method='GET',body=None,raw=None,revision=None,path=None,extra=None,wrapped=False):
        data=raw if raw is not None else json.dumps(body).encode() if body is not None else b''
        env={'PATH_INFO':path or BASE+'/'+action,'REQUEST_METHOD':method,'QUERY_STRING':'',
             'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data),
             'HTTP_HOST':'collab.example.invalid','wsgi.url_scheme':'https'}
        if who is not None:env['HTTP_AUTHORIZATION']='Bearer synthetic-'+who
        if revision is not None:env['HTTP_IF_MATCH']=f'"{revision}"'
        env.update(extra or {});result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        payload=b''.join((self.ui if wrapped else self.api)(env,start));result['raw']=payload
        if result['headers']['Content-Type'].startswith('application/json'):result['body']=json.loads(payload)
        return result

    def refresh(self):return self.call('refresh',method='POST',body={},revision=1)
    def preview(self):return self.call('review',who='approver',revision=2)['body']['data']
    def body(self,preview):return {'candidate_digest':preview['candidate_digest'],'readers':preview['readers'],'confirmation':CONFIRM}
    def state(self):
        with sqlite3.connect(self.path) as c:return c.execute('SELECT * FROM artifact_indexes_v1').fetchall(),c.execute('SELECT * FROM artifact_index_events_v1').fetchall()

    def test_capabilities_are_explicit_and_no_reader_candidate_status(self):
        self.assertEqual(self.call()['body']['data']['capabilities'],{'refresh':True,'review':False,'approve':False})
        self.assertEqual(self.call(who='approver')['body']['data']['capabilities'],{'refresh':False,'review':True,'approve':True})
        for who in ('reader','developer','other'):
            self.assertEqual(self.call(who=who)['status'],404)
        self.assertEqual(self.call(path=BASE.replace('alpha','commons')+'/status')['status'],404)
        self.assertNotIn('credential_ref',repr(self.call()))

    def test_full_candidate_digest_and_reader_set_then_publication(self):
        result=self.refresh();self.assertEqual(result['status'],200)
        self.assertEqual(read_call(self.read)['status'],404)
        preview=self.preview();self.assertTrue(preview['complete']);self.assertEqual(preview['count'],1)
        canonical=json.dumps(preview['snapshot'],sort_keys=True,ensure_ascii=True,separators=(',',':'))
        self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(),preview['candidate_digest'])
        self.assertEqual(preview['readers'],['admin','reader'])
        self.assertEqual(self.call('review',revision=2)['status'],404)
        result=self.call('approve',method='POST',who='approver',revision=2,body=self.body(preview))
        self.assertEqual(result['status'],200);self.assertEqual(result['body']['data']['revision'],3)
        self.assertEqual(read_call(self.read)['status'],200)
        self.assertFalse(self.call()['body']['data']['pending'])
        self.assertEqual(self.call('review',who='approver',revision=3)['status'],404)

    def test_refresh_only_does_not_receive_names_or_reader_list(self):
        result=self.refresh();self.assertNotIn('builds/one',repr(result));self.assertNotIn('readers',result['body']['data'])
        self.assertEqual(self.call('review',revision=2)['status'],404)

    def test_no_fake_identity_or_endpoint_credential_injection(self):
        self.assertEqual(self.call(who=None,extra={'REMOTE_USER':'admin','HTTP_X_USER':'admin','HTTP_COOKIE':'role=admin'})['status'],401)
        for body in ({'actor':'admin'},{'url':'https://private.invalid'},{'credential':'DO_NOT_LEAK'},{'scope':SCOPE.as_dict()}):
            self.assertEqual(self.call('refresh',method='POST',body=body,revision=1)['status'],400)
        self.assertEqual(self.loads,[]);self.assertEqual(self.transport.calls,[])

    def test_revision_preconditions_and_no_automatic_replay(self):
        self.assertEqual(self.call('refresh',method='POST',body={})['status'],428)
        self.assertEqual(self.call('refresh',method='POST',body={},revision=2)['status'],412)
        self.refresh();before=self.state()
        self.assertEqual(self.refresh()['status'],412);self.assertEqual(self.state(),before)
        self.assertEqual(self.call('review',who='approver',revision=1)['status'],412)

    def test_exact_reader_audience_required_and_concurrent_change_detected(self):
        self.refresh();preview=self.preview();body=self.body(preview)
        for readers in ([],['reader'],['reader','admin'],['admin','reader','reader'],None,'reader'):
            altered={**body,'readers':readers};self.assertIn(self.call('approve',who='approver',method='POST',revision=2,body=altered)['status'],(400,412))
        configure_index_fixture(self.store,replace(POLICY,readers=('reader',)))
        before=self.state()
        result=self.call('approve',who='approver',method='POST',revision=2,body=body)
        self.assertEqual(result['status'],412);self.assertEqual(self.state(),before)

    def test_explicit_confirmation_and_digest_not_optional(self):
        self.refresh();body=self.body(self.preview());before=self.state()
        for altered in ({k:v for k,v in body.items() if k!='confirmation'},{**body,'confirmation':'yes'},{**body,'candidate_digest':'0'*64},{**body,'extra':'x'}):
            self.assertIn(self.call('approve',who='approver',method='POST',revision=2,body=altered)['status'],(400,412))
        self.assertEqual(self.state(),before)

    def test_current_denial_before_network_and_approval(self):
        self.assertEqual(self.call('refresh',who='reader',method='POST',body={},revision=1)['status'],403)
        self.assertEqual(self.call('refresh',who='approver',method='POST',body={},revision=1)['status'],404)
        self.assertEqual(self.loads,[])
        self.refresh();body=self.body(self.preview());before=self.state()
        self.store.set_membership('admin','alpha','approver',role='viewer',state='active',expected_revision=1)
        self.assertEqual(self.call('approve',who='approver',method='POST',revision=2,body=body)['status'],403)
        self.assertEqual(self.state(),before)

    def test_write_only_module_cannot_skip_preview_requirement(self):
        self.refresh();body=self.body(self.preview())
        with sqlite3.connect(self.path) as c:c.execute("UPDATE modules SET can_read=0 WHERE workspace_id='alpha'")
        result=self.call(who='approver');self.assertEqual(result['status'],404)
        self.assertEqual(self.call('approve',who='approver',method='POST',revision=2,body=body)['status'],403)

    def test_expiry_after_provider_return_is_401_and_rolls_back(self):
        before=self.state();self.transport.hook=lambda:setattr(self,'now',NOW+timedelta(hours=3))
        self.assertEqual(self.refresh()['status'],401);self.assertEqual(self.state(),before)

    def test_provider_failure_has_no_raw_details(self):
        self.transport.pages=[b'DO_NOT_LEAK_PROVIDER_RESPONSE']
        result=self.refresh();self.assertEqual(result['status'],502)
        self.assertEqual(result['body'],{'error':'provider_unavailable'});self.assertEqual(len(self.state()[1]),0)

    def test_current_verifier_invoked_once_per_route(self):
        calls=[];self.verifier.hook=lambda:calls.append(1)
        self.refresh();self.assertEqual(len(calls),1)
        self.preview();self.assertEqual(len(calls),2)

    def test_scope_and_routes_have_no_cross_fallback(self):
        self.refresh()
        for path in (BASE.replace('alpha','beta')+'/review',BASE.replace('/source/','/other/')+'/review',BASE.replace('/bucket','/other')+'/review'):
            self.assertEqual(self.call(path=path,who='approver',revision=2)['status'],404)
        for action,method in (('download','GET'),('approve','GET'),('status','POST'),('resources','GET')):
            self.assertEqual(self.call(action,method=method)['status'],404)
        self.assertEqual(self.call(extra={'QUERY_STRING':'credential=bad'})['status'],404)

    def test_json_duplicate_framing_and_boundaries(self):
        for raw in (b'[]',b'{"a":1,"a":2}',b'NaN',b'\xff',b'{"__proto__":{}}'):
            self.assertEqual(self.call('refresh',method='POST',raw=raw,revision=1)['status'],400)
        self.assertEqual(self.call('refresh',method='POST',raw=b'{}',revision=1,extra={'CONTENT_LENGTH':'20000'})['status'],413)
        self.assertEqual(self.call('refresh',method='POST',raw=b'{}',revision=1,extra={'CONTENT_LENGTH':'3'})['status'],400)
        self.assertEqual(self.call('refresh',method='POST',raw=b'{}',revision=1,extra={'CONTENT_TYPE':'text/plain'})['status'],415)

    def test_ui_fixed_origin_blocks_cross_origin_before_mutations(self):
        for extra in ({'HTTP_HOST':'evil.invalid'},{'wsgi.url_scheme':'http'},{'HTTP_ORIGIN':'https://evil.invalid'},{'HTTP_SEC_FETCH_SITE':'cross-site'}):
            self.assertEqual(self.call('refresh',method='POST',body={},revision=1,extra=extra,wrapped=True)['status'],403)
        self.assertEqual(self.loads,[])

    def test_ui_allowlist_and_no_embedded_secrets(self):
        for suffix in ('','client.mjs','browser.mjs','style.css'):
            result=self.call(path='/artifact-admin/'+suffix,wrapped=True)
            self.assertEqual(result['status'],200);self.assertEqual(result['headers']['Cache-Control'],'no-store')
            self.assertIn("frame-ancestors 'none'",result['headers']['Content-Security-Policy'])
            self.assertNotIn(b'synthetic-reader',result['raw']);self.assertNotIn('Set-Cookie',result['headers'])
        for path in ('/artifact-admin','/artifact-admin/tests/client.test.mjs','/artifact-admin/../LICENSE','/artifacts/'):
            self.assertEqual(self.call(path=path,wrapped=True)['status'],404)
        self.assertEqual(self.call(path='/artifact-admin/',method='HEAD',wrapped=True)['raw'],b'')
        self.assertEqual(self.call(wrapped=True)['status'],200)

    def test_factory_rejects_unconfigured_app_and_nonsecure_origin(self):
        with self.assertRaises(ValueError):ArtifactAdministrationAPI(service=None)
        for origin in (None,'http://localhost:8000','https://user@host.invalid','https://host.invalid/path','https://host.invalid?x','https://host.invalid#x'):
            with self.assertRaises(ValueError):ArtifactAdministrationUI(api=self.api,origin=origin)


if __name__=='__main__':unittest.main()
