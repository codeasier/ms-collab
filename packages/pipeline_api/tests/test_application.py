"""Only synthetic identities, pipeline metadata and temporary SQLite stores."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential, Provider, VerifiedIdentity
from packages.pipeline_api import PipelineApplication, InvalidRecord, decode_snapshots, encode_snapshots, resource_id
from packages.pipelines import PipelineScope
from packages.workspaces import Membership, ModuleInstance, Principal, Resource, SQLiteWorkspaceService, Workspace

ISSUER = "https://identity.example.invalid"
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
SCOPE = PipelineScope("alpha", "example-ci", "build")
BASE = "/api/workspaces/alpha/pipelines/example-ci/build"


def run(scope=SCOPE, *, id="run-1", state="failure", observed="2026-01-01T12:00:00Z"):
    return {**scope.as_dict(), "id": id, "status": state, "observed_at": observed,
            "stages": [{"name": "Checks", "jobs": [{"id": "unit", "name": "Unit tests",
                "status": "success", "started_at": "2026-01-01T10:00:00Z", "finished_at": "2026-01-01T10:00:12Z"}]}]}


class Verifier:
    """Test-only credential lookup. Never imported by application factories."""
    def __init__(self):
        self.hook = lambda: None
        self.calls = 0

    def verify(self, credential):
        self.calls += 1
        self.hook()
        subject = {"test-admin": "admin-sub", "test-reader": "reader-sub", "test-other": "other-sub",
                   "test-disabled": "disabled-sub", "test-outsider": "outsider-sub"}.get(credential.value)
        if type(credential) is not Credential or subject is None:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, subject, "test-api", NOW, NOW + timedelta(seconds=60))


def setup_store(path):
    scopes = (SCOPE, PipelineScope("beta", "example-ci", "build"), PipelineScope("commons", "example-ci", "build"),
              PipelineScope("alpha", "other-ci", "build"), PipelineScope("alpha", "example-ci", "other"))
    resources = tuple(Resource(s.workspace_id, "pipelines", resource_id(s),
        encode_snapshots(s, [run(s, id="run-1"), run(s, id="run-2", state="running", observed="2026-01-01T13:00:00Z")])) for s in scopes)
    return SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p, ISSUER, p + "-sub", p != "disabled") for p in ("admin", "reader", "other", "disabled", "outsider")),
        workspaces=(Workspace("alpha", "Alpha"), Workspace("beta", "Beta"), Workspace("commons", "Commons", "shared")),
        memberships=(Membership("alpha", "admin", "admin"), Membership("alpha", "reader", "viewer"),
                     Membership("alpha", "disabled", "viewer"), Membership("beta", "other", "admin"),
                     Membership("commons", "other", "admin"), Membership("commons", "reader", "viewer")),
        modules=tuple(ModuleInstance(w, "pipelines", ("read", "write")) for w in ("alpha", "beta", "commons")),
        resources=resources, operators=frozenset({"admin"}))


def call(app, suffix="/runs", *, path=None, query="", token="test-reader", method="GET", extra=None):
    environ = {"PATH_INFO": path or BASE + suffix, "REQUEST_METHOD": method, "QUERY_STRING": query,
               "wsgi.input": io.BytesIO(b'{"actor_id":"admin"}'), "CONTENT_LENGTH": "20", "CONTENT_TYPE": "application/json"}
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + token
    environ.update(extra or {})
    result = {}
    def start(status, headers):
        result.update(status=int(status.split()[0]), headers=dict(headers))
    payload = b"".join(app(environ, start))
    result["body"] = json.loads(payload)
    assert int(result["headers"]["Content-Length"]) == len(payload)
    return result


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "policy.sqlite3"
        self.store = setup_store(self.path)
        self.verifier = Verifier()
        self.now = NOW
        self.app = PipelineApplication(store=self.store, providers=(Provider("test", ISSUER, "test-api", self.verifier),),
                                       provider_key="test", clock=lambda: self.now)

    def request(self, *args, **kwargs):
        return call(self.app, *args, **kwargs)

    def update(self, text):
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE resources SET text=? WHERE workspace_id='alpha' AND id=?", (text, resource_id(SCOPE)))

    def error(self, status, code, **kwargs):
        result = self.request(**kwargs)
        self.assertEqual((result["status"], result["body"]), (status, {"error": code}), result)

    def test_configured_factory_required(self):
        for kwargs in ({"store": None, "providers": (), "provider_key": "test"},
                       {"store": self.store, "providers": (), "provider_key": "test"}):
            with self.assertRaises(ValueError):
                PipelineApplication(**kwargs)

    def test_real_loopback_get_and_no_cookie_authority(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server("127.0.0.1", 0, self.app, handler_class=Quiet)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", BASE + "/durations", headers={"Authorization": "Bearer test-reader"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["data"]["groups"][0]["average_seconds"], 12)
            connection.request("GET", BASE + "/runs", headers={"Cookie": "user=admin", "X-User-Id": "admin"})
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            connection.close()
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()

    def test_duplicate_job_ids_retained_in_details_but_not_duration_samples(self):
        row = run()
        row["stages"][0]["jobs"].append(deepcopy(row["stages"][0]["jobs"][0]))
        self.update(encode_snapshots(SCOPE, [row]))
        detail = self.request("/runs/run-1")["body"]["data"]["run"]
        self.assertEqual(len(detail["stages"][0]["jobs"]), 2)
        trend = self.request("/durations")["body"]["data"]
        self.assertEqual(trend["groups"], [])
        self.assertEqual(trend["coverage"]["invalid_duration_jobs"], 2)

    def test_list_paginates_current_scoped_snapshot(self):
        result = self.request(query="offset=0&limit=1")
        self.assertEqual(result["status"], 200)
        data = result["body"]["data"]
        self.assertEqual(data["scope"], SCOPE.as_dict())
        self.assertEqual(data["total"], 2)
        self.assertEqual([r["id"] for r in data["items"]], ["run-2"])
        self.assertEqual(self.request(query="offset=1&limit=1")["body"]["data"]["items"][0]["id"], "run-1")
        self.assertEqual(self.request(query="offset=100")["body"]["data"]["items"], [])
        self.assertFalse(data["source"]["full_history_complete"])
        self.assertFalse(data["source"]["live_provider_verified"])

    def test_status_filter_and_unknown_status_distinct(self):
        self.assertEqual(self.request(query="status=failure")["body"]["data"]["total"], 1)
        self.assertEqual(self.request(query="status=success")["body"]["data"]["total"], 0)
        self.assertEqual(self.request(query="status=unknown")["body"]["data"]["total"], 0)

    def test_detail_allowlisted_and_missing_run(self):
        data = self.request("/runs/run-1")["body"]["data"]
        self.assertEqual(data["run"], decode_snapshots(SCOPE, encode_snapshots(SCOPE, [run()]))[0])
        self.error(404, "not_found", suffix="/runs/no-run")

    def test_durations_failure_run_success_job_and_coverage(self):
        result = self.request("/durations", query="limit=1")["body"]["data"]
        self.assertEqual(result["coverage"]["nonterminal_or_unknown_runs"], 1)
        self.assertEqual(result["groups"][0]["samples"][0]["run_status"], "failure")
        self.assertEqual(result["groups"][0]["latest_seconds"], 12)

    def test_absent_stages_are_missing_not_success(self):
        row = run()
        row["stages"] = None
        self.update(encode_snapshots(SCOPE, [row]))
        self.assertFalse(self.request()["body"]["data"]["items"][0]["details_available"])
        data = self.request("/durations")["body"]["data"]
        self.assertEqual(data["coverage"]["missing_details"], 1)
        self.assertEqual(data["groups"], [])

    def test_all_routes_use_no_store_headers(self):
        for suffix in ("/runs", "/runs/run-1", "/durations", "/runs/missing"):
            headers = self.request(suffix)["headers"]
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["Vary"], "Authorization")
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_no_client_identity_or_default_auth(self):
        self.error(401, "authentication_required", token=None, extra={"REMOTE_USER": "admin", "HTTP_X_USER_ID": "admin", "HTTP_COOKIE": "user=admin"})
        self.error(401, "authentication_required", token="admin")
        self.error(401, "authentication_required", token="test-disabled")
        self.error(404, "not_found", token="test-outsider", extra={"HTTP_X_USER_ID": "admin", "HTTP_X_WORKSPACE_ID": "alpha"})

    def test_workspace_and_unknown_denials_no_record_decode(self):
        with patch("packages.pipeline_api.application.decode_snapshots", side_effect=AssertionError("must not load")):
            for w in ("beta", "missing"):
                self.error(404, "not_found", path=f"/api/workspaces/{w}/pipelines/example-ci/build/runs")

    def test_shared_membership_not_union(self):
        path = "/api/workspaces/commons/pipelines/example-ci/build/runs"
        self.error(404, "not_found", path=path, token="test-admin")
        result = self.request(path=path)["body"]["data"]
        self.assertEqual(result["scope"]["workspace_id"], "commons")
        self.assertEqual(result["total"], 2)

    def test_connections_and_pipelines_are_exact_not_fallback(self):
        for c, p in (("other-ci", "build"), ("example-ci", "other")):
            data = self.request(path=f"/api/workspaces/alpha/pipelines/{c}/{p}/runs")["body"]["data"]
            self.assertEqual((data["scope"]["connection_id"], data["scope"]["pipeline_id"]), (c, p))
        self.error(404, "not_found", path="/api/workspaces/alpha/pipelines/missing/build/runs")
        self.error(404, "not_found", path="/api/workspaces/alpha/pipelines/example-ci/missing/runs")

    def test_suspended_and_disabled_module_reject_before_decode(self):
        with patch("packages.pipeline_api.application.decode_snapshots", side_effect=AssertionError("must not parse")):
            self.store.set_module_state("admin", "alpha", "pipelines", state="disabling", expected_revision=1)
            self.error(409, "module_unavailable")

    def test_capability_removed_denies(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE modules SET can_read=0 WHERE workspace_id='alpha'")
        self.error(403, "forbidden")

    def test_archived_retains_read_and_revocation_works(self):
        self.store.set_workspace_state("admin", "alpha", state="archived", expected_revision=1)
        self.assertEqual(self.request()["status"], 200)
        self.store.set_membership("admin", "alpha", "reader", role="viewer", state="revoked", expected_revision=1)
        self.error(404, "not_found")

    def test_revocation_after_verify_before_transaction(self):
        self.verifier.hook = lambda: self.store.set_membership("admin", "alpha", "reader", role="viewer", state="revoked", expected_revision=1)
        self.error(404, "not_found")

    def test_principal_rebinding_after_verify_rejected(self):
        def rebind():
            with sqlite3.connect(self.path) as connection:
                connection.execute("UPDATE principals SET subject='rebound' WHERE id='reader'")
        self.verifier.hook = rebind
        self.error(401, "authentication_required")

    def test_expiry_during_projection_is_rejected(self):
        from packages.pipeline_api.application import summarize_durations
        def slow(*args, **kwargs):
            result = summarize_durations(*args, **kwargs)
            self.now = NOW + timedelta(seconds=61)
            return result
        with patch("packages.pipeline_api.application.summarize_durations", side_effect=slow):
            self.error(401, "authentication_required", suffix="/durations")

    def test_explicit_pending_request_barrier_observes_revoke(self):
        entered, release = Event(), Event()
        original = self.store._transaction
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timeout")
            return original(*args, **kwargs)
        with patch.object(self.store, "_transaction", delayed), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.request)
            try:
                self.assertTrue(entered.wait(5))
                separate = SQLiteWorkspaceService(self.path)
                separate.set_membership("admin", "alpha", "reader", role="viewer", state="revoked", expected_revision=1)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)["status"], 404)

    def test_get_does_not_mutate_resources_or_audit(self):
        before = self.store.read_resource("admin", "alpha", "pipelines", resource_id(SCOPE))
        self.request(); self.request("/durations"); self.request("/runs/run-1")
        self.assertEqual(before, self.store.read_resource("admin", "alpha", "pipelines", resource_id(SCOPE)))
        self.assertEqual(self.store.audit_events("admin", "alpha"), ())

    def test_writes_and_inherited_generic_endpoint_disabled(self):
        for verb in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            self.error(404, "not_found", method=verb, token="test-admin")
        self.error(404, "not_found", path="/api/workspaces/alpha/modules/pipelines/resources/anything", method="PUT", token="test-admin")
        self.assertEqual(self.store.audit_events("admin", "alpha"), ())

    def test_invalid_queries_and_paths_fail_without_fallback(self):
        for query in ("limit=0", "limit=101", "offset=-1", "offset=100001", "limit=1&limit=2", "status=completed", "actor_id=admin", "connection_id=other", "limit=", "limit=x", "scope=beta", "bad", "limit=1&offset=0&status=success&x=y"):
            self.error(400, "invalid_request", query=query)
        self.error(400, "invalid_request", suffix="/durations", query="status=success")
        for path in (BASE + "/runs/../secret", BASE + "/runs/", BASE + "/durations/run-1", BASE.replace("alpha", "alpha%2fbeta") + "/runs"):
            self.error(404, "not_found", path=path)

    def test_malformed_store_fails_closed_and_never_echoes(self):
        for text in ('{"credential":"sensitive-example"}', '{"version":1,"version":1}', "NaN", "[" * 1000):
            self.update(text)
            self.error(503, "pipeline_data_unavailable")

    def test_wrong_scope_stored_under_correct_id_rejected(self):
        other = PipelineScope("beta", "example-ci", "build")
        self.update(encode_snapshots(other, [run(other)]))
        self.error(503, "pipeline_data_unavailable")

    def test_future_observation_not_presented_as_evidence(self):
        self.update(encode_snapshots(SCOPE, [run(observed="2026-01-03T00:00:00Z")]))
        self.error(503, "pipeline_data_unavailable")

    def test_provider_errors_redacted(self):
        def error(_):
            raise AuthenticationUnavailable()
        self.verifier.verify = error
        self.error(503, "authentication_unavailable")

    def test_storage_errors_redacted(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE resources")
        self.error(503, "storage_unavailable")


class CodecTests(unittest.TestCase):
    def test_roundtrip_and_key_stable_distinct_tuple(self):
        text = encode_snapshots(SCOPE, [run()])
        rows = decode_snapshots(SCOPE, text)
        self.assertEqual(rows[0]["id"], "run-1")
        self.assertNotEqual(resource_id(PipelineScope("alpha", "a.b", "c")), resource_id(PipelineScope("alpha", "a", "b.c")))
        self.assertEqual(resource_id(SCOPE), resource_id(PipelineScope("beta", "example-ci", "build")))  # workspace belongs to resource PK

    def test_unknown_fields_reject_raw_provider_content(self):
        for place, field in (("run", "logs"), ("run", "url"), ("job", "credential"), ("stage", "provider")):
            row = run()
            target = row if place == "run" else row["stages"][0] if place == "stage" else row["stages"][0]["jobs"][0]
            target[field] = "never-echo"
            with self.assertRaises(InvalidRecord) as error:
                encode_snapshots(SCOPE, [row])
            self.assertNotIn("never-echo", str(error.exception))

    def test_exact_scope_and_duplicate_ids(self):
        other = PipelineScope("beta", "example-ci", "build")
        for rows in ([run(other)], [run(), run()]):
            with self.assertRaises(InvalidRecord):
                encode_snapshots(SCOPE, rows)

    def test_normalization_is_explicit_not_completed_means_success(self):
        for state in ("completed", True, "passed", 0):
            with self.assertRaises(InvalidRecord):
                encode_snapshots(SCOPE, [run(state=state)])
        self.assertEqual(decode_snapshots(SCOPE, encode_snapshots(SCOPE, [run(state="unknown")]))[0]["status"], "unknown")

    def test_missing_times_retained_for_coverage(self):
        row = run()
        row["stages"][0]["jobs"][0]["finished_at"] = None
        self.assertIsNone(decode_snapshots(SCOPE, encode_snapshots(SCOPE, [row]))[0]["stages"][0]["jobs"][0]["finished_at"])

    def test_invalid_times_and_unicode(self):
        for value in ("2026-01-01", "2026-01-01T10:00:00", "not-time", None):
            with self.assertRaises(InvalidRecord):
                encode_snapshots(SCOPE, [run(observed=value)])
        row = run(); row["stages"][0]["name"] = "bad\ud800"
        with self.assertRaises(InvalidRecord):
            encode_snapshots(SCOPE, [row])

    def test_bounds_and_version(self):
        for rows in ([run(id=f"run-{n}") for n in range(101)], [run(id=f"run-{n}") for n in range(50)]):
            with self.assertRaises(InvalidRecord):
                encode_snapshots(SCOPE, rows)
        text = encode_snapshots(SCOPE, [run()])
        for version in (True, 2, "1"):
            body = json.loads(text); body["version"] = version
            with self.assertRaises(InvalidRecord):
                decode_snapshots(SCOPE, json.dumps(body))
        with self.assertRaises(InvalidRecord):
            decode_snapshots(SCOPE, "x" * 10001)


if __name__ == "__main__":
    unittest.main()
