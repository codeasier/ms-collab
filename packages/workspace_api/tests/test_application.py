"""Synthetic configured verifier and in-process WSGI/SQLite integration only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, Thread
from packages.workspaces.tests.identity_fixture import simulate_identity
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.workspaces import Membership, ModuleInstance, Principal, Resource, SQLiteWorkspaceService, Workspace
from packages.workspace_api import WorkspaceApplication

ISSUER = "https://identity.example.invalid"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
BASE = "/api/workspaces/alpha/modules/board/resources"


class SyntheticVerifier:
    """Test-only opaque credential lookup, NOT a deployable auth adapter."""
    def __init__(self):
        self.hook = lambda: None
        self.calls = []

    def verify(self, credential):
        if type(credential) is not Credential:
            raise AuthenticationError()
        self.calls.append(credential)
        self.hook()
        identities = {"test-admin": "admin-sub", "test-viewer": "viewer-sub",
                      "test-other": "other-sub", "test-disabled": "disabled-sub",
                      "test-outsider": "outsider-sub"}
        subject = identities.get(credential.value)
        if subject is None:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, subject, "test-api", NOW, NOW + timedelta(minutes=1))


def initialize(path):
    return SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p, ISSUER, f"{p}-sub", p != "disabled")
                         for p in ("admin", "viewer", "other", "disabled", "outsider")),
        workspaces=(Workspace("alpha", "Alpha"), Workspace("beta", "Beta"),
                    Workspace("commons", "Commons", "shared")),
        memberships=(Membership("alpha", "admin", "admin"), Membership("alpha", "viewer", "viewer"),
                     Membership("beta", "other", "admin"), Membership("commons", "other", "admin"),
                     Membership("commons", "viewer", "viewer")),
        modules=tuple(ModuleInstance(w, "board", ("read", "write")) for w in ("alpha", "beta", "commons")),
        resources=(Resource("alpha", "board", "same", "Alpha"), Resource("beta", "board", "same", "Beta"),
                   Resource("commons", "board", "shared", "Explicit shared content")), operators=frozenset({"admin"}))


def call(app, path=BASE + "/same", *, method="GET", token="test-admin", body=None,
         revision=None, query="", extra=None, raw=None):
    data = json.dumps(body).encode() if body is not None else b""
    if raw is not None:
        data = raw
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(data)),
               "wsgi.input": io.BytesIO(data)}
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    if revision is not None:
        environ["HTTP_IF_MATCH"] = revision
    if extra:
        environ.update(extra)
    result = {}
    def start(status, headers):
        result["status"] = int(status.split()[0])
        result["headers"] = dict(headers)
    payload = b"".join(app(environ, start))
    result["body"] = json.loads(payload)
    assert int(result["headers"]["Content-Length"]) == len(payload)
    return result


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "policy.sqlite3"
        self.store = initialize(self.path)
        self.verifier = SyntheticVerifier()
        self.now = NOW
        self.provider = Provider("test", ISSUER, "test-api", self.verifier)
        self.app = WorkspaceApplication(store=self.store, providers=(self.provider,),
                                        provider_key="test", clock=lambda: self.now)

    def request(self, *args, **kwargs):
        return call(self.app, *args, **kwargs)

    def error(self, status, code, **kwargs):
        result = self.request(**kwargs)
        self.assertEqual(result["status"], status, result)
        self.assertEqual(result["body"], {"error": code})
        return result

    def test_real_loopback_http_with_test_only_verifier(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server("127.0.0.1", 0, self.app, handler_class=Quiet)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("PUT", BASE + "/same", body='{"text":"HTTP synthetic"}',
                headers={"Authorization": "Bearer test-admin", "Content-Type": "application/json", "If-Match": '"1"'})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["data"]["revision"], 2)
            connection.request("GET", BASE + "/same", headers={"Cookie": "user=admin", "X-User-Id": "admin"})
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            self.assertEqual(json.loads(response.read()), {"error": "authentication_required"})
        finally:
            connection.close()
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()

    def test_factory_requires_explicit_store_and_configured_provider(self):
        for store, providers, key in ((None, (self.provider,), "test"), (self.store, (), "test"),
                                       (self.store, (self.provider,), "missing")):
            with self.assertRaises(ValueError):
                WorkspaceApplication(store=store, providers=providers, provider_key=key)

    def test_no_credential_no_cookie_or_client_identity_fallback(self):
        self.error(401, "authentication_required", token=None, extra={"HTTP_COOKIE": "user=admin",
            "REMOTE_USER": "admin", "HTTP_X_USER_ID": "admin", "HTTP_X_SUBJECT": "admin-sub"})
        self.assertEqual(self.verifier.calls, [])

    def test_raw_principal_id_is_not_a_credential(self):
        self.error(401, "authentication_required", token="admin")

    def test_malformed_authorization(self):
        for value in ("", "Basic test-admin", "Bearer ", "Bearer test-admin,test-other", "Bearer test-admin\n"):
            self.error(401, "authentication_required", extra={"HTTP_AUTHORIZATION": value})

    def test_disabled_or_unknown_binding_denied(self):
        self.error(401, "authentication_required", token="test-disabled")
        self.verifier.verify = lambda credential: VerifiedIdentity(ISSUER, "unmapped", "test-api", NOW, NOW + timedelta(seconds=20))
        self.error(401, "authentication_required")

    def test_current_local_identity_selected_not_client_claim(self):
        result = self.request("/api/workspaces", token="test-viewer", extra={"HTTP_X_USER_ID": "other"})
        self.assertEqual([w["id"] for w in result["body"]["data"]], ["alpha", "commons"])

    def test_other_space_resource_and_unknown_space_same_denial(self):
        for workspace in ("beta", "missing"):
            self.error(404, "not_found", path=f"/api/workspaces/{workspace}/modules/board/resources/same")

    def test_shared_requires_explicit_membership(self):
        path = "/api/workspaces/commons/modules/board/resources/shared"
        self.error(404, "not_found", path=path, token="test-outsider")
        result = self.request(path, token="test-viewer")
        self.assertEqual(result["body"]["data"]["text"], "Explicit shared content")

    def test_page_is_scoped_and_bounded(self):
        result = self.request(BASE, query="offset=0&limit=1")
        self.assertEqual(result["body"]["data"]["total"], 1)
        self.assertEqual(result["body"]["data"]["items"][0]["workspace_id"], "alpha")
        for query in ("offset=-1", "limit=0", "limit=101", "limit=1&limit=2", "actor_id=other", "offset=x"):
            self.assertEqual(self.request(BASE, query=query)["status"], 400)

    def test_unknown_fields_in_write_reject_actor_or_provider_injection(self):
        for field in ("actor_id", "principal_id", "issuer", "subject", "provider", "workspace_id", "method"):
            self.error(400, "invalid_request", method="PUT", body={"text": "No", field: "other"}, revision='"1"')
        self.assertEqual(self.store.read_resource("admin", "alpha", "board", "same").revision, 1)

    def test_valid_write_and_audit_use_verified_identity(self):
        result = self.request(method="PUT", body={"text": "Updated synthetic text"}, revision='"1"')
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["data"]["revision"], 2)
        audit = self.store.audit_events("admin", "alpha")
        self.assertEqual([(e.actor_id, e.workspace_id, e.action) for e in audit], [("admin", "alpha", "resource.update")])
        self.assertNotIn("test-admin", repr(audit))

    def test_preconditions_and_read_only_role(self):
        self.error(428, "precondition_required", method="PUT", body={"text": "No"})
        for revision in ("1", "*", 'W/"1"', '"0"', '"1", "2"'):
            self.error(400, "invalid_revision", method="PUT", body={"text": "No"}, revision=revision)
        self.error(412, "stale_revision", method="PUT", body={"text": "No"}, revision='"2"')
        self.error(403, "forbidden", method="PUT", body={"text": "No"}, revision='"1"', token="test-viewer")

    def test_membership_administration_and_revocation(self):
        path = "/api/workspaces/alpha/members"
        self.error(403, "forbidden", path=path, token="test-viewer")
        self.assertEqual(len(self.request(path)["body"]["data"]), 2)
        self.assertEqual(self.request(path + "/viewer", method="PATCH", revision='"1"',
                                     body={"role": "viewer", "state": "revoked"})["status"], 200)
        self.error(404, "not_found", token="test-viewer")

    def test_last_admin_protection_through_api(self):
        self.error(409, "last_admin", path="/api/workspaces/alpha/members/admin", method="PATCH",
                   revision='"1"', body={"role": "viewer", "state": "revoked"})

    def test_revoked_member_after_verification_is_rejected_in_transaction(self):
        self.verifier.hook = lambda: self.store.set_membership("admin", "alpha", "viewer",
            role="viewer", state="revoked", expected_revision=1)
        self.error(404, "not_found", token="test-viewer")

    def test_disabled_identity_after_verification_is_rejected_in_transaction(self):
        def disable():
            with sqlite3.connect(self.path) as connection:
                simulate_identity(connection, "UPDATE principals SET active=0 WHERE id='viewer'")
        self.verifier.hook = disable
        self.error(401, "authentication_required", token="test-viewer")

    def test_rebound_identity_does_not_reuse_old_local_privileges(self):
        def change_binding():
            with sqlite3.connect(self.path) as connection:
                simulate_identity(connection, "UPDATE principals SET subject='changed-sub' WHERE id='viewer'")
        self.verifier.hook = change_binding
        self.error(401, "authentication_required", token="test-viewer")

    def test_wrong_issuer_and_audience_denied(self):
        for issuer, audience in (("https://wrong.example.invalid", "test-api"), (ISSUER, "wrong")):
            self.verifier.verify = lambda credential: VerifiedIdentity(issuer, "admin-sub", audience, NOW, NOW + timedelta(seconds=10))
            self.error(401, "authentication_required")

    def test_provider_failure_is_sanitized_and_never_falls_back(self):
        def fail():
            raise RuntimeError("synthetic credential and provider body must not be returned")
        self.verifier.hook = fail
        self.error(503, "authentication_unavailable")

    def test_verifier_runs_outside_transaction(self):
        def probe():
            # Would fail immediately if the app already held its write lock.
            with sqlite3.connect(self.path, timeout=0, isolation_level=None) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ROLLBACK")
        self.verifier.hook = probe
        self.assertEqual(self.request(method="PUT", body={"text": "OK"}, revision='"1"')["status"], 200)

    def test_expiry_after_waiting_for_write_lock_is_denied(self):
        entered = Event()
        original_connect = self.store._connect
        def connect():
            connection = original_connect()
            entered.set()
            return connection
        connection = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(self.store, "_connect", connect):
            result = pool.submit(self.request, method="PUT", body={"text": "Expired"}, revision='"1"')
            self.assertTrue(entered.wait(3))
            self.now = NOW + timedelta(minutes=2)
            connection.execute("COMMIT")
            self.assertEqual(result.result(timeout=5)["status"], 401)
        self.assertEqual(self.store.read_resource("admin", "alpha", "board", "same").revision, 1)

    def test_expiry_during_mutation_rolls_back_resource_and_audit(self):
        times = iter((NOW, NOW, NOW + timedelta(minutes=2)))
        self.app = WorkspaceApplication(store=self.store, providers=(self.provider,), provider_key="test", clock=lambda: next(times))
        self.error(401, "authentication_required", method="PUT", body={"text": "Expired"}, revision='"1"')
        self.assertEqual(self.store.read_resource("admin", "alpha", "board", "same").revision, 1)
        self.assertEqual(self.store.audit_events("admin", "alpha"), ())

    def test_expiry_during_persistence_rolls_back_resource_and_audit(self):
        original = self.store._persist
        def persist(*args, **kwargs):
            original(*args, **kwargs)
            self.now = NOW + timedelta(minutes=2)
        with patch.object(self.store, "_persist", persist):
            self.error(401, "authentication_required", method="PUT", body={"text": "Expired"}, revision='"1"')
        self.assertEqual(self.store.read_resource("admin", "alpha", "board", "same").revision, 1)
        self.assertEqual(self.store.audit_events("admin", "alpha"), ())

    def test_current_policy_revocation_after_lock_wait_is_denied(self):
        entered = Event()
        original_connect = self.store._connect
        def connect():
            connection = original_connect()
            entered.set()
            return connection
        writer = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(self.store, "_connect", connect):
            result = pool.submit(self.request, method="PUT", body={"text": "Denied"}, revision='"1"')
            self.assertTrue(entered.wait(3))
            writer.execute("UPDATE modules SET state='disabled',revision=2 WHERE workspace_id='alpha' AND key='board'")
            writer.execute("COMMIT")
            self.assertEqual(result.result(timeout=5)["body"], {"error": "module_unavailable"})
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT revision FROM resources WHERE workspace_id='alpha'").fetchone()[0], 1)

    def test_parallel_stale_writes_only_one_commits(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request(method="PUT", body={"text": "Concurrent"}, revision='"1"'), range(2)))
        self.assertEqual(sorted(r["status"] for r in results), [200, 412])
        self.assertEqual(len(self.store.audit_events("admin", "alpha")), 1)

    def test_fail_closed_store_error_sanitized(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            simulate_identity(connection, "UPDATE principals SET active=2 WHERE id='viewer'")
        self.error(503, "storage_unavailable", token="test-viewer")

    def test_body_format_length_and_type_boundaries(self):
        self.error(415, "json_required", method="PUT", body={"text": "No"}, extra={"CONTENT_TYPE": "text/plain"})
        for length in ("", "-1", "1000000"):
            self.error(411, "length_required", method="PUT", extra={"CONTENT_LENGTH": length})
        self.error(413, "body_too_large", method="PUT", extra={"CONTENT_LENGTH": "16385"})
        for raw in (b'{"text":"a","text":"b"}', b'{"text":null}', b'{"text":1}', b'{"text":NaN}', b'[]', b'\xff', b'{"text":"\\ud800"}'):
            self.error(400, "invalid_request", method="PUT", revision='"1"', raw=raw)
        self.error(400, "invalid_request", method="PUT", raw=b"{}", extra={"CONTENT_LENGTH": "5"})

    def test_reject_path_method_query_escape(self):
        for path in (BASE + "/../same", BASE + "/%73ame", BASE + "/same/extra", "/api/workspaces/", "/api/workspaces/alpha/authorize"):
            self.error(404, "not_found", path=path)
        for verb in ("POST", "DELETE", "OPTIONS", "TRACE"):
            self.error(404, "not_found", method=verb)
        self.error(404, "not_found", method="PUT", body={"text": "No"}, query="actor_id=other", revision='"1"')

    def test_no_secrets_or_cors_in_response_headers(self):
        for result in (self.request(), self.error(401, "authentication_required", token=None)):
            headers = result["headers"]
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["Vary"], "Authorization")
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            self.assertNotIn("Set-Cookie", headers)
            self.assertNotIn("test-admin", json.dumps(result))

    def test_suspended_workspace_or_disabled_module_denies(self):
        self.store.set_workspace_state("admin", "alpha", state="suspended", expected_revision=1)
        self.error(409, "workspace_unavailable")

    def test_archived_workspace_readable_but_only_revocation_allowed(self):
        self.store.set_workspace_state("admin", "alpha", state="archived", expected_revision=1)
        self.assertEqual(self.request()["status"], 200)
        self.error(409, "workspace_unavailable", method="PUT", body={"text": "No"}, revision='"1"')
        self.assertEqual(self.request("/api/workspaces/alpha/members/viewer", method="PATCH", revision='"1"',
                         body={"role": "viewer", "state": "revoked"})["status"], 200)
        self.error(404, "not_found", token="test-viewer")

    def test_unknown_provider_not_request_configurable(self):
        self.error(401, "authentication_required", token="unrecognized", extra={"HTTP_X_PROVIDER": "test"})
        self.assertEqual(self.request(extra={"HTTP_X_PROVIDER": "evil"})["status"], 200)


if __name__ == "__main__":
    unittest.main()
