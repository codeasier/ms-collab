"""Offline WSGI composition tests: no provider, runner, listener or real data."""
from datetime import timedelta
import io
import json
import tempfile
import unittest

from packages.application_host import ApplicationHost, HostConfig
from packages.gui_smoke import GuiSmokeApplication
from packages.gui_smoke_ui import GuiSmokeUI
from packages.gui_smoke_ui.tests.fixtures import setup, cookies, NOW, ORIGIN, BASE
from packages.session_api.tests.test_application import call
from packages.workspace_admin_api import ModuleTemplate


def static(app,path='/',**extra):
    env={'wsgi.url_scheme':'https','HTTP_HOST':'app.example.invalid','REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
    env.update(extra);result={}
    def start(status,headers): result.update(status=int(status.split()[0]),headers=dict(headers))
    result['body']=b''.join(app(env,start));return result


class UITests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.now=NOW
        self.app,self.policy,self.login,self.exchange,self.connections=setup(directory.name,clock=lambda:self.now)
        self.cookies,self.csrf=cookies(self.login,self.exchange)

    def read(self,suffix='/tasks',**kwargs):
        return call(self.app,BASE+suffix,cookies=self.cookies,**kwargs)

    def test_static_allowlist_transport_and_headers(self):
        for path in ('/','/gui-smoke/','/gui-smoke/app.mjs','/gui-smoke/client.mjs','/gui-smoke/style.css'):
            result=static(self.app,path);self.assertEqual(result['status'],200)
            self.assertEqual(result['headers']['Cache-Control'],'no-store')
            self.assertIn("connect-src 'self'",result['headers']['Content-Security-Policy'])
            self.assertNotIn('Access-Control-Allow-Origin',result['headers'])
            self.assertNotIn('Set-Cookie',result['headers'])
        for path in ('/gui-smoke/../application.py','/gui-smoke/tests/client.test.mjs','/foo','/favicon.ico'):
            self.assertEqual(static(self.app,path)['status'],404)
        self.assertEqual(static(self.app,QUERY_STRING='scope=alpha')['status'],404)
        self.assertEqual(static(self.app,REQUEST_METHOD='POST')['status'],401)
        for env in ({'wsgi.url_scheme':'http'},{'HTTP_HOST':'other.invalid'},{'HTTP_AUTHORIZATION':'Bearer synthetic'},{'HTTP_SEC_FETCH_SITE':'cross-site'},{'HTTP_ORIGIN':'https://other.invalid'},{'SCRIPT_NAME':'/prefix'}):
            self.assertEqual(static(self.app,**env)['status'],401)
        self.assertEqual(static(self.app,REQUEST_METHOD='HEAD')['body'],b'')

    def test_public_top_level_shell_can_receive_oauth_redirect_only(self):
        navigation={'HTTP_SEC_FETCH_SITE':'cross-site','HTTP_SEC_FETCH_MODE':'navigate','HTTP_SEC_FETCH_DEST':'document'}
        for path in ('/','/gui-smoke/'):
            result=static(self.app,path,**navigation)
            self.assertEqual(result['status'],200)
            self.assertNotIn('Set-Cookie',result['headers'])
            self.assertEqual(result['body'],static(self.app,path)['body'])
        for override in ({'HTTP_SEC_FETCH_DEST':'iframe'},{'HTTP_SEC_FETCH_MODE':'cors'},{'REQUEST_METHOD':'POST'},{'QUERY_STRING':'scope=alpha'},{'HTTP_AUTHORIZATION':'Bearer fake'},{'HTTP_HOST':'other.invalid'},{'wsgi.url_scheme':'http'},{'SCRIPT_NAME':'/prefix'}):
            self.assertEqual(static(self.app,**(navigation|override))['status'],401)
        self.assertEqual(static(self.app,'/gui-smoke/app.mjs',**navigation)['status'],401)
        self.assertEqual(self.read(extra=navigation)['status'],401)

    def test_shell_has_no_embedded_identity_scope_or_execution_control(self):
        body=static(self.app)['body'].decode()
        for private in ('alice-sub','alpha-branch',self.cookies,self.csrf,'synthetic-provider','type="password"','Authorization'):
            self.assertNotIn(private,body)
        for label in ('>Submit<','>Retry<','>Cancel<','>Run<','>Download<'):
            self.assertNotIn(label,body)
        self.assertIn('Saved reports, not a live queue',body)

    def test_host_uses_same_session_policy_and_current_gui_acl(self):
        self.assertEqual(self.read()['status'],200)
        self.assertEqual(call(self.app,BASE+'/tasks')['status'],401)
        self.assertEqual(self.read(extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
        for subject in ('bob-sub','writer-sub','admin-sub'):
            cookie,_=cookies(self.login,self.exchange,subject)
            self.assertEqual(call(self.app,BASE+'/tasks',cookies=cookie)['status'],404)
        self.assertEqual(call(self.app,BASE.replace('alpha','commons')+'/tasks',cookies=self.cookies)['status'],404)
        self.assertEqual(call(self.app,BASE.replace('alpha','beta')+'/tasks',cookies=self.cookies)['status'],200)

    def test_session_revoke_expiry_and_policy_revoke_fail_closed(self):
        self.login.revoke_subject('alice-sub')
        self.assertEqual(self.read()['status'],401)
        self.cookies,self.csrf=cookies(self.login,self.exchange)
        self.policy.set_membership('admin','alpha','alice',role='viewer',state='revoked',expected_revision=1)
        self.assertEqual(self.read()['status'],404)
        self.now += timedelta(hours=1)
        self.assertEqual(call(self.app,BASE.replace('alpha','beta')+'/tasks',cookies=self.cookies)['status'],401)

    def test_disabled_module_and_unknown_raw_routes_do_not_fallback(self):
        self.policy.set_module_state('admin','alpha','gui_smoke',state='disabling',expected_revision=1)
        self.assertIn(self.read()['status'],(404,409))
        for path in (BASE+'/refresh',BASE+'/queue',BASE+'/tasks/task-1/logs','/api/workspaces/alpha/modules/gui_smoke/resources/raw'):
            self.assertEqual(call(self.app,path,cookies=self.cookies)['status'],404)
        self.assertEqual(call(self.app,BASE+'/tasks',method='POST',cookies=self.cookies,csrf=self.csrf,origin=ORIGIN,body={})['status'],404)

    def test_login_closed_by_default_and_no_bearer_bridge(self):
        host=self.app._host
        closed=GuiSmokeUI(host=ApplicationHost(config=host._config,sessions=host._boundary._sessions))
        self.assertEqual(call(closed,'/auth/start',method='POST',origin=ORIGIN,body={},extra={'HTTP_X_MS_COLLAB_LOGIN':'1'})['status'],503)
        with self.assertRaises(ValueError):
            GuiSmokeApplication(session_boundary=host._boundary,store=self.policy,connections=self.connections)
        self.assertEqual(call(closed,'/auth/session',cookies=self.cookies)['status'],200)

    def test_configuration_must_be_explicit_and_routes_do_not_create_data(self):
        host=self.app._host
        for kwargs in ({'modules':(),'gui_smoke_connections':self.connections}, {'modules':('gui-smoke',),'gui_smoke_connections':list(self.connections)}, {'modules':('gui-smoke',),'gui_smoke_connections':self.connections*2}, {'modules':('gui-smoke',),'module_catalog':(ModuleTemplate('gui-smoke',('read',)),)}):
            with self.assertRaises(ValueError): HostConfig(ORIGIN,**kwargs)
        empty=ApplicationHost(config=HostConfig(ORIGIN,('gui-smoke',)),sessions=host._boundary._sessions)
        self.assertEqual(call(empty,BASE+'/tasks',cookies=self.cookies)['status'],404)
        with self.assertRaises(ValueError): GuiSmokeUI(host=object())
        self.assertEqual(call(self.app,BASE.replace('alpha','unpublished')+'/tasks',cookies=self.cookies)['body']['data']['available'],False)

    def test_fixed_revision_and_cross_site_callback_are_host_handled(self):
        self.assertEqual(self.read(query='offset=20')['status'],428)
        self.assertEqual(self.read(query='offset=20&revision=1')['status'],200)
        self.assertEqual(self.read('/tasks/task-1',query='revision=0')['status'],412)
        # A callback lacking a browser binding is rejected by auth, not static shell.
        result=call(self.app,'/auth/callback',query='state=synthetic&code=synthetic',extra={'HTTP_SEC_FETCH_SITE':'cross-site'})
        self.assertIn(result['status'],(400,401))
        self.assertEqual(call(self.app,'/auth/logout',method='POST',cookies=self.cookies,origin=ORIGIN,body={})['status'],401)
        self.assertEqual(call(self.app,'/auth/logout',method='POST',cookies=self.cookies,csrf=self.csrf,origin=ORIGIN,body={})['status'],200)
        self.assertEqual(self.read()['status'],401)

if __name__=='__main__': unittest.main()
