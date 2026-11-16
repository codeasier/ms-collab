# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic data only. No external adapter, production runner or real identity."""
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Thread
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant as simulate_identity
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.gui_smoke import (GuiSmokeApplication, SmokeScope, SmokeConnection,
                                initialize, initialize_connections, migrate_v1, regrant_connection, project_task, publish_snapshot, reported_state)
from packages.identity import AuthenticationError, Provider, VerifiedIdentity
from packages.workspaces import (Membership, ModuleInstance, PolicyError, Principal,
                                 SQLiteWorkspaceService, StorageError, Workspace)

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
ISSUER = "https://identity.example.invalid"
BASE = "/api/workspaces/alpha/gui-smoke/panel/repo"
PEOPLE = ("alice", "bob", "writer", "admin", "outsider", "operator", "replacement")


class TestVerifier:
    def verify(self, credential):
        if credential.value not in {"synthetic-" + p for p in PEOPLE}:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, credential.value[10:], "smoke-tests", NOW,
                                NOW + timedelta(seconds=60))


def task(identity="task-1", **overrides):
    value = {"id": identity, "status": "success", "source": "api", "branch": "main",
             "created_at": (NOW - timedelta(minutes=10)).isoformat(),
             "started_at": (NOW - timedelta(minutes=9)).isoformat(),
             "finished_at": (NOW - timedelta(minutes=8)).isoformat(),
             "commit": "a" * 40, "requested_commit": "b" * 40,
             "summary": {"passed": 1, "failed": 0, "skipped": 0, "total": 1},
             "stages": [{"id": "gui", "name": "Synthetic GUI check", "status": "success",
                         "started_at": (NOW - timedelta(minutes=9)).isoformat(),
                         "finished_at": (NOW - timedelta(minutes=8)).isoformat()}]}
    value.update(overrides)
    return value


def request(app, suffix="/tasks", *, base=BASE, method="GET", query="", token="synthetic-alice", extra=None):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": base + suffix, "QUERY_STRING": query,
               "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO()}
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + token
    environ.update(extra or {})
    result = {}
    def start(status, headers):
        result.update(status=int(status.split()[0]), headers=dict(headers))
    result["body"] = json.loads(b"".join(app(environ, start)))
    return result


class SmokeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "policy.sqlite3"
        self.store = SQLiteWorkspaceService.initialize(self.path,
            principals=tuple(Principal(p, ISSUER, p) for p in PEOPLE),
            workspaces=tuple(Workspace(w, w, "shared" if w == "commons" else "product") for w in ("alpha", "beta", "commons")),
            memberships=tuple(Membership(w, p, "admin" if p == "admin" else "developer" if p in {"writer", "outsider"} else "viewer")
                              for w in ("alpha", "beta", "commons") for p in PEOPLE if p != "operator"),
            modules=tuple(ModuleInstance(w, "gui_smoke", ("read", "write")) for w in ("alpha", "beta", "commons")),
            operators=frozenset({"operator"}))
        initialize(self.store)
        self.scope = SmokeScope("alpha", "panel", "repo")
        self.configured = SmokeConnection(self.scope, frozenset({"alice", "bob"}), frozenset({"writer"}))
        initialize_connections(self.store,(self.configured,))
        self.now = NOW
        self.app = self.application((self.configured,))
        self.publish()

    def application(self, connections):
        return GuiSmokeApplication(store=self.store,
            providers=(Provider("test", ISSUER, "smoke-tests", TestVerifier()),),
            provider_key="test", connections=connections, clock=lambda: self.now)

    def publish(self, **overrides):
        args = dict(actor="writer", tasks=(task(),), observed_at=(NOW - timedelta(minutes=1)).isoformat(),
                    expected_revision=0, expected_grant_revision=1, clock=lambda: NOW)
        args.update(overrides)
        return publish_snapshot(self.store, self.configured, **args)

    def sql(self, statement, parameters=()):
        with sqlite3.connect(self.path) as connection:
            return simulate_identity(connection,statement,parameters) if statement.startswith("UPDATE principals SET ") else connection.execute(statement, parameters).fetchall()

    def test_legacy_snapshot_requires_explicit_migration_and_destructive_regrant(self):
        record=json.loads(self.sql('SELECT record FROM gui_smoke_snapshots')[0][0])
        record['version']=2;record.pop('grant_revision');record.pop('grant_digest')
        self.sql('UPDATE gui_smoke_snapshots SET record=?',(json.dumps(record),))
        self.sql('DROP TABLE gui_smoke_grants')
        before=self.sql('SELECT * FROM gui_smoke_snapshots')
        with self.assertRaises(StorageError):initialize(self.store)
        migrate_v1(self.store)
        self.assertEqual(request(self.app)['status'],404)
        with self.assertRaises(PolicyError):initialize_connections(self.store,(self.configured,))
        self.assertEqual(before,self.sql('SELECT * FROM gui_smoke_snapshots'))
        regrant_connection(self.store,self.configured,actor='admin',expected_grant_revision=0,expected_snapshot_revision=1,clock=lambda:NOW)
        self.publish(expected_revision=2,observed_at=NOW.isoformat())
        self.assertEqual(request(self.app)['status'],200)
        current=json.loads(self.sql('SELECT record FROM gui_smoke_snapshots')[0][0])
        self.assertEqual(current['version'],3)
        self.assertEqual(current['readers']['alice']['generation'],1)

    def test_foreign_realm_snapshot_denied(self):
        record = json.loads(self.sql("SELECT record FROM gui_smoke_snapshots")[0][0])
        record['policy_realm'] = '0' * 32
        self.sql("UPDATE gui_smoke_snapshots SET record=?", (json.dumps(record),))
        self.assertEqual(request(self.app)["status"], 404)

    def test_explicit_current_reader_and_workspace_isolation(self):
        for token in ("synthetic-writer", "synthetic-admin", "synthetic-outsider", "synthetic-operator"):
            for suffix in ("/tasks", "/tasks/task-1", "/summary"):
                self.assertEqual(request(self.app, suffix, token=token)["status"], 404)
        for base in (BASE.replace("alpha", "beta"), BASE.replace("panel", "other"), BASE.replace("/repo", "/other")):
            self.assertEqual(request(self.app, base=base)["status"], 404)
        self.assertEqual(request(self.app, token="synthetic-bob")["status"], 200)

    def test_no_anonymous_cookie_header_or_role_authentication(self):
        for extra in ({}, {"HTTP_COOKIE": "subject=alice"}, {"HTTP_X_USER": "alice", "HTTP_X_ROLE": "admin"}):
            self.assertEqual(request(self.app, token=None, extra=extra)["status"], 401)
        self.assertEqual(request(self.app, token="forged")["status"], 401)

    def test_snapshot_absence_is_not_empty_verified_history(self):
        self.sql("DELETE FROM gui_smoke_snapshots")
        result = request(self.app)["body"]["data"]
        self.assertFalse(result["available"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["source"]["revision"], 1)
        self.assertIsNone(result["source"]["observed_at"])
        self.assertFalse(result["source"]["execution_enabled"])
        self.assertEqual(request(self.app, "/tasks/task-1")["status"], 404)
        self.publish(tasks=(),expected_revision=1)
        self.assertTrue(request(self.app)["body"]["data"]["available"])

    def test_safe_detail_does_not_forge_execution_or_requested_commit(self):
        result = request(self.app, "/tasks/task-1")
        row = result["body"]["data"]["task"]
        self.assertEqual((row["commit"], row["requested_commit"]), ("a" * 40, "b" * 40))
        self.assertEqual(row["duration_seconds"], 60)
        self.assertEqual(row["stages"][0]["duration_seconds"], 60)
        self.assertEqual(result["headers"]["Cache-Control"], "no-store")
        text = json.dumps(result)
        for private in (ISSUER, "membership_revision", "readers", "publishers", "receivers", "reports_dir"):
            self.assertNotIn(private, text)
        self.assertEqual(result["body"]["data"]["capabilities"], dict(read=True, submit=False, cancel=False, retry=False, reproduce=False, refresh=False, download=False))

    def test_unknown_state_is_explicit_not_inferred_from_positive_counts(self):
        for raw in ("completed", "SUCCESS", "passed", True, None, {}, "cancelling"):
            self.assertEqual(reported_state(raw), "unknown")
        row = task(status="unknown")
        self.assertEqual(project_task(row, NOW.isoformat())["status"], "unknown")
        self.publish(tasks=(row,), expected_revision=1, observed_at=NOW.isoformat())
        result = request(self.app, "/summary")["body"]["data"]
        self.assertEqual((result["states"]["unknown"], result["states"]["success"]), (1, 0))
        self.assertIn("not_live_queue", result["interpretation"])

    def test_missing_details_remain_unknown_not_zero(self):
        row = task(status="cancelled", started_at=None, finished_at=None, summary=None, stages=None, commit=None)
        projection = project_task(row, NOW.isoformat())
        self.assertIsNone(projection["duration_seconds"])
        self.assertIsNone(projection["summary"])
        self.assertIsNone(projection["stages"])

    def test_selected_text_is_literal_never_html_or_link_fetch(self):
        row = task(branch="<img src=x onerror=alert(1)>")
        self.publish(tasks=(row,), expected_revision=1, observed_at=NOW.isoformat())
        self.assertEqual(request(self.app, "/tasks/task-1")["body"]["data"]["task"]["branch"], row["branch"])

    def test_rejects_raw_panel_identity_paths_logs_and_urls(self):
        for field in ("submitter", "receivers", "url", "links", "reports_dir", "runner", "command", "log", "message", "result_html"):
            row = task()
            row[field] = "synthetic-private-marker"
            with self.assertRaises(ValueError):
                project_task(row, NOW.isoformat())

    def test_task_identity_and_commit_validation(self):
        for overrides in ({"id": "../other"}, {"status": "completed"}, {"status": []}, {"source": "private"},
                          {"commit": "abc"}, {"requested_commit": "A" * 40}, {"branch": "bad\ntext"},
                          {"status": "pending", "commit": "a" * 40}):
            with self.assertRaises(ValueError):
                project_task(task(**overrides), NOW.isoformat())

    def test_time_and_count_contradictions_fail_closed(self):
        for row in (task(started_at=NOW.isoformat()), task(created_at="2026-01-02T00:00:00"),
                    task(finished_at=(NOW + timedelta(days=1)).isoformat()), task(status="running"),
                    task(summary={"passed": True, "failed": 0, "skipped": 0, "total": 1}),
                    task(summary={"passed": 1, "failed": 0, "skipped": 0, "total": 2}),
                    task(summary={"passed": 0, "failed": 1, "skipped": 0, "total": 1})):
            with self.assertRaises(ValueError):
                project_task(row, NOW.isoformat())

    def test_duplicate_or_invalid_stages_are_not_silently_dropped(self):
        row = task()
        row["stages"] *= 2
        with self.assertRaises(ValueError):
            project_task(row, NOW.isoformat())
        row = task()
        row["stages"][0]["raw_log"] = "synthetic-private-marker"
        with self.assertRaises(ValueError):
            project_task(row, NOW.isoformat())

    def test_revision_fixed_pagination_and_exact_filters(self):
        self.publish(tasks=tuple(task(f"t{i:03}", source="scheduled" if i % 2 else "api") for i in range(55)),
                     expected_revision=1, observed_at=NOW.isoformat())
        first = request(self.app, query="limit=50")["body"]["data"]
        self.assertEqual((len(first["items"]), first["total"], first["next_offset"]), (50, 55, 50))
        self.assertEqual(request(self.app, query="offset=50")["status"], 428)
        final = request(self.app, query="offset=50&revision=2")["body"]["data"]
        self.assertEqual(len(final["items"]), 5)
        self.assertIsNone(final["next_offset"])
        filtered = request(self.app, query="source=scheduled&status=success&branch=main")["body"]["data"]
        self.assertEqual(filtered["total"], 27)
        self.assertEqual(request(self.app, query="branch=mai")["body"]["data"]["total"], 0)
        for suffix in ("/tasks", "/tasks/t001", "/summary"):
            self.assertEqual(request(self.app, suffix, query="revision=1")["status"], 412)

    def test_read_only_methods_and_no_external_catalog_or_runner_routes(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD"):
            for suffix in ("/tasks", "/tasks/task-1", "/tasks/task-1/cancel", "/refresh"):
                self.assertEqual(request(self.app, suffix, method=method)["status"], 404)
        for suffix in ("/health", "/queue", "/catalog/branches", "/schedule", "/tasks/task-1/log", "/tasks/task-1/results", "/tasks/task-1/report", "/tasks/task-1/bundle"):
            self.assertEqual(request(self.app, suffix)["status"], 404)

    def test_strict_query_parameters(self):
        for query in ("limit=0", "limit=101", "offset=501", "offset=-1", "limit=1&limit=2", "url=https://invalid", "actor=writer", "status=completed", "revision=true", "branch=", "cursor=foo", "revision=9007199254740992"):
            self.assertEqual(request(self.app, query=query)["status"], 400, query)
        self.assertEqual(request(self.app, "/tasks/task-1", query="limit=1")["status"], 400)

    def test_reads_do_not_write_snapshot_or_audit(self):
        before = self.sql("SELECT * FROM gui_smoke_snapshots"), self.sql("SELECT * FROM audit")
        for suffix in ("/tasks", "/tasks/task-1", "/summary"):
            self.assertEqual(request(self.app, suffix)["status"], 200)
        self.assertEqual(before, (self.sql("SELECT * FROM gui_smoke_snapshots"), self.sql("SELECT * FROM audit")))

    def test_revocation_is_current_and_restoring_membership_does_not_resurrect(self):
        self.store.set_membership("admin", "alpha", "alice", role="viewer", state="revoked", expected_revision=1)
        self.assertEqual(request(self.app)["status"], 404)
        with self.assertRaisesRegex(PolicyError, "membership_revoked"):
            self.store.set_membership("admin", "alpha", "alice", role="viewer", state="active", expected_revision=2)
        # Synthetic trusted restoration, not an application shortcut around the
        # core's permanent revocation rule. A newer membership cannot reuse grants.
        self.sql("UPDATE memberships SET state='active',revision=3 WHERE workspace_id='alpha' AND principal_id='alice'")
        self.assertEqual(request(self.app)["status"], 404)
        self.assertEqual(request(self.app, token="synthetic-bob")["status"], 200)
        self.publish(expected_revision=1, observed_at=NOW.isoformat())
        self.assertEqual(request(self.app)["status"], 200)

    def test_connection_reader_change_does_not_retroactively_expand_audience(self):
        expanded = SmokeConnection(self.scope, self.configured.readers | {"outsider"}, self.configured.publishers)
        self.assertEqual(request(self.application((expanded,)), token="synthetic-outsider")["status"], 404)
        removed = SmokeConnection(self.scope, frozenset({"bob"}), self.configured.publishers)
        self.assertEqual(request(self.application((removed,)))["status"], 404)
        self.assertEqual(request(self.application((removed,)), token="synthetic-bob")["status"], 404)

    def test_persistent_regrant_invalidates_old_instance_configuration(self):
        narrowed=SmokeConnection(self.scope,frozenset({'bob'}),self.configured.publishers)
        fresh=self.application((narrowed,))
        self.assertEqual(request(fresh)['status'],404)
        self.assertEqual(request(self.app)['status'],200)
        with self.assertRaises(PolicyError):
            publish_snapshot(self.store,narrowed,actor='writer',tasks=(task(),),expected_revision=1,expected_grant_revision=1,
                observed_at=NOW.isoformat(),clock=lambda:NOW)
        regrant_connection(self.store,narrowed,actor='admin',expected_grant_revision=1,expected_snapshot_revision=1,clock=lambda:NOW)
        publish_snapshot(self.store,narrowed,actor='writer',tasks=(task(),),expected_revision=2,expected_grant_revision=2,
                observed_at=NOW.isoformat(),clock=lambda:NOW)
        self.assertEqual(request(self.app,token='synthetic-bob')['status'],404)
        self.assertEqual(request(fresh,token='synthetic-bob')['status'],200)

    def test_unapproved_snapshot_reader_does_not_interpret_task_payloads(self):
        raw = json.loads(self.sql("SELECT record FROM gui_smoke_snapshots")[0][0])
        raw["tasks"][0]["log"] = "synthetic-private-marker"
        self.sql("UPDATE gui_smoke_snapshots SET record=?", (json.dumps(raw),))
        expanded = SmokeConnection(self.scope, self.configured.readers | {"outsider"}, self.configured.publishers)
        self.assertEqual(request(self.application((expanded,)), token="synthetic-outsider")["status"], 404)
        self.assertEqual(request(self.app)["status"], 503)

    def test_module_disable_and_reenable_requires_explicit_republication(self):
        self.store.set_module_state("admin", "alpha", "gui_smoke", state="disabling", expected_revision=1)
        self.assertEqual(request(self.app)["status"], 409)
        self.store.set_module_state("admin", "alpha", "gui_smoke", state="disabled", expected_revision=2)
        self.store.set_module_state("admin", "alpha", "gui_smoke", state="enabled", expected_revision=3)
        self.assertEqual(request(self.app)["status"], 404)

    def test_workspace_state_revision_is_fenced(self):
        self.store.set_workspace_state("operator", "alpha", state="suspended", expected_revision=1)
        self.assertEqual(request(self.app)["status"], 409)
        self.store.set_workspace_state("operator", "alpha", state="active", expected_revision=2)
        self.assertEqual(request(self.app)["status"], 404)

    def test_simulated_principal_aba_cannot_restore_snapshot(self):
        self.sql("UPDATE principals SET subject='other' WHERE id='alice'")
        self.assertEqual(request(self.app)["status"], 401)
        self.sql("UPDATE principals SET subject='alice' WHERE id='alice'")
        self.assertEqual(request(self.app)["status"], 404)
        self.sql("UPDATE principals SET active=0 WHERE id='alice'")
        self.assertEqual(request(self.app)["status"], 401)

    def test_shared_workspace_is_not_public_or_aggregate(self):
        scope = SmokeScope("commons", "panel", "repo")
        configured = SmokeConnection(scope, frozenset({"alice"}), frozenset({"writer"}))
        initialize_connections(self.store,(configured,))
        publish_snapshot(self.store, configured, actor="writer", expected_grant_revision=1, tasks=(task(),), expected_revision=0,
                         observed_at=NOW.isoformat(), clock=lambda: NOW)
        app = self.application((configured,))
        base = BASE.replace("alpha", "commons")
        self.assertEqual(request(app, base=base)["status"], 200)
        self.assertEqual(request(app, base=base, token="synthetic-bob")["status"], 404)
        self.assertEqual(request(app, base=base, token=None)["status"], 401)

    def test_publication_requires_current_explicit_writer_and_all_reader_authority(self):
        for actor in ("admin", "alice", "outsider", "operator"):
            with self.assertRaises(PolicyError):
                self.publish(actor=actor, expected_revision=1, observed_at=NOW.isoformat())
        self.store.set_membership("admin", "alpha", "bob", role="viewer", state="revoked", expected_revision=1)
        with self.assertRaises(PolicyError):
            self.publish(expected_revision=1, observed_at=NOW.isoformat())
        self.assertEqual(self.sql("SELECT revision FROM gui_smoke_snapshots"), [(1,)])

    def test_missing_and_stale_publication_revision(self):
        for expected, code in ((None, 428), (0, 412), (True, 400), (-1, 400)):
            with self.assertRaises(PolicyError) as caught:
                self.publish(expected_revision=expected, observed_at=NOW.isoformat())
            self.assertEqual(caught.exception.status, code)

    def test_publication_observation_must_advance_without_future_data(self):
        for observed in ((NOW - timedelta(minutes=1)).isoformat(), (NOW + timedelta(seconds=1)).isoformat()):
            with self.assertRaises(ValueError):
                self.publish(expected_revision=1, observed_at=observed)

    def test_concurrent_publication_cas_preserves_one_snapshot_and_audit(self):
        barrier, results = Barrier(2), []
        before = len(self.sql("SELECT * FROM audit"))
        def writer(identity):
            barrier.wait()
            try:
                self.publish(tasks=(task(identity),), expected_revision=1, observed_at=NOW.isoformat())
                results.append(200)
            except PolicyError as error:
                results.append(error.status)
        threads = [Thread(target=writer, args=(identity,)) for identity in ("new-a", "new-b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(results), [200, 412])
        self.assertEqual(self.sql("SELECT revision FROM gui_smoke_snapshots"), [(2,)])
        self.assertEqual(len(self.sql("SELECT * FROM audit")), before + 1)

    def test_publication_failure_rolls_back_snapshot_revision_and_audit(self):
        before = self.sql("SELECT * FROM gui_smoke_snapshots"), self.sql("SELECT * FROM audit")
        self.sql("CREATE TRIGGER fail_snapshot BEFORE UPDATE ON gui_smoke_snapshots BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(StorageError):
            self.publish(expected_revision=1, observed_at=NOW.isoformat())
        self.assertEqual(before, (self.sql("SELECT * FROM gui_smoke_snapshots"), self.sql("SELECT * FROM audit")))

    def test_credential_expiry_after_projection_returns_no_saved_content(self):
        from packages.gui_smoke.application import read_snapshot
        def expire(*args):
            result = read_snapshot(*args)
            self.now = NOW + timedelta(seconds=61)
            return result
        with patch("packages.gui_smoke.application.read_snapshot", side_effect=expire):
            response = request(self.app)
        self.assertEqual(response["status"], 401)
        self.assertNotIn("task-1", str(response))

    def test_corruption_and_scope_misplacement_fail_closed(self):
        record = self.sql("SELECT record FROM gui_smoke_snapshots")[0][0]
        malformed = json.loads(record)
        malformed["tasks"][0]["receivers"] = ["synthetic-private-marker"]
        self.sql("UPDATE gui_smoke_snapshots SET record=?", (json.dumps(malformed),))
        response = request(self.app)
        self.assertEqual(response["status"], 503)
        self.assertNotIn("synthetic-private-marker", str(response))
        self.sql("UPDATE gui_smoke_snapshots SET record=?,workspace_id='beta'", (record,))
        configured = SmokeConnection(SmokeScope("beta", "panel", "repo"), self.configured.readers, self.configured.publishers)
        self.assertEqual(request(self.application((configured,)), base=BASE.replace("alpha", "beta"))["status"], 404)

    def test_duplicate_keys_and_revision_corruption_fail_closed(self):
        record = self.sql("SELECT record FROM gui_smoke_snapshots")[0][0]
        self.sql("UPDATE gui_smoke_snapshots SET record=?", ('{"version":1,' + record[1:],))
        self.assertEqual(request(self.app)["status"], 503)
        self.sql("UPDATE gui_smoke_snapshots SET record=?,revision=2", (record,))
        self.assertEqual(request(self.app)["status"], 503)

    def test_degraded_schema_is_not_silently_repaired(self):
        self.sql("ALTER TABLE gui_smoke_snapshots RENAME TO old_smoke")
        self.sql("CREATE TABLE gui_smoke_snapshots (workspace_id,connection_id,repository_id,revision,record)")
        with self.assertRaises(StorageError):
            initialize(self.store)
        self.assertEqual(request(self.app)["status"], 503)

    def test_configuration_and_input_limits(self):
        for connections in ([self.configured], (self.configured, self.configured)):
            with self.assertRaises(ValueError):
                self.application(connections)
        with self.assertRaises(ValueError):
            SmokeConnection(self.scope, frozenset(), frozenset({"writer"}))
        for tasks in ((task(), task()), tuple(task(f"t{i}") for i in range(501))):
            with self.assertRaises(ValueError):
                self.publish(tasks=tasks)
        with self.assertRaises(ValueError):
            project_task(task(branch="x" * 401), NOW.isoformat())

    def test_total_storage_budget_is_checked_before_publication(self):
        rows = []
        for i in range(500):
            row = task(f"t{i}", branch="x" * 400)
            row["stages"] = [dict(row["stages"][0], id=f"s{j}", name="n" * 400) for j in range(20)]
            rows.append(row)
        with self.assertRaisesRegex(ValueError, "snapshot_too_large"):
            self.publish(tasks=tuple(rows), expected_revision=1, observed_at=NOW.isoformat())
        self.assertEqual(self.sql("SELECT revision FROM gui_smoke_snapshots"), [(1,)])

    def test_real_loopback_http_only_reads_authorized_saved_tasks(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server("127.0.0.1", 0, self.app, handler_class=Quiet)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", BASE + "/tasks/task-1", headers={"Authorization": "Bearer synthetic-alice"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["data"]["task"]["id"], "task-1")
            connection.request("POST", BASE + "/tasks", headers={"Authorization": "Bearer synthetic-writer"})
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(5)


if __name__ == "__main__":
    unittest.main()
