"""Synthetic same-origin wrapper and authorization integration checks."""
import io,json,tempfile,unittest
from pathlib import Path
from packages.artifacts_ui import ArtifactBrowserUI
from packages.artifacts_ui.tests.browser_server import configured_api
class Tests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.api=configured_api(Path(directory.name)/"test.sqlite3")
        self.ui=ArtifactBrowserUI(api=self.api,origin="https://collab.example.invalid")
    def call(self,path="/artifacts/",**values):
        environ={"PATH_INFO":path,"REQUEST_METHOD":"GET","QUERY_STRING":"","HTTP_HOST":"collab.example.invalid","wsgi.url_scheme":"https","wsgi.input":io.BytesIO()};environ.update(values)
        result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result['body']=b''.join(self.ui(environ,start));return result
    def test_factory_requires_configured_api_and_fixed_origin(self):
        with self.assertRaises(ValueError):ArtifactBrowserUI(api=None,origin="https://collab.example.invalid")
        for origin in (None,"http://localhost:8000","http://external.example.invalid","https://user@host.invalid","https://host.invalid/path","https://host.invalid?q=a","https://host.invalid#x"," https://host.invalid"):
            with self.assertRaises(ValueError):ArtifactBrowserUI(api=self.api,origin=origin)
        ArtifactBrowserUI(api=self.api,origin="http://127.0.0.1:8000")
    def test_assets_and_csp_no_secrets_or_cookie(self):
        for suffix in ("","browser.mjs","client.mjs","style.css"):
            result=self.call('/artifacts/'+suffix);self.assertEqual(result['status'],200)
            self.assertEqual(result['headers']['Cache-Control'],'no-store')
            self.assertIn("frame-ancestors 'none'",result['headers']['Content-Security-Policy'])
            self.assertNotIn('Set-Cookie',result['headers'])
            self.assertNotIn(b'synthetic-reader',result['body'])
    def test_host_scheme_origin_and_cross_site_fail(self):
        for values in ({'HTTP_HOST':'evil.invalid'},{'wsgi.url_scheme':'http'},{'HTTP_ORIGIN':'https://evil.invalid'},{'HTTP_SEC_FETCH_SITE':'cross-site'}):
            for path in ('/artifacts/','/api/workspaces/alpha/artifacts/example-storage/releases/quarters'):
                self.assertEqual(self.call(path,**values)['status'],403)
    def test_exact_asset_allowlist(self):
        for path in ('/artifacts/../LICENSE','/artifacts','/artifacts/tests/browser.cjs','/manage/','/artifacts/index.html'):
            self.assertEqual(self.call(path)['status'],404)
        self.assertEqual(self.call(QUERY_STRING='credential=never')['status'],404)
        self.assertEqual(self.call(REQUEST_METHOD='POST')['status'],404)
        self.assertEqual(self.call(REQUEST_METHOD='HEAD')['body'],b'')
    def test_api_authentication_and_scope_still_authoritative(self):
        path='/api/workspaces/alpha/artifacts/example-storage/releases/quarters'
        self.assertEqual(self.call(path)['status'],401)
        result=self.call(path,HTTP_AUTHORIZATION='Bearer synthetic-reader');self.assertEqual(result['status'],200)
        self.assertEqual(json.loads(result['body'])['data']['scope']['workspace_id'],'alpha')
        self.assertEqual(self.call(path.replace('alpha','hidden'),HTTP_AUTHORIZATION='Bearer synthetic-reader')['status'],404)
        self.assertEqual(self.call(path,HTTP_COOKIE='user=reader',HTTP_X_USER_ID='reader')['status'],401)
    def test_no_mutation_or_inherited_management_route(self):
        for path in ('/api/workspaces','/api/workspaces/alpha/members'):
            self.assertEqual(self.call(path,HTTP_AUTHORIZATION='Bearer synthetic-reader')['status'],404)
        self.assertEqual(self.call('/api/workspaces/alpha/artifacts/example-storage/releases/query',REQUEST_METHOD='DELETE',HTTP_AUTHORIZATION='Bearer synthetic-reader')['status'],404)
if __name__=='__main__':unittest.main()
