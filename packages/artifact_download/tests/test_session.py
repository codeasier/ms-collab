"""Actual same-session download admission, byte binding, revocation and cleanup."""
from dataclasses import replace
from datetime import timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from packages.workspaces.tests.identity_fixture import simulate_identity
import unittest
from unittest.mock import patch

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests import fixtures
from packages.artifact_download import ArtifactDownloadConfiguration, ArtifactSessionDownloadAPI, DownloadInterrupted
from packages.artifact_download import records
from packages.artifact_download.tests.session_fixtures import setup_download, SCOPE, KEY, DATA, BINDING, GRANT
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header

BASE='/api/workspaces/alpha/artifact-download/source/bucket/'


class SessionDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=fixtures.NOW
        self.host,self.policy,self.login,self.exchange,self.objects=setup_download(self.tmp.name,clock=lambda:self.now)
        self.sign_in()
    def sign_in(self,subject='admin-sub'):
        self.session=fixtures.session(self.login,self.exchange,subject)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
    def call(self,action='download',*,body=None,extra=None,consume=True,app=None,start_error=False):
        raw=json.dumps(body if body is not None else {'key':KEY,**({'expected_sha256':BINDING.sha256} if action=='download' else {})}).encode()
        env={'PATH_INFO':BASE+action,'REQUEST_METHOD':'POST','QUERY_STRING':'','SCRIPT_NAME':'',
             'HTTP_HOST':'app.example.invalid','wsgi.url_scheme':'https','HTTP_ORIGIN':fixtures.ORIGIN,
             'HTTP_COOKIE':self.cookies,'HTTP_X_CSRF_TOKEN':self.session.csrf.value,'HTTP_IF_MATCH':'"1"',
             'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        env.update(extra or {});result={}
        def start(status,headers):
            result.update(status=int(status.split()[0]),headers=dict(headers))
            if start_error:raise RuntimeError('synthetic start failure')
        stream=(app or self.host)(env,start);result['stream']=stream
        if consume:
            try:result['raw']=b''.join(stream)
            finally:
                if hasattr(stream,'close'):stream.close()
        return result
    def leases(self):
        with sqlite3.connect(Path(self.tmp.name)/'policy.sqlite') as db:return db.execute('SELECT actor,state,offered FROM artifact_download_leases_v1 ORDER BY rowid').fetchall()
    def revoke(self):self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
    def test_workspace_hint_rejected_by_host_and_direct_binary_adapter_before_admission(self):
        for app in (self.host, self.host._artifact_downloads):
            for action in ('availability', 'download'):
                result = self.call(action, app=app, extra={'HTTP_X_WORKSPACE_ID': 'beta'})
                self.assertEqual((result['status'], json.loads(result['raw'])), (400, {'error': 'invalid_request'}))
                self.assertEqual(self.call(action, app=app, extra={'HTTP_X_WORKSPACE_ID': 'beta', 'HTTP_COOKIE': ''})['status'], 401)
                self.assertEqual(self.call(action, app=app, extra={'HTTP_X_WORKSPACE_ID': 'beta', 'HTTP_X_CSRF_TOKEN': ''})['status'], 401)
                self.assertEqual(self.call(action, app=app, extra={'PATH_INFO': BASE.replace('/alpha/', '/beta/') + action,
                                      'HTTP_X_WORKSPACE_ID': 'alpha'})['status'], 404)
        self.assertEqual(self.objects.loads, []); self.assertEqual(self.objects.calls, []); self.assertEqual(self.leases(), [])
        self.assertEqual(self.call('availability', extra={'HTTP_X_WORKSPACE_ID': 'alpha'})['status'], 200)
        result = self.call(extra={'HTTP_X_WORKSPACE_ID': 'alpha'})
        self.assertEqual((result['status'], result['raw']), (200, DATA))
        self.login.logout(self.session.token, self.session.csrf)
        self.assertEqual(self.call(extra={'HTTP_X_WORKSPACE_ID': 'beta'})['status'], 401)

    def test_availability_has_exact_bytes_no_secret_network_or_lease(self):
        r=self.call('availability');self.assertEqual(r['status'],200)
        self.assertEqual(json.loads(r['raw'])['data'],{'scope':SCOPE.as_dict(),'key':KEY,'size':len(DATA),'sha256':BINDING.sha256,'published_revision':1})
        self.assertEqual(self.objects.calls,[]);self.assertEqual(self.objects.loads,[]);self.assertEqual(self.leases(),[])
    def test_complete_cookie_download_binary_headers_and_session_cleanup(self):
        r=self.call();self.assertEqual(r['status'],200);self.assertEqual(r['raw'],DATA)
        h=r['headers'];self.assertEqual(h['Vary'],'Cookie, Origin');self.assertEqual(h['Content-Type'],'application/octet-stream')
        self.assertEqual(h['Pragma'],'no-cache');self.assertEqual(h['Cache-Control'],'no-store');self.assertEqual(h['X-Artifact-SHA256'],BINDING.sha256)
        self.assertNotIn('WWW-Authenticate',h);self.assertNotIn('Location',h);self.assertNotIn('Set-Cookie',h)
        self.assertTrue(all(r.closed for r in self.objects.responses));self.assertEqual(self.leases(),[('admin','complete',len(DATA))])
    def test_confirmation_digest_is_transactional_before_admission_and_upstream(self):
        self.assertEqual(self.call('availability')['status'],200)
        changed=replace(GRANT,bindings=(replace(BINDING,sha256='a'*64),))
        from packages.artifact_sync.tests.authority_fixtures import configure_bytes_fixture
        configure_bytes_fixture(self.policy,changed)
        self.assertEqual(self.call()['status'],412);self.assertEqual(self.leases(),[]);self.assertEqual(self.objects.calls,[]);self.assertEqual(self.objects.loads,[])
    def test_digest_is_constraint_not_authority_and_required_for_session(self):
        for body in ({'key':KEY},{'key':KEY,'expected_sha256':None},{'key':KEY,'expected_sha256':'A'*64},{'key':KEY,'expected_sha256':'0'*64}):
            self.assertIn(self.call(body=body)['status'],(400,412))
        self.sign_in('reader-sub');self.assertEqual(self.call()['status'],404);self.assertEqual(self.call('availability')['status'],404)
        self.assertEqual(self.objects.loads,[]);self.assertEqual(self.leases(),[])
    def test_cookie_csrf_origin_bearer_and_framing_fail_closed(self):
        variants=({'HTTP_COOKIE':''},{'HTTP_X_CSRF_TOKEN':''},{'HTTP_X_CSRF_TOKEN':'B'*43},{'HTTP_ORIGIN':'https://other.invalid'},
            {'HTTP_HOST':'other.invalid'},{'wsgi.url_scheme':'http'},{'SCRIPT_NAME':'/proxy'},{'HTTP_SEC_FETCH_SITE':'cross-site'},
            {'HTTP_AUTHORIZATION':'Bearer synthetic-admin'},{'HTTP_COOKIE':'actor=admin','HTTP_X_USER':'admin'})
        for extra in variants:
            r=self.call(extra=extra);self.assertEqual(r['status'],401);self.assertNotIn('WWW-Authenticate',r['headers']);self.assertEqual(r['headers']['Vary'],'Cookie, Origin')
        for extra in ({'HTTP_RANGE':'bytes=0-1'},{'HTTP_IF_RANGE':'"x"'},{'HTTP_TRANSFER_ENCODING':'chunked'}):self.assertEqual(self.call(extra=extra)['status'],400)
        for extra in ({'QUERY_STRING':'key=secret'},{'REQUEST_METHOD':'GET'},{'PATH_INFO':BASE+'download/more'}):self.assertEqual(self.call(extra=extra)['status'],404)
        self.assertEqual(self.objects.loads,[]);self.assertEqual(self.leases(),[])
    def test_scope_private_candidate_unbound_and_archived_denied(self):
        for path in (BASE.replace('/alpha/','/beta/'),BASE.replace('/alpha/','/commons/'),BASE.replace('/source/','/other/')):
            self.assertEqual(self.call(extra={'PATH_INFO':path+'download'})['status'],404)
        for key in ('builds/','builds/unbound.txt','builds/private.txt'):
            self.assertEqual(self.call(body={'key':key,'expected_sha256':BINDING.sha256})['status'],404)
        self.policy.set_workspace_state('admin','alpha',state='archived',expected_revision=1)
        self.assertEqual(self.call()['status'],404);self.assertEqual(self.objects.loads,[])
    def test_logout_between_output_chunks_interrupts_but_cleans_lease(self):
        r=self.call(consume=False);stream=r['stream'];self.assertEqual(len(next(stream)),65536)
        self.login.logout(self.session.token,self.session.csrf)
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertEqual(self.leases(),[('admin','interrupted',65536)])
        self.assertTrue(stream._spool.closed)
    def test_membership_revoke_between_chunks(self):
        r=self.call(consume=False);stream=r['stream'];next(stream);self.revoke()
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertEqual(self.leases()[0][1],'interrupted')
    def test_session_expiry_between_output_chunks_interrupts_and_cleans(self):
        r=self.call(consume=False);stream=r['stream'];next(stream)
        self.now=self.session.expires_at
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertEqual(self.leases(),[('admin','interrupted',65536)]);self.assertTrue(stream._spool.closed)
    def test_current_principal_binding_changes_interrupt_output(self):
        r=self.call(consume=False);stream=r['stream'];next(stream)
        with sqlite3.connect(Path(self.tmp.name)/'policy.sqlite') as db:
            simulate_identity(db, "UPDATE principals SET subject='changed-sub' WHERE id='admin'")
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertEqual(self.leases(),[('admin','interrupted',65536)])
    def test_archive_between_chunks_stops_external_download_output(self):
        r=self.call(consume=False);stream=r['stream'];next(stream)
        self.policy.set_workspace_state('admin','alpha',state='archived',expected_revision=1)
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertEqual(self.leases(),[('admin','interrupted',65536)])
    def test_close_before_first_chunk_releases_only_owned_lease(self):
        r=self.call(consume=False);r['stream'].close();r['stream'].close()
        self.assertEqual(self.leases(),[('admin','interrupted',0)]);self.assertTrue(r['stream']._spool.closed)
    def test_expiry_during_acquisition_leaves_no_bytes_and_frees_lease(self):
        self.objects.read_hook=lambda:setattr(self,'now',self.now+timedelta(days=2))
        r=self.call();self.assertEqual(r['status'],401);self.assertEqual(self.leases(),[('admin','failed',0)])
        self.assertTrue(self.objects.responses[0].closed)
    def test_per_actor_limit_and_cancel_release_share_policy_store(self):
        first=self.call(consume=False);self.assertEqual(first['status'],200);self.assertEqual(self.call()['status'],429)
        first['stream'].close();self.assertEqual(self.leases()[0][1],'interrupted');self.assertEqual(self.call()['status'],200)
    def test_hash_failure_has_no_binary_or_raw_errors(self):
        self.objects.data=b'x'*len(DATA);r=self.call();self.assertEqual(r['status'],502)
        self.assertEqual(json.loads(r['raw']),{'error':'provider_unavailable'});self.assertEqual(self.leases(),[('admin','failed',0)])
    def test_binary_start_failure_closes_spool_and_lease(self):
        downloader=self.host._artifact_downloads._downloader
        original=downloader._prepare_verified;streams=[]
        def prepare(*args,**kwargs):
            result=original(*args,**kwargs);streams.append(result);return result
        with patch.object(downloader,'_prepare_verified',side_effect=prepare):
            with self.assertRaisesRegex(RuntimeError,'synthetic start failure'):self.call(start_error=True)
        self.assertTrue(streams[0]._spool.closed);self.assertEqual(self.leases(),[('admin','interrupted',0)])
    def test_misconfigured_host_cannot_mount_or_inject_other_authority(self):
        for args in ({'artifact_downloads':True},{'modules':('artifacts',),'artifact_downloads':1}):
            with self.assertRaises(ValueError):HostConfig(fixtures.ORIGIN,**args)
        base=dict(config=self.host._config,sessions=self.host._boundary._sessions)
        with self.assertRaises(ValueError):ApplicationHost(**base)
        with self.assertRaises(ValueError):ArtifactDownloadConfiguration(None)
        with tempfile.TemporaryDirectory() as d:
            other,*_=setup_download(d,enabled=False)
            with self.assertRaises(ValueError):ArtifactSessionDownloadAPI(boundary=other._boundary,downloader=self.host._artifact_downloads._downloader)
            self.assertEqual(self.call(app=other)['status'],404)

if __name__=='__main__':unittest.main()
