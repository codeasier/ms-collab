"""Cookie-only research asset page and existing current-policy API."""
import tempfile,unittest
from packages.application_host.tests.fixtures import setup,session,NOW
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.test_application import request
from packages.application_ui.tests.research_asset_fixtures import provision
from packages.session_api.application import _SESSION,_CSRF
from packages.session_api.tests.test_application import cookie_header

class AssetUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.host,self.policy,self.login,self.exchange=setup(self.temp.name,clock=lambda:NOW)
        provision(self.policy);self.app=ApplicationUI(host=self.host);self.session=session(self.login,self.exchange)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
    def read(self,path,**options):return request(self.app,path,cookies=self.cookies,**options)
    def test_credential_free_page_allowlist_and_closed_module(self):
        for path in ('/research-assets/','/research-assets/client.mjs','/research-assets/browser.mjs','/research-assets/style.css'):
            response=self.read(path);self.assertEqual(response['status'],200)
            self.assertIn(('Cache-Control','no-store'),response['headers'])
        raw=self.read('/research-assets/')['raw'].decode();self.assertNotIn('type="password"',raw);self.assertNotIn('id="credential"',raw)
        self.assertEqual(self.read('/research-assets/entry.mjs')['status'],404)
        with tempfile.TemporaryDirectory() as temp:
            host,*_=setup(temp,modules=());self.assertEqual(request(ApplicationUI(host=host),'/research-assets/')['status'],404)
    def test_exact_scope_current_cookie_and_revision_paging(self):
        base='/api/workspaces/alpha/research-assets/knowledge'
        types=self.read(base+'/types')['body']['data'];self.assertEqual(sum(t['count'] for t in types['items']),28)
        page=self.read(base+'/assets?page_size=20&page=2&revision=2')['body']['data'];self.assertEqual(len(page['items']),8)
        self.assertEqual(self.read(base+'/assets?revision=1')['status'],412)
        self.assertEqual(self.read(base+'/assets',extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
        self.assertEqual(self.read(base.replace('/alpha/','/commons/')+'/assets')['status'],404)
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        self.assertEqual(self.read(base+'/types')['status'],404)
    def test_unpublished_and_published_empty_are_distinct(self):
        for catalog,available,revision in (('empty',True,2),('unpublished',False,1)):
            data=self.read('/api/workspaces/alpha/research-assets/'+catalog+'/assets')['body']['data']
            self.assertEqual((data['available'],data['revision'],data['total']),(available,revision,0))
    def test_details_are_plain_projection_not_source_or_execution(self):
        base='/api/workspaces/alpha/research-assets/knowledge'
        rows=self.read(base+'/assets?type=runbook')['body']['data']['items'];detail=self.read(base+'/assets/'+rows[0]['id']+'?revision=2')['body']['data']
        self.assertFalse(detail['execution_verified']);self.assertEqual(detail['asset']['fields']['status'],'verified')
        self.assertEqual(self.read(base+'/sync')['status'],404)
        for part in ('repository','source_path','credential_ref'):self.assertNotIn(part,str(detail))
    def test_logout_and_provider_gate_not_bypassed(self):
        self.login.logout(self.session.token,self.session.csrf)
        self.assertEqual(self.read('/api/workspaces/alpha/research-assets/knowledge/types')['status'],401)
        self.assertEqual(self.read('/research-assets/')['status'],200)

    def test_published_directory_through_real_host_mount(self):
        directory='/api/workspaces/alpha/research-assets'
        result=self.read(directory)
        self.assertEqual(result['status'],200)
        self.assertEqual(result['body'],{'data':{'workspace_id':'alpha','items':['empty','knowledge']}})
        self.assertIn(('Cache-Control','no-store'),result['headers'])
        self.assertEqual(self.read(directory.replace('/alpha/','/commons/'))['status'],404)
        self.assertEqual(self.read(directory,extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        self.assertEqual(self.read(directory)['status'],404)

    def test_paired_browser_data_has_the_same_catalog_id_with_distinct_publications(self):
        provision(self.policy,paired=True)
        root='/api/workspaces'
        for workspace,ids,total in (('alpha',['empty','knowledge'],28),('beta',['beta-only','knowledge'],1)):
            directory=f'{root}/{workspace}/research-assets'
            self.assertEqual(self.read(directory)['body']['data']['items'],ids)
            view=self.read(directory+'/knowledge/assets?revision=2')['body']['data']
            self.assertEqual(view['scope'],{'workspace_id':workspace,'catalog_id':'knowledge'})
            self.assertEqual(view['total'],total)
        self.assertEqual(self.read(root+'/commons/research-assets')['body'],{'error':'not_found'})
