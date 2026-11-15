"""Real cookie authority, static navigation and read-only GUI composition."""
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urljoin
from packages.application_ui import ApplicationUI
from packages.application_ui.application import _FILES
from packages.application_host.application import _MODULES
from packages.application_ui.tests.test_application import request
from packages.gui_smoke_ui.tests.fixtures import setup,cookies,BASE

class GuiEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        legacy,self.policy,self.login,self.exchange,_=setup(self.temp.name)
        self.app=ApplicationUI(host=legacy._host)
        self.cookies,self.csrf=cookies(self.login,self.exchange)
    def read(self,path,**kwargs):return request(self.app,path,cookies=kwargs.pop('cookies',self.cookies),**kwargs)
    def test_gui_bootstrap_navigation_and_legacy_auth_entry_not_mounted(self):
        self.assertEqual(self.read('/app/bootstrap')['body']['data']['ui_modules'],['workspaces','gui-smoke'])
        html=self.read('/gui-smoke/',cookies='')['raw'].decode()
        self.assertIn('data-page="gui-smoke"',html);self.assertIn('/app/shell.mjs',html)
        self.assertNotIn('id="sign-in"',html);self.assertNotIn('type="password"',html)
        self.assertEqual(self.read('/gui-smoke/app.mjs')['status'],404)
        self.assertEqual(self.read('/gui-smoke/?scope=alpha')['status'],404)
        self.assertEqual(self.read('/gui-smoke/',extra={'HTTP_SEC_FETCH_SITE':'cross-site','HTTP_SEC_FETCH_MODE':'navigate','HTTP_SEC_FETCH_DEST':'document'})['status'],200)
        for path in ('/gui-smoke/view.mjs','/app/gui-smoke-session.mjs','/app/bootstrap'):
            self.assertEqual(self.read(path,extra={'HTTP_SEC_FETCH_SITE':'cross-site','HTTP_SEC_FETCH_MODE':'navigate','HTTP_SEC_FETCH_DEST':'document'})['status'],401)
    def test_every_supported_module_has_exact_entry_and_identical_navigation(self):
        root=Path(__file__).resolve().parents[3]/'frontend'
        expected={'/','/manage/','/artifact-admin/'}|{'/'+name+'/' for name in _MODULES}
        self.assertEqual({path for path,(_,media) in _FILES.items() if media=='text/html'},expected)
        navigation=None
        for path,(filename,media) in _FILES.items():
            if media!='text/html':continue
            html=(root/filename).read_text();nav=re.search(r'<nav aria-label="Application">(.*?)</nav>',html).group(1)
            links=re.findall(r'href="([^"]+)"',nav)
            self.assertEqual(set(links),expected,path);self.assertEqual(len(links),len(expected),path)
            self.assertEqual(len(re.findall(r'\bid="([^"]+)"',html)),len(set(re.findall(r'\bid="([^"]+)"',html))),path)
            if navigation is None:navigation=nav
            self.assertEqual(nav,navigation,path)
    def test_current_cookie_read_scope_and_snapshot_binding(self):
        for suffix in ('/tasks?offset=0&limit=20','/summary?revision=1','/tasks/task-1?revision=1'):
            response=self.read(BASE+suffix);self.assertEqual(response['status'],200)
            self.assertEqual(response['body']['data']['source']['revision'],1)
            self.assertEqual(self.read(BASE+suffix,cookies='')['status'],401)
            self.assertEqual(self.read(BASE+suffix,extra={'HTTP_AUTHORIZATION':'Bearer rejected'})['status'],401)
            self.assertEqual(self.read((BASE+suffix).replace('/alpha/','/commons/'))['status'],404)
        self.assertEqual(self.read(BASE+'/tasks/task-1?revision=2')['status'],412)
    def test_revocation_and_real_logout_apply_without_static_authority(self):
        self.policy.set_membership('admin','alpha','alice',role='viewer',state='revoked',expected_revision=1)
        self.assertEqual(self.read(BASE+'/tasks')['status'],404)
        self.assertEqual(self.read('/gui-smoke/')['status'],200)
        self.assertEqual(self.read('/auth/logout',method='POST',origin='https://app.example.invalid',csrf=self.csrf,body={})['status'],200)
        self.assertEqual(self.read(BASE+'/tasks')['status'],401)
    def test_no_provider_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy,*_=setup(temp,enabled=False);app=ApplicationUI(host=legacy._host)
            self.assertFalse(request(app,'/app/bootstrap')['body']['data']['login_configured'])
            self.assertEqual(request(app,'/auth/start',method='POST',body={},origin='https://app.example.invalid',extra={'HTTP_X_MS_COLLAB_LOGIN':'1'})['status'],503)
            self.assertEqual(request(app,BASE+'/tasks')['status'],401)
