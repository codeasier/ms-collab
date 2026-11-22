"""Synthetic provider -> authorized publication -> API -> browser, loopback only."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from socketserver import ThreadingMixIn
import tempfile
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.pipeline_api import PipelineApplication, encode_snapshots, resource_id
from packages.pipeline_sync import BoundCredential, Connection, PipelineSynchronizer, connection_record, connection_resource_id
from packages.pipelines import PipelineScope
from packages.pipeline_ui import PipelineUI
from packages.workspaces import SQLiteWorkspaceService, Principal, Workspace, Membership, ModuleInstance, Resource

ISSUER="https://identity.example.invalid"
NOW=datetime(2026,1,2,tzinfo=timezone.utc)


class TestVerifier:
    def verify(self, credential):
        subject={"synthetic-reader":"reader","synthetic-admin":"admin"}.get(credential.value)
        if subject is None:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER,subject,"pipeline-ui",NOW,NOW+timedelta(seconds=60))


class TestProvider:
    def get(self,path,credential,connection):
        scope=connection.scope
        if "?limit=" in path:
            return {"items":[] if scope.workspace_id=="empty" else [
                {"id":f"run-{n:02d}","pipeline_id":scope.pipeline_id} for n in range(1,21)]}
        run_id=path.rsplit("/",1)[1]
        run={"id":run_id,"pipeline_id":scope.pipeline_id,"status":"running" if run_id=="run-19" else "failure","stages":None}
        if run_id in {"run-18","run-20"}:
            run["stages"]=[{"name":"<b>Checks</b>","jobs":[
                {"id":"unit","name":"<img src=x onerror=alert(1)>","status":"success",
                 "started_at":"2026-01-01T10:00:00Z","finished_at":"2026-01-01T10:00:12Z"},
                {"id":"lint","name":"Lint","status":"failure","started_at":None,"finished_at":None}]}]
        if scope.workspace_id=="commons" and run_id in {"run-17","run-18","run-20"}:
            # A real, bounded synthetic publication with a slow middle sample.
            minutes,seconds={"run-17":(0,4),"run-18":(2,19),"run-20":(8,7)}[run_id]
            run["stages"]=[{"name":"Build","jobs":[{"id":"unit","name":"Unit tests","status":"success",
                "started_at":f"2026-01-01T10:{minutes:02d}:00Z",
                "finished_at":f"2026-01-01T10:{minutes:02d}:{seconds:02d}Z"}]}]
        return run


class Quiet(WSGIRequestHandler):
    def log_message(self,*args): pass


class Threaded(ThreadingMixIn,WSGIServer):
    daemon_threads=True


def make_api(path):
    spaces=("alpha","commons","beta","empty")
    connections=tuple(Connection(PipelineScope(w,"example-ci","build"),"https://adapter.example.invalid",run_limit=20) for w in spaces)
    resources=[]
    for c in connections:
        resources.extend((Resource(c.scope.workspace_id,"pipelines",resource_id(c.scope),encode_snapshots(c.scope,[])),
                          Resource(c.scope.workspace_id,"pipeline_connections",connection_resource_id(c.scope),connection_record(c))))
    store=SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p,ISSUER,p) for p in ("admin","reader")),
        workspaces=tuple(Workspace(w,w,"shared" if w=="commons" else "product") for w in spaces),
        memberships=tuple([Membership(w,"admin","admin") for w in spaces]+[Membership(w,"reader","viewer") for w in spaces if w!="beta"]),
        modules=tuple(m for w in spaces for m in (ModuleInstance(w,"pipelines",("read","write")),ModuleInstance(w,"pipeline_connections",("read",)))),resources=tuple(resources))
    providers=(Provider("test",ISSUER,"pipeline-ui",TestVerifier()),)
    sync=PipelineSynchronizer(store=store,providers=providers,provider_key="test",clock=lambda:NOW,transport=TestProvider())
    for c in connections:
        sync.refresh(user_credential=Credential("synthetic-admin"),connection=c,
                     upstream_credential=BoundCredential(c.scope,c.origin,"synthetic-upstream-token-for-tests-only"),expected_revision=1)
    return PipelineApplication(store=store,providers=providers,provider_key="test",clock=lambda:NOW)


if __name__=="__main__":
    with tempfile.TemporaryDirectory() as directory:
        api=make_api(Path(directory)/"synthetic.sqlite3")
        server=make_server("127.0.0.1",0,api,server_class=Threaded,handler_class=Quiet)
        origin=f"http://127.0.0.1:{server.server_port}"
        server.set_app(PipelineUI(api=api,origin=origin));print(origin,flush=True)
        try:server.serve_forever()
        finally:server.server_close()
