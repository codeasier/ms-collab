"""Isolated synthetic acceptance harness ONLY, never production configuration."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from socketserver import ThreadingMixIn
import tempfile
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer
from packages.artifacts import Artifact, Definition, SavedView, Scope, Snapshot
from packages.artifacts_api import ArtifactApplication, catalog_resource, view_resource
from packages.artifacts_ui import ArtifactBrowserUI
from packages.identity import AuthenticationError, Provider, VerifiedIdentity
from packages.workspaces import Membership, ModuleInstance, Principal, SQLiteWorkspaceService, Workspace
ISSUER = "https://identity.example.invalid"
class TestVerifier:
    def verify(self, credential):
        subject = {"synthetic-reader": "reader", "synthetic-outsider": "outsider"}.get(credential.value)
        if subject is None: raise AuthenticationError()
        now = datetime.now(timezone.utc)
        return VerifiedIdentity(ISSUER, subject, "artifact-ui-tests", now, now + timedelta(minutes=5))
def configured_api(path):
    resources=[]
    for w in ("alpha", "commons", "hidden"):
        scope=Scope(w,"example-storage","releases")
        rows=[Artifact(f"builds/package-{n:02d}.zip","2026-01-02T12:00:00Z",1000+n) for n in range(12)]
        rows.extend((Artifact("builds/<svg onload=alert(1)>.txt","2026-01-02T12:00:00Z",42),Artifact("notes.txt","2026-01-02T12:00:00Z",64),Artifact("history/old.zip","2025-10-02T12:00:00Z",128)))
        if w=="commons":rows=[Artifact("shared-guide.txt","2026-01-02T12:00:00Z",88)]
        resources.extend((catalog_resource(Snapshot(scope,"2026-01-03T00:00:00Z",tuple(rows))),view_resource(SavedView(scope,"packages","Packages",Definition(path="builds/",files_only=True)))))
    store=SQLiteWorkspaceService.initialize(path,
        principals=(Principal("reader",ISSUER,"reader"),Principal("outsider",ISSUER,"outsider")),
        workspaces=(Workspace("alpha","Product Alpha"),Workspace("commons","Shared collaboration","shared"),Workspace("hidden","Hidden")),
        memberships=(Membership("alpha","reader","admin"),Membership("commons","reader","admin"),Membership("hidden","outsider","admin")),
        modules=tuple(ModuleInstance(w,"artifacts",("read",)) for w in ("alpha","commons","hidden")),resources=tuple(resources),operators=frozenset())
    return ArtifactApplication(store=store,providers=(Provider("test",ISSUER,"artifact-ui-tests",TestVerifier()),),provider_key="test")
class Quiet(WSGIRequestHandler):
    def log_message(self,*args):pass
class Threaded(ThreadingMixIn,WSGIServer):
    daemon_threads=True
if __name__=="__main__":
    with tempfile.TemporaryDirectory() as directory:
        api=configured_api(Path(directory)/"synthetic.sqlite3")
        server=make_server("127.0.0.1",0,api,server_class=Threaded,handler_class=Quiet)
        origin=f"http://127.0.0.1:{server.server_port}"
        server.set_app(ArtifactBrowserUI(api=api,origin=origin));print(origin,flush=True)
        try:server.serve_forever()
        finally:server.server_close()
