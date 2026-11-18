"""Cross-module synthetic sessions, SQLite policies and HTTP security composition."""
from datetime import timedelta
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit,urlencode

from packages.application_host import ApplicationHost,HostConfig
from packages.application_host.tests.fixtures import setup,session,READS,MODULES,ORIGIN,NOW
from packages.session_api.application import _SESSION,_CSRF
from packages.session_api.tests.test_application import call,cookie_header,cookie_values
from packages.workspace_api import WorkspaceApplication
from packages.workspace_admin_api import ModuleTemplate


class HostTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.now=NOW
        self.app,self.policy,self.login,self.exchange=setup(self.directory.name,clock=lambda:self.now)
        self.session=session(self.login,self.exchange)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})

    def request(self,path,**kwargs):
        return call(self.app,path,cookies=kwargs.pop('cookies',self.cookies),**kwargs)

    def mutation(self,path,**kwargs):
        values=dict(method='POST',origin=ORIGIN,csrf=self.session.csrf.value,body={},revision='"1"')
        values.update(kwargs);return self.request(path,**values)

    def test_all_six_modules_use_one_real_sqlite_session_boundary(self):
        for path in READS:
            with self.subTest(path=path):
                response=self.request(path);self.assertEqual(response['status'],200,response)
                headers=dict(response['headers']);self.assertEqual(headers['Vary'],'Cookie, Origin')
                self.assertEqual(headers['Cache-Control'],'no-store');self.assertNotIn('WWW-Authenticate',headers)
        self.assertEqual(self.request('/api/workspaces')['body']['data'][0]['id'],'alpha')
        self.assertEqual(self.request('/api/control/capabilities')['body']['data'],{'manage_workspaces':True})
        self.assertEqual(self.request('/api/invitations')['body']['data'],[])

    def test_missing_cookies_never_fall_back_to_synthetic_or_bearer_identity(self):
        for path in READS:
            for extra in ({},{'HTTP_AUTHORIZATION':'Bearer synthetic-admin'},{'HTTP_X_USER':'admin'}):
                with self.subTest(path=path,extra=extra):
                    self.assertEqual(self.request(path,cookies='',extra=extra)['status'],401)

    def test_bearer_and_cookie_mixing_is_rejected_on_every_module(self):
        for path in READS:
            self.assertEqual(self.request(path,extra={'HTTP_AUTHORIZATION':'Bearer anything'})['status'],401)

    def test_cross_workspace_and_shared_workspace_are_not_public(self):
        for path in READS:
            for workspace in ('beta','commons'):
                self.assertIn(self.request(path.replace('/alpha/','/'+workspace+'/'))['status'],(403,404))

    def test_module_specific_approval_and_history_owner_fences_remain(self):
        data=self.request(READS[0])['body']['data']
        self.assertEqual([row['number'] for row in data['items']],[1])
        other=session(self.login,self.exchange,'second-sub')
        cookies=cookie_header({_SESSION:other.token.value,_CSRF:other.csrf.value})
        self.assertEqual(self.request(READS[-1],cookies=cookies)['status'],404)
        self.assertEqual(self.request(READS[0],cookies=cookies)['status'],404)

    def test_csrf_is_required_for_read_only_post_query_too(self):
        path='/api/workspaces/alpha/artifacts/source/bucket/query'
        body={'quarter':'2026Q1','definition':{'files_only':True}}
        self.assertEqual(self.mutation(path,body=body,csrf=None)['status'],401)
        self.assertEqual(self.mutation(path,body=body)['status'],200)

    def test_every_mounted_mutation_has_same_csrf_boundary(self):
        mutations=(
          (READS[0].replace('/items','/approve'),'POST',{'fingerprints':[]}),
          (READS[-2],'PUT',{'fields':[]}),
          ('/api/workspaces/alpha/assistant/conversations/one','PATCH',{'state':'archived'}),
          ('/api/workspaces/alpha/members/reader','PATCH',{'role':'viewer','state':'revoked'}),
          ('/api/workspaces/alpha/invitations','POST',{'principal_id':'outsider','role':'viewer'}),
        )
        for path,method,body in mutations:
            with self.subTest(path=path):
                self.assertEqual(self.mutation(path,method=method,body=body,csrf=None)['status'],401)
                self.assertEqual(self.mutation(path,method=method,body=body,origin='https://other.example.invalid')['status'],401)

    def test_authorized_module_write_is_persisted_and_cas_kept(self):
        path='/api/workspaces/alpha/assistant/conversations/one'
        self.assertEqual(self.mutation(path,method='PATCH',body={'state':'archived'},revision='"2"')['status'],200)
        self.assertEqual(self.mutation(path,method='PATCH',body={'state':'active'},revision='"2"')['status'],412)
        self.assertEqual(self.request(path)['body']['data']['state'],'archived')

    def test_logout_revokes_all_modules_and_workspace_apis(self):
        self.assertEqual(self.mutation('/auth/logout')['status'],200)
        for path in (*READS,'/api/workspaces','/api/invitations','/api/control/capabilities'):
            self.assertEqual(self.request(path)['status'],401)

    def test_current_membership_revocation_applies_to_every_module(self):
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        for path in READS:self.assertIn(self.request(path)['status'],(403,404))

    def test_expired_session_fails_every_module(self):
        self.now=self.session.expires_at
        for path in READS:self.assertEqual(self.request(path)['status'],401)

    def test_session_expiry_during_pipeline_projection_is_not_authority_cache(self):
        self.app._modules['pipelines']._clock=lambda:self.expire()
        self.assertEqual(self.request(READS[1])['status'],401)

    def expire(self):
        self.now=self.session.expires_at;return self.now

    def test_host_module_allowlist_cannot_be_bypassed_by_raw_resources(self):
        app=ApplicationHost(config=HostConfig(ORIGIN),sessions=self.app._boundary._sessions)
        for path in READS:self.assertEqual(call(app,path,cookies=self.cookies)['status'],404)
        for path in ('/api/workspaces/alpha/modules/pipelines/resources/raw',
                     '/api/workspaces/alpha/modules/artifacts/resources/raw',
                     '/api/workspaces/alpha/modules/assistant/resources/raw',
                     '/api/workspaces/alpha/artifact-admin/source/bucket/status',
                     '/api/workspaces/alpha/artifact-download/source/bucket'):
            self.assertEqual(self.request(path)['status'],404)

    def test_default_provider_closed_without_side_effects(self):
        app=ApplicationHost(config=HostConfig(ORIGIN),sessions=self.app._boundary._sessions)
        with sqlite3.connect(self.login._path) as db:before=db.execute('SELECT count(*) FROM attempts').fetchone()
        result=call(app,'/auth/start',method='POST',body={},origin=ORIGIN,extra={'HTTP_X_MS_COLLAB_LOGIN':'1'})
        self.assertEqual(result['status'],503);self.assertFalse(cookie_values(result))
        self.assertEqual(call(app,'/auth/callback',query='code=anything&state=anything')['status'],503)
        with sqlite3.connect(self.login._path) as db:self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone(),before)

    def test_network_refresh_is_not_accidentally_enabled(self):
        path=READS[0].replace('/items','/refresh')
        with patch('packages.community_sync.GitCodeProvider.observe',side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.mutation(path)['status'],503)
        caps=self.request(READS[0].replace('/items','/capabilities'))['body']['data']['capabilities']
        self.assertFalse(caps['refresh'])

    def test_all_modules_enforce_https_exact_host_origin(self):
        for path in READS:
            for extra in ({'wsgi.url_scheme':'http'},{'HTTP_HOST':'other.example.invalid'},
                          {'HTTP_ORIGIN':'https://other.example.invalid'},{'HTTP_SEC_FETCH_SITE':'cross-site'}):
                self.assertEqual(self.request(path,extra=extra)['status'],401)

    def test_unknown_routes_and_legacy_demo_never_fall_through(self):
        for path in ('/api/demo','/demo','/api/workspaces/alpha/unknown/items','/api/workspaces/alpha/community//source/items','/api/workspaces/alpha/pipelines/../runs'):
            self.assertEqual(self.request(path)['status'],404)

    def test_configuration_is_explicit_and_exclusive(self):
        for modules in (['community'],('community','community'),('unknown',),('responsibility',)):
            with self.assertRaises(ValueError):HostConfig(ORIGIN,modules)
        with self.assertRaises(ValueError):HostConfig(ORIGIN,('community',),(ModuleTemplate('artifacts'),))
        for args in ({'store':self.policy},{'providers':()},{'provider_key':'any'}):
            with self.assertRaises(ValueError):WorkspaceApplication(session_boundary=self.app._boundary,**args)
        with self.assertRaises(ValueError):WorkspaceApplication(session_boundary=object())
        with self.assertRaises(ValueError):ApplicationHost(config={},sessions=self.app._boundary._sessions)

    def test_missing_module_schema_fails_closed_without_creation(self):
        with sqlite3.connect(self.policy._path) as db:db.execute('DROP TABLE assistant_events')
        self.assertEqual(self.request(READS[-1])['status'],503)
        with sqlite3.connect(self.policy._path) as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_schema WHERE name='assistant_events'").fetchone())

    def test_invitation_acceptance_uses_recipient_session_not_body_identity(self):
        proof=self.request('/api/workspaces/alpha/invitations/selections/outsider/viewer')
        self.assertEqual(proof['status'],200,proof)
        created=self.mutation('/api/workspaces/alpha/invitations',body={'principal_id':'outsider','role':'viewer'},
                              extra={'HTTP_IDEMPOTENCY_KEY':'11111111-1111-4111-8111-111111111111',
                                     'HTTP_IF_INVITATION_RECIPIENT_MATCH':proof['body']['data']['precondition']})
        self.assertEqual(created['status'],200,created)
        ident=created['body']['data']['id'];other=session(self.login,self.exchange,'outsider-sub')
        cookies=cookie_header({_SESSION:other.token.value,_CSRF:other.csrf.value})
        result=self.mutation('/api/invitations/'+ident,method='PATCH',body={'state':'accepted'},cookies=cookies,csrf=other.csrf.value)
        self.assertEqual(result['status'],200,result)
        self.assertEqual(self.policy.list_memberships('admin','alpha')[-1].workspace_id,'alpha')
        self.assertEqual(self.request(READS[0],cookies=cookies)['status'],404)  # Membership alone does not grant explicit reader scope.

    def test_http_login_dispatch_stays_with_session_boundary(self):
        result=call(self.app,'/auth/start',method='POST',body={},origin=ORIGIN,extra={'HTTP_X_MS_COLLAB_LOGIN':'1'})
        params=parse_qs(urlsplit(result['body']['data']['authorization_url']).query)
        code=self.exchange.authorize(params['code_challenge'][0])
        callback=call(self.app,'/auth/callback',query=urlencode({'state':params['state'][0],'code':code}),
                      cookies=cookie_header(cookie_values(result)),extra={'HTTP_SEC_FETCH_SITE':'cross-site'})
        self.assertEqual(callback['status'],303)
        self.assertEqual(self.request(READS[1],cookies=cookie_header(cookie_values(callback)))['status'],200)

    def test_storage_failure_is_not_anonymous_access(self):
        with patch.object(self.login,'_now',side_effect=RuntimeError('synthetic outage')):
            for path in READS:
                response=self.request(path);self.assertEqual(response['status'],503)
                self.assertFalse(cookie_values(response));self.assertNotIn('synthetic outage',str(response))

if __name__=='__main__':unittest.main()
