# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import http.client
from threading import Thread
from wsgiref.simple_server import make_server, WSGIRequestHandler
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.community_api import CommunityApplication, ConnectionPolicy, initialize, replace_policy
from packages.community_api.records import read, fingerprint
from packages.community_board.model import Scope, Snapshot, BoardItem
from packages.community_sync import BoundCredential, Connection
from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import Membership, ModuleInstance, Principal, SQLiteWorkspaceService, Workspace, StorageError

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
ISSUER = "https://identity.example.invalid"
BASE = "/api/workspaces/alpha/community/source/sample/project"
SCOPE = Scope("alpha", "source", "sample/project")


class TestVerifier:
    def __init__(self):
        self.hook = lambda: None
    def verify(self, credential):
        self.hook()
        if type(credential) is not Credential or credential.value not in {"test-admin", "test-second", "test-reader", "test-dev", "test-outsider", "test-disabled"}:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, credential.value[5:], "community-test", NOW, NOW + timedelta(seconds=60))


class TestProvider:
    def __init__(self):
        self.calls = 0
        self.hook = lambda: None
        self.title = "Synthetic issue"
        self.updated = "2026-01-01T00:00:00Z"
        self.scope = None
    def observe(self, connection, credential, *, observed_at, before_read=None):
        if before_read is not None: before_read()
        self.calls += 1
        self.hook()
        return Snapshot(self.scope or connection.scope, observed_at, (
            BoardItem("issue", 1, self.title, "open", self.updated, ("test",)),
            BoardItem("pull", 2, "Synthetic pull", "merged", "2026-01-01T00:00:00Z")))


def config(scope=SCOPE):
    return ConnectionPolicy(Connection(scope, page_size=10, max_pages=1), "approved-secret-ref",
                            ("reader", "admin", "dev"), ("admin",), ("admin",))


def call(app, suffix="/items", *, base=BASE, method="GET", token="test-reader", body=None, query="", revision=None, extra=None, raw=None):
    payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    environ = {"REQUEST_METHOD":method, "PATH_INFO":base + suffix, "QUERY_STRING":query,
               "CONTENT_TYPE":"application/json", "CONTENT_LENGTH":str(len(payload)), "wsgi.input":io.BytesIO(payload)}
    if token is not None: environ["HTTP_AUTHORIZATION"] = "Bearer " + token
    if revision is not None: environ["HTTP_IF_MATCH"] = f'"{revision}"'
    environ.update(extra or {})
    result = {}
    def start(status, headers): result.update(status=int(status.split()[0]), headers=dict(headers))
    result["body"] = json.loads(b"".join(app(environ, start)))
    return result


class CommunityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "workspace.sqlite3"
        self.store = SQLiteWorkspaceService.initialize(self.path,
            principals=tuple(Principal(name, ISSUER, name, name != "disabled") for name in ("admin", "second", "reader", "dev", "outsider", "disabled")),
            workspaces=tuple(Workspace(w, w, "shared" if w == "commons" else "product") for w in ("alpha", "beta", "commons")),
            memberships=tuple(Membership(w, p, "admin") for w in ("alpha", "beta", "commons") for p in ("admin", "second")) +
                (Membership("alpha", "reader", "viewer"), Membership("alpha", "dev", "developer"), Membership("commons", "reader", "viewer"), Membership("alpha", "disabled", "viewer")),
            modules=tuple(ModuleInstance(w, "community", ("read", "write")) for w in ("alpha", "beta", "commons")))
        self.policies = [config(Scope(w, "source", "sample/project")) for w in ("alpha", "beta", "commons")]
        self.policies.append(config(Scope("alpha", "second", "sample/project")))
        initialize(self.store, self.policies)
        self.verifier = TestVerifier(); self.provider = TestProvider(); self.now = NOW
        self.load_calls = []
        def loader(scope, ref):
            self.load_calls.append((scope, ref))
            return BoundCredential(scope, "synthetic-provider-token")
        self.app = CommunityApplication(store=self.store, providers=(Provider("test", ISSUER, "community-test", self.verifier),),
            provider_key="test", credential_loader=loader, provider=self.provider, clock=lambda:self.now)

    def state(self, scope=SCOPE):
        return self.store._transaction(lambda p,c: read(c, scope), write=False, with_connection=True)

    def refresh(self, **kwargs):
        kwargs.setdefault("token", "test-admin"); kwargs.setdefault("body", {}); kwargs.setdefault("revision", self.state().revision)
        return call(self.app, "/refresh", method="POST", **kwargs)

    def approve(self, values=None, **kwargs):
        kwargs.setdefault("token", "test-admin"); kwargs.setdefault("revision", self.state().revision)
        if values is None:
            values = [fingerprint(SCOPE, item) for item in self.state().snapshot.items]
        kwargs.setdefault("body", {"fingerprints":values})
        return call(self.app, "/approve", method="POST", **kwargs)

    def test_capabilities_are_current_and_not_inferred_from_role(self):
        r=call(self.app,"/capabilities")["body"]["data"]
        self.assertEqual(r["capabilities"],dict(items=True,review=False,refresh=False,approve=False))
        r=call(self.app,"/capabilities",token="test-admin")["body"]["data"]
        self.assertTrue(all(r["capabilities"].values()))
        self.assertEqual(call(self.app,"/capabilities",token="test-second")["status"],404)
        replace_policy(self.store,actor_id="admin",config=replace(config(),reader_ids=()),expected_revision=1)
        r=call(self.app,"/capabilities",token="test-admin")["body"]["data"]
        self.assertFalse(r["capabilities"]["items"])
        self.assertTrue(r["capabilities"]["review"])
        self.assertEqual(call(self.app,"/capabilities")["status"],404)
        self.app._credential_loader=None
        self.assertFalse(call(self.app,"/capabilities",token="test-admin")["body"]["data"]["capabilities"]["refresh"])

    def test_empty_then_refresh_still_not_shared(self):
        result = call(self.app); self.assertFalse(result["body"]["data"]["available"])
        self.assertEqual(self.refresh()["status"], 200)
        data = call(self.app)["body"]["data"]
        self.assertEqual((data["total"], data["items"]), (0, []))
        self.assertNotIn("Synthetic issue", str(data))
        self.assertEqual(data["sharing"], "explicit_content_approval")

    def test_review_explicit_approval_and_filtered_board(self):
        self.refresh()
        review = call(self.app, "/review", token="test-admin")["body"]["data"]
        self.assertEqual(len(review["items"]), 2)
        self.assertTrue(all(not i["approved"] for i in review["items"]))
        self.assertEqual(self.approve()["status"], 200)
        result = call(self.app, query="kind=issue&limit=1")["body"]["data"]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Synthetic issue")
        self.assertNotIn("fingerprint", result["items"][0])
        self.assertEqual(call(self.app)["headers"]["Cache-Control"], "no-store")

    def test_changed_content_requires_reapproval(self):
        self.refresh(); self.approve()
        self.provider.title = "New confidential title"; self.provider.updated = "2026-01-02T00:00:01Z"
        self.now += timedelta(seconds=2)
        self.assertEqual(self.refresh()["status"], 200)
        data = call(self.app)["body"]["data"]
        self.assertEqual(data["total"], 1)
        self.assertNotIn("New confidential", str(data))
        self.assertEqual(self.state().revision, 4)

    def test_approval_removal_immediate(self):
        self.refresh(); self.approve(); self.approve([])
        self.assertEqual(call(self.app)["body"]["data"]["total"], 0)

    def test_shared_workspace_requires_own_approval_and_membership(self):
        self.refresh(); self.approve()
        public = BASE.replace("alpha", "commons")
        self.assertFalse(call(self.app, base=public)["body"]["data"]["available"])
        self.assertEqual(self.refresh(base=public, revision=1)["status"], 200)
        self.assertEqual(call(self.app, base=public)["body"]["data"]["total"], 0)
        self.assertEqual(call(self.app, base=public, token="test-outsider")["status"], 404)
        alpha_hash = next(iter(self.state().approvals))
        self.assertEqual(self.approve([alpha_hash], base=public, revision=2)["status"], 400)

    def test_exact_connection_and_repository(self):
        self.refresh(); self.approve()
        for base in (BASE.replace("/source/", "/missing/"), BASE.replace("/project", "/missing")):
            self.assertEqual(call(self.app, base=base)["status"], 404)
        self.assertFalse(call(self.app, base=BASE.replace("/source/", "/second/"))["body"]["data"]["available"])

    def test_unknown_and_unowned_workspace_indistinguishable(self):
        a = call(self.app, base=BASE.replace("alpha", "beta"))
        b = call(self.app, base=BASE.replace("alpha", "unknown"))
        self.assertEqual((a["status"],a["body"]), (b["status"],b["body"]))
        self.assertEqual(a["status"], 404)

    def test_role_and_explicit_connection_permission_both_required(self):
        for token in ("test-reader", "test-dev", "test-second"):
            self.assertNotEqual(self.refresh(token=token)["status"], 200)
            self.assertEqual(call(self.app, "/review", token=token)["status"], 404)
            self.assertNotEqual(self.approve([], token=token)["status"], 200)
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(self.load_calls, [])

    def test_binding_and_forged_claims(self):
        for token in (None, "unknown", "test-disabled"):
            r = call(self.app, token=token, extra={"REMOTE_USER":"admin", "HTTP_X_USER":"admin", "HTTP_COOKIE":"actor=admin"})
            self.assertEqual(r["status"], 401)
        self.assertEqual(self.refresh(body={"actor":"admin"})["status"], 400)
        self.assertEqual(call(self.app, query="actor=admin")["status"], 400)

    def test_revoke_reader_during_verification(self):
        self.refresh(); self.approve()
        self.verifier.hook = lambda:self.store.set_membership("admin", "alpha", "reader", role="viewer", state="revoked", expected_revision=1)
        self.assertEqual(call(self.app)["status"], 404)

    def test_revoke_admin_during_network_discards_observation(self):
        self.provider.hook = lambda:self.store.set_membership("second", "alpha", "admin", role="viewer", state="revoked", expected_revision=1)
        self.assertEqual(self.refresh()["status"], 404)
        self.assertIsNone(self.state().snapshot)
        self.assertEqual(self.state().revision, 1)

    def test_demotion_during_network_blocks_publish(self):
        self.provider.hook = lambda:self.store.set_membership("second", "alpha", "admin", role="developer", state="active", expected_revision=1)
        self.assertEqual(self.refresh()["status"], 403)
        self.assertIsNone(self.state().snapshot)

    def test_config_change_during_network_invalidates_refresh(self):
        self.provider.hook = lambda:replace_policy(self.store,actor_id="admin",config= replace(config(), credential_ref="new-ref"), expected_revision=1)
        self.assertEqual(self.refresh()["status"], 412)
        self.assertIsNone(self.state().snapshot)
        self.assertEqual(self.state().revision, 2)

    def test_audience_change_clears_prior_snapshot_and_approvals(self):
        self.refresh(); self.approve()
        replace_policy(self.store,actor_id="admin",config= replace(config(), reader_ids=("admin",)), expected_revision=3)
        self.assertIsNone(self.state().snapshot)
        self.assertEqual(self.state().approvals, frozenset())
        self.assertEqual(call(self.app)["status"], 404)

    def test_expiry_during_network_rolls_back(self):
        self.provider.hook = lambda:setattr(self, "now", NOW + timedelta(seconds=61))
        self.assertEqual(self.refresh()["status"], 401)
        self.assertIsNone(self.state().snapshot)

    def test_module_disabled_during_network_rejected(self):
        self.provider.hook = lambda:self.store.set_module_state("admin", "alpha", "community", state="disabling", expected_revision=1)
        self.assertEqual(self.refresh()["status"], 409)
        self.assertIsNone(self.state().snapshot)

    def test_stale_precondition_never_contacts_provider(self):
        self.refresh()
        self.assertEqual(self.refresh(revision=1)["status"], 412)
        self.assertEqual(self.provider.calls, 1)

    def test_concurrent_refresh_compare_and_swap(self):
        def overlap():
            self.provider.hook = lambda:None
            self.assertEqual(self.refresh(revision=1)["status"], 200)
        self.provider.hook = overlap
        self.assertEqual(self.refresh(revision=1)["status"], 412)
        self.assertEqual(self.state().revision, 2)
        self.assertEqual(len(self.store.audit_events("admin", "alpha")), 1)

    def test_approval_revision_race_and_unknown_digest(self):
        self.refresh()
        self.assertEqual(self.approve(["a"*64])["status"], 400)
        self.approve()
        self.assertEqual(self.approve([], revision=2)["status"], 412)
        self.assertEqual(len(self.state().approvals), 2)

    def test_wrong_scope_credential_or_provider_keeps_prior(self):
        self.app._credential_loader = lambda *_:BoundCredential(Scope("beta", "source", "sample/project"), "synthetic")
        self.assertEqual(self.refresh()["status"], 502)
        self.assertEqual(self.provider.calls, 0)
        self.app._credential_loader = lambda scope,_:BoundCredential(scope, "synthetic")
        self.provider.scope = Scope("beta", "source", "sample/project")
        self.assertEqual(self.refresh()["status"], 502)
        self.assertIsNone(self.state().snapshot)

    def test_provider_failure_safe_and_no_freshness_change(self):
        self.refresh(); old=self.state()
        def fail(): raise RuntimeError("synthetic-secret-private-response")
        self.provider.hook=fail
        r=self.refresh(); self.assertEqual(r["status"],502)
        self.assertNotIn("synthetic-secret",str(r)); self.assertEqual(self.state(),old)

    def test_stale_and_ambiguous_items_rejected(self):
        self.refresh(); old=self.state()
        self.now += timedelta(seconds=2); self.provider.title="Ambiguous same timestamp"
        self.assertEqual(self.refresh()["status"],409); self.assertEqual(self.state(),old)
        self.provider.updated="2025-01-01T00:00:00Z"
        self.assertEqual(self.refresh()["status"],409)

    def test_generic_resource_routes_cannot_read_or_write_community_data(self):
        self.refresh(); self.approve()
        generic=WorkspaceApplication(store=self.store,providers=(Provider("test",ISSUER,"community-test",self.verifier),),provider_key="test",clock=lambda:self.now)
        r=call(generic,suffix="",base="/api/workspaces/alpha/modules/community/resources")
        self.assertEqual(r["body"]["data"],{"total":0,"items":[]})
        r=call(generic,suffix="",base="/api/workspaces/alpha/modules/community/resources/source",method="PUT",token="test-dev",body={"text":"attacker"},revision=1)
        self.assertEqual(r["status"],404)
        self.assertEqual(self.state().revision,3)

    def test_missing_table_and_corrupt_json_fail_closed(self):
        with sqlite3.connect(self.path) as c:c.execute("UPDATE community_connections SET config='secret-broken' WHERE workspace_id='alpha'")
        r=call(self.app);self.assertEqual(r["status"],503);self.assertNotIn("secret",str(r))
        with sqlite3.connect(self.path) as c:c.execute("DROP TABLE community_connections")
        self.assertEqual(call(self.app)["status"],503)

    def test_sql_publication_failure_rolls_back_snapshot_and_audit(self):
        self.refresh(); old=self.state()
        with sqlite3.connect(self.path) as c:c.execute("CREATE TRIGGER fail_community AFTER UPDATE ON community_connections BEGIN SELECT RAISE(ABORT,'synthetic-secret'); END")
        r=self.approve();self.assertEqual(r["status"],503);self.assertNotIn("synthetic-secret",str(r))
        self.assertEqual(self.state(),old)
        self.assertEqual(len(self.store.audit_events("admin","alpha")),1)

    def test_expiry_before_commit_rolls_back_both_tables(self):
        self.refresh(); old=self.state(); original=self.store._persist
        def expire(*args,**kwargs):
            original(*args,**kwargs);self.now=NOW+timedelta(seconds=61)
        with patch.object(self.store,"_persist",side_effect=expire):
            self.assertEqual(self.approve()["status"],401)
        self.assertEqual(self.state(),old)
        self.assertEqual(len(self.store.audit_events("admin","alpha")),1)

    def test_body_route_and_filter_validation(self):
        for query in ("limit=0","offset=-1","state=locked","kind=no","query="+"x"*201,"limit=1&limit=2","unexpected=x"):
            self.assertEqual(call(self.app,query=query)["status"],400)
        self.assertEqual(call(self.app,"/refresh",method="POST",token="test-admin",body={})["status"],428)
        for body in ({"fingerprints":["no"]},{"fingerprints":["a"*64,"a"*64]},{"fingerprints":[],"actor":"admin"}):
            self.assertEqual(self.approve([],body=body)["status"],400)
        self.assertEqual(self.refresh(body={"token":"not-accepted"})["status"],400)
        self.assertEqual(call(self.app,"/items",method="PUT")["status"],404)

    def test_archive_suspension_and_missing_read_capability(self):
        self.refresh(); self.approve()
        with sqlite3.connect(self.path) as c:c.execute("UPDATE workspaces SET state='archived' WHERE id='alpha'")
        self.assertEqual(call(self.app)["status"],200)
        self.assertEqual(self.refresh()["status"],409)
        with sqlite3.connect(self.path) as c:c.execute("UPDATE workspaces SET state='suspended' WHERE id='alpha'")
        self.assertEqual(call(self.app)["status"],409)
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE workspaces SET state='active' WHERE id='alpha'")
            c.execute("UPDATE modules SET can_read=0 WHERE workspace_id='alpha'")
        self.assertEqual(call(self.app)["status"],403)

    def test_configured_developer_still_cannot_refresh_or_approve(self):
        replace_policy(self.store,actor_id="admin",config=replace(config(),refresher_ids=("dev",),approver_ids=("dev",)),expected_revision=1)
        self.assertEqual(self.refresh(token="test-dev")["status"],403)
        self.assertEqual(self.approve([],token="test-dev")["status"],403)
        self.assertEqual(self.provider.calls,0)

    def test_content_and_scope_fingerprints_cannot_be_reused(self):
        self.refresh(); self.approve()
        other=Scope("alpha","second","sample/project")
        raw=self.state().snapshot.items[0]
        self.assertNotEqual(fingerprint(SCOPE,raw),fingerprint(other,raw))
        with sqlite3.connect(self.path) as c:
            text=c.execute("SELECT snapshot FROM community_connections WHERE workspace_id='alpha' AND connection_id='source'").fetchone()[0]
            c.execute("UPDATE community_connections SET snapshot=? WHERE workspace_id='alpha' AND connection_id='source'",(text.replace('Synthetic issue','Tampered title'),))
        self.assertEqual(call(self.app)["status"],503)

    def test_oversized_observation_preserves_prior(self):
        self.refresh();old=self.state()
        original=self.provider.observe
        def oversized(*args,**kwargs):
            observed=original(*args,**kwargs)
            return Snapshot(SCOPE,observed.observed_at,tuple(BoardItem("issue",i,"x"*1000,"open","2026-01-01T00:00:00Z") for i in range(1,1500)))
        self.provider.observe=oversized
        self.assertEqual(self.refresh()["status"],502)
        self.assertEqual(self.state(),old)

    def test_real_loopback_http_uses_same_authorized_projection(self):
        self.refresh();self.approve()
        class Quiet(WSGIRequestHandler):
            def log_message(self,*_):pass
        server=make_server("127.0.0.1",0,self.app,handler_class=Quiet)
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            client=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3)
            client.request("GET",BASE+"/items?kind=issue",headers={"Authorization":"Bearer test-reader"})
            response=client.getresponse();data=json.loads(response.read());client.close()
            self.assertEqual(response.status,200)
            self.assertEqual(data["data"]["items"][0]["title"],"Synthetic issue")
        finally:
            server.shutdown();server.server_close();worker.join(3)

    def test_no_raw_credentials_in_database(self):
        self.refresh()
        self.assertNotIn(b"synthetic-provider-token",self.path.read_bytes())
        self.assertNotIn(b"test-admin",self.path.read_bytes())
        self.assertEqual(self.load_calls,[(SCOPE,"approved-secret-ref")])

    def test_reinitialize_never_resets_data(self):
        self.refresh()
        with self.assertRaises(StorageError):initialize(self.store,self.policies)
        self.assertEqual(self.state().revision,2)

    def test_unconfigured_refresh_is_explicit(self):
        self.app._credential_loader=None
        self.assertEqual(self.refresh()["status"],503)
        self.assertEqual(self.provider.calls,0)


if __name__ == "__main__": unittest.main()
