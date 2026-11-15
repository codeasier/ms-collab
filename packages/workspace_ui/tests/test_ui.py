"""Synthetic integration only, no production identity provider."""
import io
import unittest
from packages.workspace_ui import WorkspaceManagementUI
from packages.workspace_api.tests import test_application as fixtures


class UITests(unittest.TestCase):
    def setUp(self):
        fixtures.ApplicationTests.setUp(self)
        self.ui = WorkspaceManagementUI(api=self.app, origin="https://collab.example.invalid")

    def call_ui(self, path="/manage/", **extra):
        environ = {"PATH_INFO":path, "REQUEST_METHOD":"GET", "QUERY_STRING":"",
                   "HTTP_HOST":"collab.example.invalid", "wsgi.url_scheme":"https", "wsgi.input":io.BytesIO(b"")}
        environ.update(extra)
        result = {}
        def start(status, headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result["body"]=b"".join(self.ui(environ,start));return result

    def test_static_allowlist_and_security_headers(self):
        for path in ("/manage/","/manage/admin.mjs","/manage/client.mjs","/manage/style.css"):
            result=self.call_ui(path);self.assertEqual(result["status"],200)
            self.assertEqual(result["headers"]["Cache-Control"],"no-store")
            self.assertIn("frame-ancestors 'none'",result["headers"]["Content-Security-Policy"])
        for path in ("/manage/../README.md","/manage/%2e%2e/README.md","/manage/tests/test_ui.py"):
            self.assertEqual(self.call_ui(path)["status"],404)
        self.assertEqual(self.call_ui(QUERY_STRING="token=no")["status"],404)

    def test_explicit_origin_rejects_rebinding_and_cross_origin(self):
        for extra in ({"HTTP_HOST":"evil.invalid"},{"HTTP_ORIGIN":"https://evil.invalid"},
                      {"wsgi.url_scheme":"http"},{"HTTP_SEC_FETCH_SITE":"cross-site"}):
            self.assertEqual(self.call_ui(**extra)["status"],403)
            self.assertEqual(self.call_ui("/api/workspaces",**extra)["status"],403)

    def test_no_anonymous_api_or_static_credential(self):
        self.assertEqual(self.call_ui("/api/workspaces")["status"],401)
        self.assertEqual(self.call_ui("/api/workspaces",HTTP_AUTHORIZATION="Bearer test-admin")["status"],200)
        self.assertNotIn(b"test-admin",self.call_ui()["body"])

    def test_configuration_requires_known_origin_and_api(self):
        for origin in ("http://example.invalid","https://example.invalid/","https://u:p@example.invalid", "https://example.invalid?x", "http://localhost:80", "https://x.invalid\n"):
            with self.assertRaises(ValueError):WorkspaceManagementUI(api=self.app,origin=origin)
        with self.assertRaises(ValueError):WorkspaceManagementUI(api=None,origin="https://x.invalid")


if __name__ == "__main__":unittest.main()
