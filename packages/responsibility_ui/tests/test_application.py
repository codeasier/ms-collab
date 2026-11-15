"""Synthetic exact-origin composition, real responsibility authorization."""
import io
import json
import unittest
from packages.responsibility_api.tests import test_application as fixtures
BASE=fixtures.BASE
from packages.responsibility_ui import ResponsibilityUI

class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ResponsibilityTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.ui=ResponsibilityUI(api=self.fixture.app,origin='https://collab.example.invalid')

    def call(self,path='/responsibility/',*,method='GET',extra=None):
        env={'PATH_INFO':path,'REQUEST_METHOD':method,'QUERY_STRING':'','wsgi.input':io.BytesIO(b''),
             'HTTP_HOST':'collab.example.invalid','wsgi.url_scheme':'https'};env.update(extra or {});result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result['body']=b''.join(self.ui(env,start));return result

    def test_static_allowlist_and_security_headers(self):
        for name in ('','style.css','browser.mjs','client.mjs'):
            r=self.call('/responsibility/'+name);self.assertEqual(r['status'],200)
            self.assertEqual(r['headers']['Cache-Control'],'no-store');self.assertEqual(r['headers']['Referrer-Policy'],'no-referrer')
            self.assertIn("frame-ancestors 'none'",r['headers']['Content-Security-Policy']);self.assertIn("connect-src 'self'",r['headers']['Content-Security-Policy'])
        for path in ('/','/responsibility/../client.mjs','/responsibility/tests/browser.cjs','/responsibility/index.html'):
            self.assertEqual(self.call(path)['status'],404)
        self.assertEqual(self.call(method='POST')['status'],404)
        self.assertEqual(self.call(extra={'QUERY_STRING':'token=not-a-credential'})['status'],404)
        self.assertEqual(self.call(method='HEAD')['body'],b'')

    def test_no_host_origin_cross_site_or_scheme_substitution(self):
        for extra in ({'HTTP_HOST':'other.invalid'},{'HTTP_ORIGIN':'https://other.invalid'},
                      {'wsgi.url_scheme':'http'},{'HTTP_SEC_FETCH_SITE':'cross-site'}):
            self.assertEqual(self.call(extra=extra)['status'],403)
            self.assertEqual(self.call(BASE+'/config',extra=extra)['status'],403)

    def test_api_uses_real_configured_identity_not_cookies_or_claims(self):
        self.assertEqual(self.call(BASE+'/config',extra={'HTTP_COOKIE':'user=admin','REMOTE_USER':'admin'})['status'],401)
        r=self.call(BASE+'/capabilities',extra={'HTTP_AUTHORIZATION':'Bearer test-reader'});self.assertEqual(r['status'],200)
        self.assertEqual(json.loads(r['body'])['data']['capabilities'],{'read':True,'edit':False})
        r=self.call(BASE+'/capabilities',extra={'HTTP_AUTHORIZATION':'Bearer test-admin'});self.assertEqual(json.loads(r['body'])['data']['capabilities']['edit'],True)

    def test_factory_has_no_default_api_identity_or_insecure_origin(self):
        for origin in ('http://example.invalid','https://user:pass@example.invalid','https://example.invalid/path','https://example.invalid?x=1','https://example.invalid#x','https://example.invalid\n',None):
            with self.assertRaises(ValueError):ResponsibilityUI(api=self.fixture.app,origin=origin)
        with self.assertRaises(ValueError):ResponsibilityUI(api=object(),origin='https://example.invalid')
        ResponsibilityUI(api=self.fixture.app,origin='http://127.0.0.1:1234')
