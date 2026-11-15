"""Configured cookie-only wrapper integration. All data and identities synthetic."""
import io
import json
import re
from urllib.parse import urljoin
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.application import _FILES
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header


def request(app,path='/',*,method='GET',cookies='',origin=None,csrf=None,body=None,revision=None,extra=None):
    raw=b'' if body is None else json.dumps(body).encode()
    path,_,query=path.partition('?')
    env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'SCRIPT_NAME':'',
         'wsgi.url_scheme':'https','HTTP_HOST':'app.example.invalid','wsgi.input':io.BytesIO(raw),
         'CONTENT_LENGTH':str(len(raw)),'HTTP_COOKIE':cookies}
    if body is not None:env['CONTENT_TYPE']='application/json'
    if origin is not None:env['HTTP_ORIGIN']=origin
    if csrf is not None:env['HTTP_X_CSRF_TOKEN']=csrf
    if revision is not None:env['HTTP_IF_MATCH']=revision
    env.update(extra or {});result={}
    def start(status,headers):result.update(status=int(status.split()[0]),headers=headers)
    result['raw']=b''.join(app(env,start))
    if dict(result['headers'])['Content-Type'].startswith('application/json'):result['body']=json.loads(result['raw'])
    return result


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.now=fixtures.NOW
        self.host,self.policy,self.login,self.exchange=fixtures.setup(self.temp.name,clock=lambda:self.now)
        self.app=ApplicationUI(host=self.host)
        self.session=fixtures.session(self.login,self.exchange)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
    def read(self,path,**kwargs):return request(self.app,path,cookies=kwargs.pop('cookies',self.cookies),**kwargs)
    def write(self,path,**kwargs):
        options=dict(method='POST',body={},origin=fixtures.ORIGIN,csrf=self.session.csrf.value,revision='"1"');options.update(kwargs)
        return self.read(path,**options)
    def test_explicit_host_only_no_unconfigured_or_subclass_fallback(self):
        for value in (None,object(),self.host._boundary):
            with self.assertRaises(ValueError):ApplicationUI(host=value)
    def test_all_static_assets_are_bounded_public_content_with_csp(self):
        for path in _FILES:
            response=self.read(path,cookies='')
            if path=='/artifact-admin/':
                self.assertEqual(response['status'],404);continue
            self.assertEqual(response['status'],200,path)
            headers=dict(response['headers']);self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff');self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
            self.assertIn("script-src 'self'",headers['Content-Security-Policy']);self.assertNotIn("'unsafe-inline'",headers['Content-Security-Policy'])
            self.assertNotIn(self.session.token.value.encode(),response['raw']);self.assertNotIn(self.session.csrf.value.encode(),response['raw'])
            self.assertEqual(self.read(path,method='HEAD')['raw'],b'')
    def test_every_static_module_import_resolves_in_the_same_allowlist(self):
        for path in _FILES:
            if not path.endswith('.mjs'):continue
            raw=self.read(path)['raw'].decode()
            for target in re.findall(r"from\s*['\"]([^'\"]+)['\"]",raw):
                self.assertIn(urljoin(path,target),_FILES,(path,target))
    def test_html_never_prompts_for_credentials_or_contains_private_workspace_rows(self):
        for path in ('/','/manage/','/responsibility/','/pipelines/','/community/','/assistant/'):
            raw=self.read(path,cookies='')['raw'].decode()
            self.assertNotIn('type="password"',raw);self.assertNotIn('id="credential"',raw)
            self.assertNotIn('value="alpha"',raw);self.assertNotIn('Private synthetic issue',raw)
            self.assertIn('/app/shell.mjs',raw);self.assertNotIn('/manage/entry.mjs',raw)
    def test_static_html_has_unique_element_ids(self):
        for path,(_,media) in _FILES.items():
            if media != 'text/html':continue
            ids=re.findall(r'\bid="([^"]+)"',self.read(path)['raw'].decode())
            self.assertEqual(len(ids),len(set(ids)),path)
    def test_unknown_paths_queries_methods_and_legacy_entry_are_not_exposed(self):
        for path in ('/manage/entry.mjs','/responsibility/entry.mjs','/pipelines/entry.mjs','/app/../client.mjs','/app/%2e%2e/client.mjs','/unknown','/?x=y','/app/bootstrap?x=y'):
            self.assertEqual(self.read(path)['status'],404,path)
        self.assertEqual(self.write('/manage/')['status'],404)
    def test_static_and_bootstrap_keep_exact_transport_security(self):
        for path in ('/','/manage/','/app/bootstrap','/app/client.mjs'):
            for extra in ({'wsgi.url_scheme':'http'},{'HTTP_HOST':'other.invalid'},{'HTTP_ORIGIN':'https://other.invalid'},{'HTTP_SEC_FETCH_SITE':'same-site'},{'HTTP_SEC_FETCH_SITE':'cross-site'},{'HTTP_AUTHORIZATION':'Bearer not-used'},{'SCRIPT_NAME':'/prefix'}):
                self.assertEqual(self.read(path,extra=extra)['status'],401,(path,extra))
    def test_callback_navigation_exception_is_public_html_only(self):
        navigation={'HTTP_SEC_FETCH_SITE':'cross-site','HTTP_SEC_FETCH_MODE':'navigate','HTTP_SEC_FETCH_DEST':'document'}
        for path in ('/','/manage/','/responsibility/','/pipelines/','/community/','/assistant/'):
            self.assertEqual(self.read(path,extra=navigation)['status'],200)
            for changed in ({'HTTP_SEC_FETCH_DEST':'iframe'},{'HTTP_SEC_FETCH_MODE':'cors'},{'HTTP_HOST':'other.invalid'},{'wsgi.url_scheme':'http'},{'HTTP_AUTHORIZATION':'Bearer not-used'},{'HTTP_ORIGIN':'https://other.invalid'},{'SCRIPT_NAME':'/prefix'}):
                self.assertEqual(self.read(path,extra={**navigation,**changed})['status'],401)
        for path in ('/app/bootstrap','/app/client.mjs','/api/workspaces','/auth/session','/?code=not-allowed'):
            self.assertEqual(self.read(path,extra=navigation)['status'],401,path)
        self.assertEqual(self.read('/',method='HEAD',extra=navigation)['status'],401)
    def test_bootstrap_has_only_fixed_non_authorizing_configuration(self):
        data=self.read('/app/bootstrap',cookies='')['body']['data']
        self.assertEqual(data,{'service':'ms-collab','ui_modules':['workspaces','responsibility','pipelines','community','assistant','research-assets','artifacts'],'login_configured':True,'authorization_endpoint':fixtures.ISSUER+'/authorize'})
        self.assertNotIn('principal_id',data)
    def test_disabled_modules_and_provider_stay_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            host,_,login,_=fixtures.setup(d,enabled=False,modules=())
            app=ApplicationUI(host=host);data=request(app,'/app/bootstrap')['body']['data']
            self.assertEqual(data['ui_modules'],['workspaces']);self.assertEqual(data['login_configured'],False);self.assertIsNone(data['authorization_endpoint'])
            self.assertEqual(request(app,'/responsibility/')['status'],404)
            self.assertEqual(request(app,'/community/')['status'],404)
            self.assertEqual(request(app,'/assistant/')['status'],404)
            self.assertEqual(request(app,'/pipelines/')['status'],404)
            self.assertEqual(request(app,'/auth/start',method='POST',origin=fixtures.ORIGIN,body={},extra={'HTTP_X_MS_COLLAB_LOGIN':'1'})['status'],503)
            self.assertEqual(request(app,'/auth/session')['status'],401)
            self.assertEqual(request(app,'/api/workspaces/alpha/responsibility/source/sample/project/config')['status'],404)
            with sqlite3.connect(Path(d)/'login.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
    def test_api_passthrough_current_cookie_authority_and_no_claims(self):
        for path in ('/api/workspaces','/auth/session','/api/workspaces/alpha/responsibility/source/sample/project/capabilities'):
            self.assertEqual(self.read(path)['status'],200)
            self.assertEqual(self.read(path,cookies='',extra={'HTTP_X_USER':'admin'})['status'],401)
            self.assertEqual(self.read(path,extra={'HTTP_AUTHORIZATION':'Bearer not-used'})['status'],401)
    def test_csrf_and_cas_remain_required_for_responsibility_save(self):
        path='/api/workspaces/alpha/responsibility/source/sample/project/config'
        self.assertEqual(self.write(path,method='PUT',body={'fields':[]},csrf=None)['status'],401)
        self.assertEqual(self.write(path,method='PUT',body={'fields':[]},csrf='B'*43)['status'],401)
        self.assertEqual(self.write(path,method='PUT',body={'fields':[]})['status'],200)
        self.assertEqual(self.write(path,method='PUT',body={'fields':[]})['status'],412)
    def test_workspace_mutation_is_real_authority_and_one_scope_only(self):
        path='/api/workspaces/alpha/members/reader'
        self.assertEqual(self.write(path,method='PATCH',body={'role':'developer','state':'active'})['status'],200)
        self.assertEqual(self.write(path.replace('/alpha/','/beta/'),method='PATCH',body={'role':'developer','state':'active'})['status'],404)
    def test_logout_and_expiry_remove_all_module_access(self):
        self.assertEqual(self.write('/auth/logout')['status'],200)
        for path in ('/auth/session','/api/workspaces','/api/workspaces/alpha/responsibility/source/sample/project/config'):
            self.assertEqual(self.read(path)['status'],401)
        self.session=fixtures.session(self.login,self.exchange);self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value});self.now+=timedelta(days=2)
        self.assertEqual(self.read('/api/workspaces')['status'],401)
    def test_revoked_membership_denies_old_scope_without_affecting_public_shell(self):
        self.policy.set_membership('second','alpha','admin',expected_revision=1,role='viewer',state='revoked')
        self.assertEqual(self.read('/api/workspaces/alpha/responsibility/source/sample/project/config')['status'],404)
        self.assertEqual(self.read('/manage/')['status'],200)
        self.assertEqual(self.read('/api/workspaces')['body']['data'],[])

    def test_pipeline_cookie_reads_keep_scope_revision_and_current_revocation(self):
        base='/api/workspaces/alpha/pipelines/example-ci/build'
        for suffix in ('/runs?limit=100','/durations?limit=100','/runs/run-1'):
            response=self.read(base+suffix)
            self.assertEqual(response['status'],200)
            self.assertEqual(response['body']['data']['scope']['workspace_id'],'alpha')
            self.assertEqual(response['body']['data']['source']['revision'],1)
            self.assertEqual(self.read(base+suffix,cookies='')['status'],401)
            self.assertEqual(self.read(base+suffix,extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
            self.assertEqual(self.read((base+suffix).replace('/alpha/','/commons/'))['status'],404)
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        self.assertEqual(self.read(base+'/runs')['status'],404)
        self.assertEqual(self.read('/pipelines/')['status'],200)
