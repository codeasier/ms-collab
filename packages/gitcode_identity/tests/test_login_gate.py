"""Offline cross-layer canaries: documented wire success is not login authority.

No provider adapter, credentials, HTTP server or live network is configured.
These assertions preserve a closed gate, not proof of GitCode PKCE support.
"""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from packages.application_host import ApplicationHost, HostConfig
from packages.application_ui import ApplicationUI
from packages.gitcode_identity import GitCodeConfig, GitCodeOAuthClient
from packages.gitcode_identity.client import AUTHORIZE_URL
from packages.identity import AuthenticationUnavailable, Credential
from packages.login_state import LoginBinding, LoginCoordinator, SQLiteLoginStore
from packages.session_api import SessionPolicyStore, SessionWorkspaceApplication
from packages.session_api.application import _BROWSER, _SESSION
from packages.workspace_admin_api import initialize_administration
from packages.workspace_invitations import initialize_invitations
from packages.workspaces import Membership, Principal, SQLiteWorkspaceService, Workspace

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ORIGIN = "https://app.example.invalid"
BINDING = LoginBinding("https://gitcode.com", "synthetic-client", ORIGIN + "/auth/callback")


def request(app, path, *, method="GET", body=None, query="", extra=None):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {"wsgi.url_scheme": "https", "HTTP_HOST": "app.example.invalid",
               "SCRIPT_NAME": "", "REQUEST_METHOD": method, "PATH_INFO": path,
               "QUERY_STRING": query, "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
    if method == "POST":
        environ.update(HTTP_ORIGIN=ORIGIN, HTTP_X_MS_COLLAB_LOGIN="1")
    environ.update(extra or {})
    result = {}
    def start(status, headers):
        result.update(status=int(status.split()[0]), headers=headers)
    result["raw"] = b"".join(app(environ, start))
    result["body"] = json.loads(result["raw"])
    return result


class LoginGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        policy = SQLiteWorkspaceService.initialize(self.path / "policy.sqlite",
            principals=(Principal("admin", BINDING.issuer, "synthetic-user"),),
            workspaces=(Workspace("alpha", "Synthetic workspace"),),
            memberships=(Membership("alpha", "admin", "admin"),), modules=())
        initialize_administration(policy)
        initialize_invitations(policy)
        self.login = SQLiteLoginStore.initialize(self.path / "login.sqlite", binding=BINDING, clock=lambda: NOW)
        self.sessions = SessionPolicyStore(policy=policy, login=self.login)
        # Tripwires assert no attempted connection, even if application catches
        # the assertion and translates it to its ordinary unavailable response.
        self.sockets = self.enterContext(patch("socket.create_connection", side_effect=AssertionError("offline only")))
        self.https = self.enterContext(patch("packages.gitcode_identity.client.HTTPSConnection", side_effect=AssertionError("offline only")))

    def tearDown(self):
        self.sockets.assert_not_called()
        self.https.assert_not_called()

    def host(self, **kwargs):
        return ApplicationHost(config=HostConfig(ORIGIN), sessions=self.sessions, clock=lambda: NOW, **kwargs)

    def layers(self):
        return (SessionWorkspaceApplication(sessions=self.sessions, origin=ORIGIN),
                self.host(), ApplicationUI(host=self.host()))

    def counts(self):
        with sqlite3.connect(self.path / "login.sqlite") as db:
            return tuple(db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                         for table in ("attempts", "sessions"))

    def assert_closed(self, response, status=503):
        self.assertEqual(response["status"], status)
        self.assertEqual(response["body"], {"error": "authentication_unavailable"})
        headers = dict(response["headers"])
        self.assertNotIn("Set-Cookie", headers)
        self.assertNotIn("Location", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_coordinator_has_no_implicit_wire_adapter(self):
        coordinator = LoginCoordinator(self.login)
        with self.assertRaises(AuthenticationUnavailable):
            coordinator.begin()
        with self.assertRaises(AuthenticationUnavailable):
            coordinator.complete(parameters=(("state", "S" * 43), ("code", "synthetic-code")),
                browser_binding=Credential("B" * 43), callback_uri=BINDING.callback_uri)
        self.assertEqual(self.counts(), (0, 0))

    def test_wire_client_cannot_fill_any_bound_code_slot(self):
        client = GitCodeOAuthClient(GitCodeConfig(BINDING.audience, "synthetic-secret",
                                                  BINDING.callback_uri, ("example_user",)))
        self.assertFalse(hasattr(client, "exchange_bound_code"))
        self.assertFalse(hasattr(client, "verify"))
        with self.assertRaises(ValueError):
            LoginCoordinator(self.login, exchange=client)
        options = dict(exchange=client, authorization_endpoint=AUTHORIZE_URL,
                       authorization_scopes=("example_user",))
        with self.assertRaises(ValueError):
            SessionWorkspaceApplication(sessions=self.sessions, origin=ORIGIN, **options)
        with self.assertRaises(ValueError):
            self.host(**options)
        self.assertEqual(self.counts(), (0, 0))

    def test_partial_configuration_cannot_enable_login(self):
        for options in ({"authorization_endpoint": AUTHORIZE_URL},
                        {"authorization_scopes": ("example_user",)},
                        {"exchange": object()},
                        {"exchange": True, "authorization_endpoint": AUTHORIZE_URL,
                         "authorization_scopes": ("example_user",)}):
            with self.subTest(options=list(options)), self.assertRaises(ValueError):
                self.host(**options)
        self.assertEqual(self.counts(), (0, 0))

    def test_start_and_callback_are_closed_at_every_layer(self):
        for app in self.layers():
            with self.subTest(layer=type(app).__name__):
                self.assert_closed(request(app, "/auth/start", method="POST", body={}))
                self.assert_closed(request(app, "/auth/callback",
                    query=urlencode({"state": "S" * 43, "code": "synthetic-code"}),
                    extra={"HTTP_COOKIE": _BROWSER + "=" + "B" * 43,
                           "HTTP_SEC_FETCH_SITE": "cross-site"}))
        self.assertEqual(self.counts(), (0, 0))

    def test_preexisting_attempt_does_not_bypass_closed_provider(self):
        attempt = self.login.begin()  # Trusted core fixture, not an HTTP start.
        for app in self.layers():
            self.assert_closed(request(app, "/auth/callback",
                query=urlencode({"state": attempt.state.value, "code": "synthetic-code"}),
                extra={"HTTP_COOKIE": _BROWSER + "=" + attempt.browser_binding.value}))
        self.assertEqual(self.counts(), (1, 0))

    def test_environment_credentials_and_capability_flags_are_not_activation(self):
        # Synthetic values replace just these keys; no environment value is read
        # or printed by the test, and the host must not import credentials.
        with patch.dict(os.environ, {"GC_TOKEN": "synthetic-not-used",
                "GITCODE_TOKEN": "synthetic-not-used", "GITCODE_CLIENT_ID": "synthetic-client",
                "GITCODE_CLIENT_SECRET": "synthetic-not-used", "GITCODE_PKCE_SUPPORTED": "true"}):
            app = ApplicationUI(host=self.host())
            data = request(app, "/app/bootstrap")["body"]["data"]
            self.assertFalse(data["login_configured"])
            self.assertIsNone(data["authorization_endpoint"])
            self.assert_closed(request(app, "/auth/start", method="POST", body={}))
        self.assertEqual(self.counts(), (0, 0))

    def test_request_parameters_cannot_select_an_adapter_or_assert_pkce(self):
        for app in self.layers():
            for body in ({"pkce_supported": True}, {"exchange": "gitcode"},
                         {"authorization_endpoint": AUTHORIZE_URL},
                         {"code_verifier": "V" * 43}, {"principal_id": "admin"}):
                with self.subTest(layer=type(app).__name__, key=next(iter(body))):
                    response = request(app, "/auth/start", method="POST", body=body)
                    self.assertEqual(response["status"], 400)
                    self.assertNotIn("Set-Cookie", dict(response["headers"]))
            self.assert_closed(request(app, "/auth/callback", query=urlencode({
                "state": "S" * 43, "code": "synthetic-code", "pkce_supported": "true"})))
        self.assertEqual(self.counts(), (0, 0))

    def test_pat_claim_headers_and_forged_cookie_do_not_create_a_session(self):
        for app in self.layers():
            for extra in ({"HTTP_AUTHORIZATION": "Bearer synthetic-not-used"},
                          {"HTTP_X_USER": "admin", "HTTP_X_GITCODE_USER_ID": "synthetic-user"},
                          {"HTTP_COOKIE": _SESSION + "=" + "S" * 43}):
                for path in ("/auth/session", "/api/workspaces"):
                    with self.subTest(layer=type(app).__name__, path=path, headers=list(extra)):
                        response = request(app, path, extra=extra)
                        self.assertEqual(response["status"], 401)
                        self.assertNotIn(b"synthetic-user", response["raw"])
                        self.assertNotIn("Set-Cookie", dict(response["headers"]))
        self.assertEqual(self.counts(), (0, 0))

    def test_public_bootstrap_is_not_a_login_or_membership_assertion(self):
        data = request(ApplicationUI(host=self.host()), "/app/bootstrap")["body"]["data"]
        self.assertEqual(data, {"service": "ms-collab", "ui_modules": ["workspaces"],
                                "login_configured": False, "authorization_endpoint": None})
        self.assertEqual(self.counts(), (0, 0))

    def test_denial_does_not_echo_callback_material(self):
        markers = {"state": "S" * 43, "code": "synthetic-private-code", "error_description": "synthetic-private-error"}
        for app in self.layers():
            response = request(app, "/auth/callback", query=urlencode(markers))
            self.assert_closed(response)
            rendered = response["raw"] + repr(response["headers"]).encode()
            for marker in markers.values():
                self.assertNotIn(marker.encode(), rendered)
        self.assertEqual(self.counts(), (0, 0))
