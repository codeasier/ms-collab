"""Synthetic composition and same-origin/static delivery contract."""
import io
import tempfile
import unittest
from pathlib import Path
from packages.community_ui import CommunityUI
from packages.community_ui.tests.browser_server import make_api


class UITests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.api=make_api(Path(directory.name)/"test.sqlite3")
        self.ui=CommunityUI(api=self.api,origin="https://collab.example.invalid")

    def call(self,path="/community/",**extra):
        environ={"PATH_INFO":path,"REQUEST_METHOD":"GET","QUERY_STRING":"","HTTP_HOST":"collab.example.invalid","wsgi.url_scheme":"https","wsgi.input":io.BytesIO(b"")}
        environ.update(extra);result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result["body"]=b"".join(self.ui(environ,start));return result

    def test_static_allowlist_security_and_head(self):
        for path in ("/community/","/community/board.mjs","/community/client.mjs","/community/style.css"):
            result=self.call(path);self.assertEqual(result["status"],200)
            self.assertEqual(result["headers"]["Cache-Control"],"no-store")
            self.assertIn("frame-ancestors 'none'",result["headers"]["Content-Security-Policy"])
            self.assertIn("connect-src 'self'",result["headers"]["Content-Security-Policy"])
            self.assertEqual(self.call(path,REQUEST_METHOD="HEAD")["body"],b"")
        for path in ("/community/../README.md","/community/tests/browser.cjs","/manage/","/"):
            self.assertEqual(self.call(path)["status"],404)
        self.assertEqual(self.call(QUERY_STRING="token=x")["status"],404)

    def test_rebinding_cross_origin_and_non_tls_rejected(self):
        for extra in ({"HTTP_HOST":"evil.invalid"},{"HTTP_ORIGIN":"https://evil.invalid"},{"wsgi.url_scheme":"http"},{"HTTP_SEC_FETCH_SITE":"cross-site"}):
            self.assertEqual(self.call(**extra)["status"],403)
            self.assertEqual(self.call("/api/workspaces/alpha/community/source/sample/project/items",**extra)["status"],403)

    def test_no_anonymous_api_or_static_credentials(self):
        path="/api/workspaces/alpha/community/source/sample/project/capabilities"
        self.assertEqual(self.call(path)["status"],401)
        self.assertEqual(self.call(path,HTTP_AUTHORIZATION="Bearer synthetic-reader")["status"],200)
        self.assertNotIn(b"synthetic-reader",self.call()["body"])

    def test_explicit_api_and_origin_configuration(self):
        for origin in ("http://example.invalid","https://x.invalid/","https://u:p@x.invalid","http://localhost:8000","https://x.invalid\n"):
            with self.assertRaises(ValueError):CommunityUI(api=self.api,origin=origin)
        with self.assertRaises(ValueError):CommunityUI(api=None,origin="https://x.invalid")


if __name__ == "__main__":unittest.main()
