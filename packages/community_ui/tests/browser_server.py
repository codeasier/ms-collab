"""Loopback-only synthetic browser fixture, never deployment configuration."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer
from socketserver import ThreadingMixIn

from packages.identity import AuthenticationError, Provider, VerifiedIdentity
from packages.community_api import CommunityApplication, ConnectionPolicy, initialize
from packages.community_api.records import read, encode, fingerprint, snapshot_data
from packages.community_sync import Connection, BoundCredential
from packages.community_board import Scope, Snapshot, BoardItem
from packages.community_ui import CommunityUI
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, SQLiteWorkspaceService

ISSUER = "https://identity.example.invalid"


class TestVerifier:
    def verify(self, credential):
        subject = {"synthetic-admin":"admin", "synthetic-reader":"reader", "synthetic-reviewer":"reviewer", "synthetic-refresh":"refresher"}.get(credential.value)
        if subject is None:
            raise AuthenticationError()
        now = datetime.now(timezone.utc)
        return VerifiedIdentity(ISSUER, subject, "ui-tests", now, now + timedelta(seconds=60))


class TestProvider:
    def observe(self, connection, credential, *, observed_at):
        # Refresh advances every changed record version; old fingerprints must not survive.
        return Snapshot(connection.scope, observed_at, tuple(
            BoardItem("issue", number, "Changed synthetic issue" if number == 101 else f"Synthetic issue {number}", "open", observed_at, ("fixture",))
            for number in range(1,102)))


class Quiet(WSGIRequestHandler):
    def log_message(self, *args): pass


class Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def make_api(path):
    people = ("admin", "reader", "reviewer", "refresher")
    scopes = tuple(Scope(w, "source", "sample/project") for w in ("alpha", "commons", "empty"))
    store = SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p, ISSUER, p) for p in people),
        workspaces=tuple(Workspace(w, w, "shared" if w == "commons" else "product") for w in ("alpha", "commons", "empty")),
        memberships=tuple(Membership(w,p,"viewer" if p == "reader" else "admin") for w in ("alpha", "commons", "empty") for p in people),
        modules=tuple(ModuleInstance(w,"community",("read","write")) for w in ("alpha", "commons", "empty")))
    initialize(store, tuple(ConnectionPolicy(Connection(s), "synthetic-ref", ("admin","reader"), ("admin","refresher"), ("admin","reviewer")) for s in scopes))
    def seed(policy, connection):
        for scope in scopes[:2]:
            items=tuple(BoardItem("issue",n,"<img src=x onerror=alert(1)>" if n == 101 else f"Synthetic issue {n}","open","2026-01-01T00:00:00Z",("<b>plain label</b>",)) for n in range(1,102))
            snapshot=Snapshot(scope,"2026-01-02T00:00:00Z",items)
            approvals=[fingerprint(scope,item) for item in items if item.number in (1,101)]
            connection.execute("UPDATE community_connections SET snapshot=?,approvals=? WHERE workspace_id=?",(encode(snapshot_data(snapshot)),encode(approvals),scope.workspace_id))
    store._transaction(seed,write=True,with_connection=True)
    return CommunityApplication(store=store,providers=(Provider("test",ISSUER,"ui-tests",TestVerifier()),),provider_key="test",provider=TestProvider(),credential_loader=lambda scope,ref: BoundCredential(scope,"synthetic-provider"))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        api=make_api(Path(directory)/"ui.sqlite3")
        server=make_server("127.0.0.1",0,api,server_class=Threaded,handler_class=Quiet)
        origin=f"http://127.0.0.1:{server.server_port}"
        server.set_app(CommunityUI(api=api,origin=origin));print(origin,flush=True)
        try: server.serve_forever()
        finally: server.server_close()
