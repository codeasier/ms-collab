# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""All data/verifiers are synthetic. No agent process, network provider or secret."""
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Thread
from packages.workspaces.tests.identity_fixture import simulate_identity
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.assistant_history import AssistantHistoryApplication, DisplayEvent, project_observation, initialize, create_conversation, append_events
from packages.assistant_history.store import audit
from packages.identity import AuthenticationError, Provider, VerifiedIdentity
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, SQLiteWorkspaceService, PolicyError, StorageError

ISSUER="https://identity.example.invalid"
NOW=datetime(2026,1,2,tzinfo=timezone.utc)
BASE="/api/workspaces/alpha/assistant/conversations"


class TestVerifier:
    def verify(self, credential):
        if credential.value not in {"test-alice","test-bob","test-admin","test-operator","test-reader","test-replacement"}:raise AuthenticationError()
        return VerifiedIdentity(ISSUER,credential.value[5:],"history-tests",NOW,NOW+timedelta(seconds=60))


def request(app, suffix="", *, base=BASE, token="test-alice", method="GET", query="", body=None, revision=None, raw=None, extra=None):
    payload=raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    environ={"REQUEST_METHOD":method,"PATH_INFO":base+suffix,"QUERY_STRING":query,"CONTENT_TYPE":"application/json","CONTENT_LENGTH":str(len(payload)),"wsgi.input":io.BytesIO(payload)}
    if token is not None:environ["HTTP_AUTHORIZATION"]="Bearer "+token
    if revision is not None:environ["HTTP_IF_MATCH"]=f'"{revision}"'
    environ.update(extra or {});result={}
    def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
    result["body"]=json.loads(b"".join(app(environ,start)))
    return result


class HistoryTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/"policy.sqlite3"
        people=("alice","bob","admin","operator","reader")
        self.store=SQLiteWorkspaceService.initialize(self.path,
            principals=tuple(Principal(p,ISSUER,p) for p in people),
            workspaces=tuple(Workspace(w,w,"shared" if w=="commons" else "product") for w in ("alpha","beta","commons")),
            memberships=tuple(Membership(w,p,"admin" if p=="admin" else "viewer" if p=="reader" else "developer") for w in ("alpha","beta","commons") for p in ("alice","bob","admin","reader")),
            modules=tuple(ModuleInstance(w,m,("read","write")) for w in ("alpha","beta","commons") for m in ("assistant","pipelines")),operators=frozenset({"operator"}))
        initialize(self.store);self.now=NOW
        self.app=AssistantHistoryApplication(store=self.store,providers=(Provider("test",ISSUER,"history-tests",TestVerifier()),),provider_key="test",clock=lambda:self.now)
        self.create()

    def create(self, workspace="alpha", conversation="one", owner="alice", required_modules=("assistant",), title="Synthetic saved conversation"):
        return create_conversation(self.store,owner=owner,workspace=workspace,conversation=conversation,title=title,required_modules=required_modules,clock=lambda:NOW)

    def append(self, events=None, **kwargs):
        args=dict(owner="alice",workspace="alpha",conversation="one",events=events or (DisplayEvent("assistant_message","Synthetic answer"),),expected_revision=1,clock=lambda:NOW)
        args.update(kwargs);return append_events(self.store,**args)

    def lifecycle(self,state="archived",revision=1,**kwargs):
        return request(self.app,"/one",method="PATCH",body={"state":state},revision=revision,**kwargs)

    def sql(self,statement,values=()):
        with sqlite3.connect(self.path) as c:
            return simulate_identity(c,statement,values) if statement.startswith("UPDATE principals SET ") else c.execute(statement,values).fetchall()

    def test_legacy_fence_is_retained_but_never_upgraded_to_current_authority(self):
        fence = json.loads(self.sql("SELECT fence FROM assistant_conversations")[0][0])
        fence.pop('principal_generation'); fence.pop('policy_realm')
        self.sql("UPDATE assistant_conversations SET fence=?", (json.dumps(fence),))
        before = self.sql("SELECT * FROM assistant_conversations")
        self.assertEqual(request(self.app, "/one")["status"], 404)
        self.assertEqual(request(self.app)["body"]["data"]["items"], [])
        with self.assertRaises(PolicyError): self.append()
        self.assertEqual(self.sql("SELECT * FROM assistant_conversations"), before)

    def test_foreign_realm_fence_is_not_adopted(self):
        fence = json.loads(self.sql("SELECT fence FROM assistant_conversations")[0][0])
        fence['policy_realm'] = '0' * 32
        self.sql("UPDATE assistant_conversations SET fence=?", (json.dumps(fence),))
        self.assertEqual(request(self.app, "/one")["status"], 404)

    def test_explicit_owner_and_workspace_on_every_read(self):
        self.append()
        for suffix in ("/one","/one/events"):
            for token in ("test-bob","test-admin","test-operator","test-reader"):
                self.assertIn(request(self.app,suffix,token=token)["status"],(403,404))
            self.assertEqual(request(self.app,suffix,base=BASE.replace("alpha","beta"))["status"],404)
        self.assertEqual(request(self.app,token="test-bob")["body"]["data"]["items"],[])
        self.assertEqual(request(self.app,"/one/events")["status"],200)

    def test_anonymous_cookies_and_forged_identity_are_not_credentials(self):
        for extra in ({},{"HTTP_COOKIE":"subject=alice"},{"HTTP_X_USER":"alice"}):
            self.assertEqual(request(self.app,"/one",token=None,extra=extra)["status"],401)
        self.assertEqual(request(self.app,"/one",token="invalid")["status"],401)

    def test_public_metadata_omits_authority_and_native_locators(self):
        data=request(self.app,"/one")["body"]["data"]
        self.assertNotIn("fence",data);self.assertNotIn("owner_id",data)
        self.assertEqual(data["capabilities"],dict(events=True,archive=True,restore=False,execute=False,resume=False,export=False))
        self.assertNotIn(ISSUER,json.dumps(data))
        self.assertEqual(request(self.app,"/one")["headers"]["Cache-Control"],"no-store")

    def test_typed_events_preserve_order_without_executing_or_inventing_content(self):
        events=(DisplayEvent("user_message","Question"),DisplayEvent("agent_thought","Explicit user-visible upstream thought"),DisplayEvent("tool","Reported summary",tool_name="synthetic.read",status="completed"),DisplayEvent("assistant_message","<img src=x onerror=alert(1)>"),DisplayEvent("notice","Stored display only"))
        self.append(events)
        data=request(self.app,"/one/events")["body"]["data"]
        self.assertEqual([e["kind"] for e in data["items"]],[e.kind for e in events])
        self.assertEqual([e["sequence"] for e in data["items"]],[1,2,3,4,5])
        self.assertFalse(data["execution_enabled"])
        self.assertEqual(data["items"][3]["text"],events[3].text)

    def test_projection_requires_explicit_audience_and_rejects_raw_objects(self):
        self.assertIsNone(project_observation(dict(kind="notice",text="Hidden",audience=["assistant"])))
        self.assertEqual(project_observation(dict(kind="notice",text="Visible",audience=["user"])).text,"Visible")
        for value in ({"kind":"notice","text":"No audience"},{"kind":"tool","text":"x","audience":["user"],"rawOutput":{"token":"synthetic"}},{"kind":"notice","text":"x","audience":[[]]}):
            with self.assertRaises(ValueError):project_observation(value)

    def test_display_event_validation_has_no_arbitrary_metadata(self):
        for args in (("unknown","x"),("notice",""),("tool","x","tool","successful"),("notice","x","tool",None),([],"x"),("notice","\ud800"),("notice","x"*8193)):
            with self.assertRaises(ValueError):DisplayEvent(*args)
        with self.assertRaises(ValueError):DisplayEvent.from_dict({"kind":"notice","text":"x","locator":"private"})

    def test_pagination_is_revision_fenced_and_positions_are_not_authority(self):
        self.append(tuple(DisplayEvent("notice",f"event {i}") for i in range(53)))
        first=request(self.app,"/one/events",query="limit=50")["body"]["data"]
        self.assertEqual((len(first["items"]),first["next_after"],first["total"]),(50,50,53))
        self.assertEqual(request(self.app,"/one/events",query="after=50&limit=50")["status"],428)
        last=request(self.app,"/one/events",query="after=50&limit=50&revision=2")["body"]["data"]
        self.assertEqual([e["sequence"] for e in last["items"]],[51,52,53]);self.assertIsNone(last["next_after"])
        self.append(expected_revision=2)
        self.assertEqual(request(self.app,"/one/events",query="after=50&revision=2")["status"],412)
        self.assertEqual(request(self.app,"/one/events",query="after=50&revision=3",token="test-bob")["status"],404)

    def test_gets_are_side_effect_free_and_empty_is_not_permission_to_replay(self):
        before=self.sql("SELECT * FROM assistant_conversations"),self.sql("SELECT * FROM audit")
        for _ in range(3):
            self.assertEqual(request(self.app,"/one/events")["body"]["data"]["items"],[])
            request(self.app);request(self.app,"/one")
        self.assertEqual(before,(self.sql("SELECT * FROM assistant_conversations"),self.sql("SELECT * FROM audit")))

    def test_reversible_archive_is_metadata_only_and_does_not_replay(self):
        self.append();prior=self.sql("SELECT * FROM assistant_events")
        data=self.lifecycle(revision=2)["body"]["data"]
        self.assertEqual((data["state"],data["revision"]),("archived",3))
        self.assertEqual(request(self.app)["body"]["data"]["items"],[])
        self.assertEqual(len(request(self.app,query="state=archived")["body"]["data"]["items"]),1)
        self.assertEqual(request(self.app,"/one/events")["status"],200)
        with self.assertRaisesRegex(PolicyError,"conversation_archived"):self.append(expected_revision=3)
        self.assertEqual(self.lifecycle("active",3)["status"],200)
        self.assertEqual(self.sql("SELECT * FROM assistant_events"),prior)

    def test_lifecycle_noop_requires_current_revision_and_does_not_write_audit(self):
        audit_before=self.sql("SELECT * FROM audit")
        self.assertEqual(self.lifecycle("active",1)["status"],200)
        self.assertEqual(self.sql("SELECT * FROM audit"),audit_before)
        self.assertEqual(self.lifecycle("active",2)["status"],412)
        self.assertEqual(self.lifecycle(revision=None)["status"],428)

    def test_foreign_owner_cannot_archive_even_as_admin_or_operator(self):
        for token in ("test-admin","test-bob","test-operator"):
            self.assertIn(self.lifecycle(token=token)["status"],(403,404))
        self.assertEqual(request(self.app,"/one")["body"]["data"]["state"],"active")

    def test_two_writers_cas_only_one_wins(self):
        barrier=Barrier(2);results=[]
        def writer():barrier.wait();results.append(self.lifecycle()["status"])
        threads=[Thread(target=writer) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(5)
        self.assertEqual(sorted(results),[200,412])

    def test_member_demotion_invalidates_entire_history_and_labels(self):
        self.append()
        self.store.set_membership("admin","alpha","alice",role="viewer",state="active",expected_revision=1)
        self.assertEqual(request(self.app,"/one")["status"],404)
        self.assertEqual(request(self.app)["body"]["data"]["items"],[])
        with self.assertRaises(PolicyError):self.append(expected_revision=2)

    def test_revoked_member_and_inactive_principal_are_denied(self):
        self.store.set_membership("admin","alpha","alice",role="developer",state="revoked",expected_revision=1)
        self.assertEqual(request(self.app,"/one/events")["status"],404)
        self.sql("UPDATE principals SET active=0 WHERE id='alice'")
        self.assertEqual(request(self.app,"/one")["status"],401)

    def test_dependency_disable_and_reenable_never_resurrect_history(self):
        self.create(conversation="dependent",required_modules=("assistant","pipelines"))
        self.store.set_module_state("admin","alpha","pipelines",state="disabling",expected_revision=1)
        self.assertEqual(request(self.app,"/dependent")["status"],409)
        self.assertEqual([x["id"] for x in request(self.app)["body"]["data"]["items"]],["one"])
        self.store.set_module_state("admin","alpha","pipelines",state="disabled",expected_revision=2)
        self.store.set_module_state("admin","alpha","pipelines",state="enabled",expected_revision=3)
        self.assertEqual(request(self.app,"/dependent")["status"],404)

    def test_workspace_suspend_recovery_keeps_old_scope_fence_invalid(self):
        self.store.set_workspace_state("operator","alpha",state="suspended",expected_revision=1)
        self.assertEqual(request(self.app,"/one")["status"],409)
        self.store.set_workspace_state("operator","alpha",state="active",expected_revision=2)
        self.assertEqual(request(self.app,"/one")["status"],404)

    def test_subject_rebinding_cannot_inherit_saved_records(self):
        self.sql("UPDATE principals SET subject='replacement' WHERE id='alice'")
        self.assertEqual(request(self.app,"/one")["status"],401)
        self.assertEqual(request(self.app,"/one",token="test-replacement")["status"],404)
        self.assertEqual(request(self.app,token="test-replacement")["body"]["data"]["items"],[])
        self.sql("UPDATE principals SET subject='alice',issuer='https://other.invalid' WHERE id='alice'")
        self.assertEqual(request(self.app,"/one")["status"],401)

    def test_simulated_binding_aba_and_reactivation_never_restore_history(self):
        # Production mutations are closed. Simulate future transitions only in
        # this disposable fixture, preserving all membership revisions.
        self.sql("UPDATE principals SET subject='replacement' WHERE id='alice'")
        self.assertEqual(request(self.app,"/one",token="test-replacement")["status"],404)
        self.sql("UPDATE principals SET subject='alice' WHERE id='alice'")
        self.assertEqual(request(self.app,"/one")["status"],404)
        self.sql("UPDATE principals SET active=0 WHERE id='alice'")
        self.assertEqual(request(self.app,"/one")["status"],401)
        self.sql("UPDATE principals SET active=1 WHERE id='alice'")
        self.assertEqual(request(self.app,"/one")["status"],404)

    def test_persisted_scope_rejects_cross_workspace_misplacement(self):
        self.append()
        self.sql("UPDATE assistant_events SET workspace_id='beta'")
        self.sql("UPDATE assistant_conversations SET workspace_id='beta'")
        self.store.set_membership("admin","alpha","alice",role="developer",state="revoked",expected_revision=1)
        response=request(self.app,"/one/events",base=BASE.replace("alpha","beta"))
        self.assertEqual(response["status"],503)
        self.assertNotIn("Synthetic",str(response))
        self.assertEqual(request(self.app,base=BASE.replace("alpha","beta"))["status"],503)

    def test_persisted_scope_rejects_renamed_or_reowned_records(self):
        self.append()
        self.sql("UPDATE assistant_events SET conversation_id='moved'")
        self.sql("UPDATE assistant_conversations SET id='moved'")
        self.assertEqual(request(self.app,"/moved/events")["status"],503)
        self.sql("UPDATE assistant_events SET conversation_id='one'")
        self.sql("UPDATE assistant_conversations SET id='one',owner_id='bob'")
        self.assertEqual(request(self.app,"/one",token="test-bob")["status"],503)

    def test_shared_workspace_is_not_public_or_cross_owner(self):
        self.create(workspace="commons")
        base=BASE.replace("alpha","commons")
        self.assertEqual(request(self.app,"/one",base=base)["status"],200)
        self.assertEqual(request(self.app,"/one",base=base,token="test-bob")["status"],404)
        self.assertEqual(request(self.app,"/one",base=base,token=None)["status"],401)

    def test_write_only_module_does_not_bypass_read_permission(self):
        self.sql("UPDATE modules SET can_read=0 WHERE workspace_id='alpha' AND key='assistant'")
        self.assertEqual(self.lifecycle()["status"],403)

    def test_unknown_routes_cannot_execute_resume_export_or_publish(self):
        for suffix in ("/one/execute","/one/resume","/one/export","/one/prompt","/one/events"):
            self.assertEqual(request(self.app,suffix,method="POST",body={})["status"],404)
        self.assertEqual(request(self.app,method="POST",body={})["status"],404)
        self.assertEqual(request(self.app,"/one",method="DELETE")["status"],404)

    def test_generic_resource_routes_do_not_expose_or_overwrite_sessions(self):
        app=WorkspaceApplication(store=self.store,providers=(Provider("test",ISSUER,"history-tests",TestVerifier()),),provider_key="test",clock=lambda:NOW)
        base="/api/workspaces/alpha/modules/assistant/resources"
        self.assertEqual(request(app,"/one",base=base)["status"],404)
        self.assertEqual(request(app,"/one",base=base,method="PUT",body={"text":"overwrite"},revision=1)["status"],404)
        self.assertEqual(request(app,base=base)["body"]["data"]["items"],[])

    def test_strict_queries_body_and_media(self):
        for query in ("after=0&after=1","limit=0","limit=101","revision=0","after=-1","owner=bob","after=2001","revision=9007199254740992"):
            self.assertEqual(request(self.app,"/one/events",query=query)["status"],400,query)
        for query in ("state=unknown","state=active&state=archived","owner=bob"):
            self.assertEqual(request(self.app,query=query)["status"],400,query)
        for raw in (b'{"state":"active","owner":"bob"}',b'{"state":true}',b'{"state":[]}',b'{"state":{}}',b'{"state":"active","state":"archived"}',b'{"state":"deleted"}'):
            self.assertEqual(request(self.app,"/one",method="PATCH",raw=raw,revision=1)["status"],400)
        self.assertEqual(self.lifecycle(extra={"CONTENT_TYPE":"text/plain"})["status"],415)

    def test_invalid_stored_event_fails_closed_without_echoing_payload(self):
        self.append()
        raw=json.dumps({"kind":"notice","text":"private-marker","locator":"private"})
        self.sql("UPDATE assistant_events SET event=?",(raw,));self.sql("UPDATE assistant_conversations SET event_bytes=?",(len(raw),))
        response=request(self.app,"/one/events")
        self.assertEqual(response["status"],503);self.assertNotIn("private-marker",str(response))

    def test_missing_sequence_and_bad_counts_are_storage_failures(self):
        self.append((DisplayEvent("notice","one"),DisplayEvent("notice","two")))
        self.sql("DELETE FROM assistant_events WHERE sequence=1")
        self.assertEqual(request(self.app,"/one/events")["status"],503)

    def test_degraded_schema_is_rejected_on_setup_and_read(self):
        self.sql("ALTER TABLE assistant_events RENAME TO discarded_events")
        self.sql("CREATE TABLE assistant_events (workspace_id TEXT,conversation_id TEXT,sequence INTEGER,event TEXT)")
        with self.assertRaises(StorageError):initialize(self.store)
        self.assertEqual(request(self.app,"/one")["status"],503)

    def test_append_rolls_back_events_revision_and_audit_on_failure(self):
        before=self.sql("SELECT * FROM assistant_conversations"),self.sql("SELECT * FROM audit")
        self.sql("CREATE TRIGGER fail_assistant_update BEFORE UPDATE ON assistant_conversations BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(StorageError):self.append()
        self.assertEqual(self.sql("SELECT * FROM assistant_events"),[])
        self.assertEqual(before,(self.sql("SELECT * FROM assistant_conversations"),self.sql("SELECT * FROM audit")))

    def test_credential_expiry_rolls_back_lifecycle_and_audit(self):
        calls=[]
        def expire_after_audit(*args):
            result=audit(*args)
            calls.append(1)
            self.now=NOW+timedelta(seconds=61)
            return result
        before=self.sql("SELECT * FROM audit")
        with patch("packages.assistant_history.application.audit",side_effect=expire_after_audit):
            self.assertEqual(self.lifecycle()["status"],401)
        self.assertEqual(calls,[1])  # The mutation and audit ran before expiration.
        self.assertEqual(self.sql("SELECT state,revision FROM assistant_conversations"),[("active",1)])
        self.assertEqual(self.sql("SELECT * FROM audit"),before)

    def test_ingress_limits_and_schema_never_import_external_records(self):
        with self.assertRaises(PolicyError):self.create()
        with self.assertRaises(ValueError):self.create(conversation="other",required_modules=())
        with self.assertRaises(ValueError):self.append(events=(DisplayEvent("notice","x"),)*101)
        with self.assertRaises(PolicyError):self.create(conversation="viewer",owner="reader")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM assistant_conversations"),[(1,)])

    def test_immutable_event_budget_is_fail_closed(self):
        events=tuple(DisplayEvent("notice","x"*8192) for _ in range(100))
        self.append(events)
        with self.assertRaisesRegex(PolicyError,"conversation_limit"):self.append(events,expected_revision=2)
        self.assertEqual(self.sql("SELECT event_count,revision FROM assistant_conversations"),[(100,2)])

    def test_real_http_reads_saved_history_and_metadata_only_patch(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server("127.0.0.1",0,self.app,handler_class=Quiet)
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            conn.request("GET",BASE+"/one",headers={"Authorization":"Bearer test-alice"})
            response=conn.getresponse();data=json.loads(response.read());self.assertEqual(response.status,200)
            conn.request("PATCH",BASE+"/one",body='{"state":"archived"}',headers={"Authorization":"Bearer test-alice","Content-Type":"application/json","If-Match":f'"{data["data"]["revision"]}"'})
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())["data"]["state"],"archived");conn.close()
        finally:server.shutdown();server.server_close();worker.join(5)


if __name__ == "__main__":unittest.main()
