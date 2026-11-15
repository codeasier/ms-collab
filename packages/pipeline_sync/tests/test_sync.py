"""Fictional upstream responses only; no external requests or production secrets."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import io
import json
import socket
import sqlite3
import tempfile
from packages.workspaces.tests.identity_fixture import simulate_identity
import unittest
from unittest.mock import Mock, patch

from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.pipeline_api import PipelineApplication, decode_snapshots, encode_snapshots, resource_id
from packages.pipelines import PipelineScope
from packages.pipeline_sync import (BoundCredential, Connection, HTTPSReadTransport, PipelineSynchronizer,
                                    ProviderError, connection_record, connection_resource_id)
from packages.pipeline_sync.provider import _PinnedHTTPS
from packages.workspaces import Membership, ModuleInstance, Principal, Resource, SQLiteWorkspaceService, Workspace, PolicyError

SCOPE = PipelineScope("alpha", "ci", "build")
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
ISSUER = "https://identity.example.invalid"
ORIGIN = "https://adapter.example.invalid"
TOKEN = "synthetic-adapter-token-for-tests-only"


def payload(run_id="r1", pipeline_id="build"):
    return {"id": run_id, "pipeline_id": pipeline_id, "status": "FAILED", "private_debug": "excluded",
            "stages": [{"name": "Test", "jobs": [{"id": "j1", "name": "Unit", "status": "SUCCESS",
            "started_at": "2026-01-01T10:00:00Z", "finished_at": "2026-01-01T10:00:12Z", "log": "excluded"}]}]}


class Verifier:
    def verify(self, credential):
        if credential.value not in {"developer", "reader", "other", "admin"}:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, credential.value, "pipeline-test", NOW, NOW + timedelta(minutes=5))


class Fake:
    def __init__(self):
        self.items = [{"id": "r1", "pipeline_id": "build", "source": "not copied"}]
        self.detail = payload()
        self.calls = []
        self.hook = lambda: None

    def get(self, path, credential, connection):
        self.calls.append(path)
        self.hook()
        return {"items": deepcopy(self.items)} if "?limit=" in path else deepcopy(self.detail)


def initialize(path, connection):
    scope = connection.scope
    return SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p, ISSUER, p) for p in ("admin", "developer", "reader", "other")),
        workspaces=(Workspace("alpha", "Alpha"), Workspace("beta", "Beta"), Workspace("commons", "Commons", "shared")),
        memberships=(Membership("alpha", "admin", "admin"), Membership("alpha", "developer", "developer"),
                     Membership("alpha", "reader", "viewer"), Membership("beta", "other", "admin"),
                     Membership("commons", "other", "admin")),
        modules=(ModuleInstance("alpha", "pipelines", ("read", "write")),
                 ModuleInstance("alpha", "pipeline_connections", ("read",))),
        resources=(Resource("alpha", "pipeline_connections", connection_resource_id(scope), connection_record(connection)),
                   Resource("alpha", "pipelines", resource_id(scope), encode_snapshots(scope, []))),
        operators=frozenset({"admin"}))


class SyncTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "store.sqlite3"
        self.connection = Connection(SCOPE, ORIGIN)
        self.credential = BoundCredential(SCOPE, ORIGIN, TOKEN)
        self.store = initialize(self.path, self.connection)
        self.fake = Fake()
        self.now = NOW
        self.providers = (Provider("test", ISSUER, "pipeline-test", Verifier()),)
        self.sync = PipelineSynchronizer(store=self.store, providers=self.providers, provider_key="test",
                                         clock=lambda: self.now, transport=self.fake)

    def refresh(self, **kwargs):
        options = dict(user_credential=Credential("developer"), connection=self.connection,
                       upstream_credential=self.credential, expected_revision=1)
        options.update(kwargs)
        return self.sync.refresh(**options)

    def record(self):
        return self.store.read_resource("admin", "alpha", "pipelines", resource_id(SCOPE))

    def mutate_config(self, *, text=None, revision=None):
        with sqlite3.connect(self.path) as db:
            if text is not None:
                db.execute("UPDATE resources SET text=? WHERE module_key='pipeline_connections'", (text,))
            if revision is not None:
                db.execute("UPDATE resources SET revision=? WHERE module_key='pipeline_connections'", (revision,))

    def test_success_atomic_local_update_and_existing_api_reads(self):
        result = self.refresh()
        self.assertEqual((result["revision"], result["run_count"]), (2, 1))
        self.assertFalse(result["full_history_complete"])
        self.assertFalse(result["live_provider_verified"])
        self.assertEqual(self.fake.calls, ["/api/v1/pipelines/build/runs?limit=5", "/api/v1/pipelines/build/runs/r1"])
        rows = decode_snapshots(SCOPE, self.record().text)
        self.assertEqual(rows[0]["status"], "failure")
        self.assertNotIn("excluded", self.record().text)
        self.assertEqual(len(self.store.audit_events("admin", "alpha")), 1)
        app = PipelineApplication(store=self.store, providers=self.providers, provider_key="test", clock=lambda: self.now)
        response = {}
        body = b"".join(app({"HTTP_AUTHORIZATION": "Bearer reader", "REQUEST_METHOD": "GET", "QUERY_STRING": "",
                "PATH_INFO": "/api/workspaces/alpha/pipelines/ci/build/durations"},
                lambda status, headers: response.update(status=status)))
        self.assertEqual(response["status"], "200 OK")
        self.assertEqual(json.loads(body)["data"]["groups"][0]["average_seconds"], 12)

    def test_no_authority_or_viewer_stops_before_network(self):
        for user in ("unknown", "reader", "other"):
            with self.subTest(user=user), self.assertRaises((AuthenticationError, PolicyError)):
                self.refresh(user_credential=Credential(user))
            self.assertEqual(self.fake.calls, [])

    def test_wrong_credential_scope_or_origin_never_sent(self):
        for credential in (BoundCredential(PipelineScope("beta", "ci", "build"), ORIGIN, TOKEN),
                           BoundCredential(SCOPE, "https://other.example.invalid", TOKEN)):
            with self.assertRaises(ProviderError):
                self.refresh(upstream_credential=credential)
        self.assertEqual(self.fake.calls, [])
        self.assertNotIn(TOKEN, repr(self.credential))

    def test_shared_workspace_not_union(self):
        scope = PipelineScope("commons", "ci", "build")
        with self.assertRaises(PolicyError):
            self.refresh(connection=Connection(scope, ORIGIN), upstream_credential=BoundCredential(scope, ORIGIN, TOKEN))
        self.assertEqual(self.fake.calls, [])

    def test_other_connection_pipeline_no_fallback(self):
        for scope in (PipelineScope("alpha", "other", "build"), PipelineScope("alpha", "ci", "other")):
            with self.assertRaises(PolicyError):
                self.refresh(connection=Connection(scope, ORIGIN), upstream_credential=BoundCredential(scope, ORIGIN, TOKEN))
        self.assertEqual(self.fake.calls, [])

    def test_read_only_connection_module_required(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE modules SET can_write=1 WHERE key='pipeline_connections'")
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(self.fake.calls, [])

    def test_origin_must_match_current_config(self):
        other = Connection(SCOPE, "https://other.example.invalid")
        with self.assertRaises(PolicyError):
            self.refresh(connection=other, upstream_credential=BoundCredential(SCOPE, other.origin, TOKEN))
        self.assertEqual(self.fake.calls, [])

    def test_malformed_or_unknown_config_rejected_before_network(self):
        for value in ('{', '{"version":true,"origin":"https://adapter.example.invalid","pipeline_id":"build"}',
                      '{"version":1,"version":1}', '{"version":1,"origin":"x","pipeline_id":"build","extra":0}'):
            with self.subTest(value=value):
                self.mutate_config(text=value)
                with self.assertRaises(PolicyError):
                    self.refresh()
        self.assertEqual(self.fake.calls, [])

    def test_stale_revision_no_network(self):
        with self.assertRaises(PolicyError) as error:
            self.refresh(expected_revision=2)
        self.assertEqual(error.exception.status, 412)
        self.assertEqual(self.fake.calls, [])

    def test_revoke_during_list_stops_details_and_publish(self):
        self.fake.hook = lambda: self.store.set_membership("admin", "alpha", "developer", role="developer", state="revoked", expected_revision=1)
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.record().revision, 1)

    def test_revocation_in_check_to_dispatch_gap_still_blocks_publication(self):
        response = Mock(status=200)
        response.getheader.side_effect = lambda name, default=None: {"Content-Type": "application/json", "Content-Encoding": "identity"}.get(name, default)
        response.read.return_value = b'{"items":[]}'
        client = Mock()
        client.getresponse.return_value = response
        def connect(*args, **kwargs):
            self.store.set_membership("admin", "alpha", "developer", role="developer", state="revoked", expected_revision=1)
            return client
        self.sync._transport = HTTPSReadTransport()
        with patch("packages.pipeline_sync.provider._PinnedHTTPS", side_effect=connect):
            with self.assertRaises(PolicyError):
                self.refresh()
        # Dispatch is not atomic with local policy: this checked GET still occurs.
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(self.record().revision, 1)

    def test_revoke_after_detail_stops_publish(self):
        def hook():
            if len(self.fake.calls) == 2:
                self.store.set_membership("admin", "alpha", "developer", role="developer", state="revoked", expected_revision=1)
        self.fake.hook = hook
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(self.record().revision, 1)

    def test_expiry_during_network_stops_publish(self):
        self.fake.hook = lambda: setattr(self, "now", NOW + timedelta(minutes=10))
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.record().revision, 1)

    def test_connection_change_during_read_stops_publish(self):
        self.fake.hook = lambda: self.mutate_config(revision=2)
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.record().revision, 1)

    def test_concurrent_winner_retained(self):
        def hook():
            if len(self.fake.calls) == 1:
                self.store.update_resource("admin", "alpha", "pipelines", resource_id(SCOPE), text=encode_snapshots(SCOPE, []), expected_revision=1)
        self.fake.hook = hook
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(self.record().revision, 2)
        self.assertEqual(decode_snapshots(SCOPE, self.record().text), [])

    def test_disabled_module_and_archive_no_network(self):
        self.store.set_module_state("admin", "alpha", "pipeline_connections", state="disabling", expected_revision=1)
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(self.fake.calls, [])

    def test_archived_workspace_no_network(self):
        self.store.set_workspace_state("admin", "alpha", state="archived", expected_revision=1)
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(self.fake.calls, [])

    def test_identity_rebinding_during_request_prevents_publish(self):
        def hook():
            with sqlite3.connect(self.path) as db:
                simulate_identity(db, "UPDATE principals SET subject='unbound' WHERE id='developer'")
        self.fake.hook = hook
        with self.assertRaises(AuthenticationError):
            self.refresh()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.record().revision, 1)

    def test_connection_changed_after_last_response_prevents_publish(self):
        def hook():
            if len(self.fake.calls) == 2:
                self.mutate_config(revision=2)
        self.fake.hook = hook
        with self.assertRaises(PolicyError):
            self.refresh()
        self.assertEqual(self.record().revision, 1)

    def test_empty_list_is_explicit_subset_not_complete_history(self):
        self.fake.items = []
        result = self.refresh()
        self.assertEqual(result["run_count"], 0)
        self.assertFalse(result["full_history_complete"])
        self.assertEqual(len(self.fake.calls), 1)

    def test_mutable_latest_alias_rejected(self):
        self.fake.items = [{"id": "latest", "pipeline_id": "build"}]
        with self.assertRaises(ProviderError):
            self.refresh()
        self.assertEqual(self.record().revision, 1)

    def test_corrupt_provider_never_overwrites_snapshot(self):
        cases = [None, {"id": "wrong", "pipeline_id": "build"}, {"id": "r1"},
                 payload(pipeline_id="elsewhere"), {**payload(), "stages": "bad"},
                 {**payload(), "stages": [{"name": "Test", "jobs": [None]}]}]
        for detail in cases:
            with self.subTest(detail=detail):
                self.fake.detail = detail
                with self.assertRaises(ProviderError):
                    self.refresh()
                self.assertEqual(self.record().revision, 1)
                self.assertEqual(self.store.audit_events("admin", "alpha"), ())

    def test_list_scope_and_duplicates_rejected(self):
        for items in ([{"id": "r1"}], [{"id": "r1", "pipeline_id": "elsewhere"}],
                      [{"id": "r1", "pipeline_id": "build"}] * 2,
                      [{"id": "../retry", "pipeline_id": "build"}]):
            self.fake.items = items
            with self.assertRaises(ProviderError):
                self.refresh()
            self.assertEqual(self.record().revision, 1)

    def test_limit_and_oversized_projection_fail(self):
        self.fake.items *= 6
        with self.assertRaises(ProviderError):
            self.refresh()
        self.fake.items = [{"id": "r1", "pipeline_id": "build"}]
        self.fake.detail["stages"][0]["jobs"] *= 201
        with self.assertRaises(ProviderError):
            self.refresh()
        self.assertEqual(self.record().revision, 1)

    def test_completed_unknown_without_explicit_conclusion(self):
        self.fake.detail["status"] = "COMPLETED"
        self.fake.detail["stages"][0]["jobs"][0]["status"] = "COMPLETED"
        self.refresh()
        row = decode_snapshots(SCOPE, self.record().text)[0]
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["stages"][0]["jobs"][0]["status"], "unknown")

    def test_no_equal_observation_replaces_existing_evidence(self):
        self.refresh()
        with self.assertRaises(ProviderError):
            self.refresh(expected_revision=2)
        self.assertEqual(self.record().revision, 2)

    def test_transport_error_redacted(self):
        self.fake.hook = Mock(side_effect=RuntimeError("private " + TOKEN))
        with self.assertRaises(ProviderError) as error:
            self.refresh()
        self.assertEqual(str(error.exception), "provider_unavailable")
        self.assertIsNone(error.exception.__cause__)
        self.assertEqual(self.record().revision, 1)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection(SCOPE, ORIGIN)
        self.credential = BoundCredential(SCOPE, ORIGIN, TOKEN)
        self.path = "/api/v1/pipelines/build/runs?limit=5"

    def response(self, *, status=200, body=b'{"items":[]}', content_type="application/json", encoding="identity"):
        response = Mock(status=status)
        response.read.return_value = body
        response.getheader.side_effect = lambda key, default=None: {"Content-Type": content_type, "Content-Encoding": encoding}.get(key, default)
        return response

    def test_exact_get_token_header_and_tls_context(self):
        with patch("packages.pipeline_sync.provider._PinnedHTTPS") as cls:
            cls.return_value.getresponse.return_value = self.response()
            self.assertEqual(HTTPSReadTransport().get(self.path, self.credential, self.connection), {"items": []})
            args, kwargs = cls.return_value.request.call_args
            self.assertEqual(args, ("GET", self.path))
            self.assertEqual(kwargs["headers"]["X-Service-Token"], TOKEN)
            self.assertNotIn(TOKEN, self.path)
            self.assertEqual(cls.call_args.args, ("adapter.example.invalid",))
            self.assertTrue(cls.call_args.kwargs["context"].check_hostname)
            cls.return_value.close.assert_called_once()

    def test_reject_mutation_log_artifact_url_and_query_before_connect(self):
        with patch("packages.pipeline_sync.provider._PinnedHTTPS") as cls:
            for path in ("https://other.invalid", "/api/v1/pipelines/other/runs?limit=5", self.path + "&artifacts=1",
                         "/api/v1/pipelines/build/runs/r1/retry", "/api/v1/pipelines/build/runs/r1/logs",
                         "/api/v1/pipelines/build/runs/r1?artifacts=1", "/api/v1/pipelines/build/runs/../bad"):
                with self.subTest(path=path), self.assertRaises(ProviderError):
                    HTTPSReadTransport().get(path, self.credential, self.connection)
            cls.assert_not_called()

    def test_redirect_error_schema_encoding_and_size_reject(self):
        responses = [self.response(status=x) for x in (301,302,307,400,401,403,404,429,500)] + [
            self.response(body=b'{"x":1,"x":2}'), self.response(body=b'{"x":NaN}'), self.response(body=b'\xff'),
            self.response(content_type="text/html"), self.response(encoding="gzip"),
            self.response(body=b"x" * (self.connection.max_response_bytes+1))]
        for response in responses:
            with patch("packages.pipeline_sync.provider._PinnedHTTPS") as cls:
                cls.return_value.getresponse.return_value = response
                with self.assertRaises(ProviderError):
                    HTTPSReadTransport().get(self.path, self.credential, self.connection)
                self.assertEqual(cls.return_value.request.call_count, 1)
                cls.return_value.close.assert_called_once()

    def test_origin_and_header_injection_rejected(self):
        for origin in ("http://example.com", "https://127.0.0.1", "https://localhost", "https://example.com:443",
                       "https://user@example.com", "https://example.com/", "https://example.com?secret=x",
                       "https://example.com#x", "https://[::1]", "https://EXAMPLE.com", "https://a.com\n"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Connection(SCOPE, origin)
        for token in ("short", TOKEN+"\r\nInjected: true", TOKEN+" "):
            with self.assertRaises(ValueError):
                BoundCredential(SCOPE, ORIGIN, token)

    def test_private_or_mixed_dns_never_connects(self):
        for ips in (("127.0.0.1",), ("10.0.0.1",), ("169.254.169.254",), ("::1",), ("224.0.0.1",), ("ff02::1",), ("8.8.8.8", "192.168.1.1")):
            addresses = [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip,443)) for ip in ips]
            with patch("packages.pipeline_sync.provider.socket.getaddrinfo", return_value=addresses), patch("packages.pipeline_sync.provider.socket.socket") as sock:
                with self.assertRaises(OSError):
                    _PinnedHTTPS("adapter.example.invalid", timeout=1).connect()
                sock.assert_not_called()

    def test_pinned_dns_address_with_original_tls_hostname(self):
        address = ("8.8.8.8", 443)
        context, raw = Mock(), Mock()
        with patch("packages.pipeline_sync.provider.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", address)]) as dns, patch("packages.pipeline_sync.provider.socket.socket", return_value=raw):
            conn = _PinnedHTTPS("adapter.example.invalid", timeout=3, context=context)
            conn.connect()
            dns.assert_called_once()
            raw.connect.assert_called_once_with(address)
            context.wrap_socket.assert_called_once_with(raw, server_hostname="adapter.example.invalid")


if __name__ == "__main__":
    unittest.main()
