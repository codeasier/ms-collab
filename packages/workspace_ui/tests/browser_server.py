"""Test harness ONLY. Never import this verifier into deployment configuration."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer
from socketserver import ThreadingMixIn

from packages.identity import AuthenticationError, Provider, VerifiedIdentity
from packages.workspace_api import WorkspaceApplication
from packages.workspace_ui import WorkspaceManagementUI
from packages.workspaces import Principal, Workspace, Membership, SQLiteWorkspaceService

ISSUER = "https://identity.example.invalid"


class TestVerifier:
    def verify(self, credential):
        subject = {"synthetic-admin": "admin", "synthetic-reader": "reader"}.get(credential.value)
        if subject is None:
            raise AuthenticationError()
        now = datetime.now(timezone.utc)
        return VerifiedIdentity(ISSUER, subject, "ui-tests", now, now + timedelta(seconds=60))


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):pass


class Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteWorkspaceService.initialize(Path(directory)/"ui.sqlite3",
            principals=(Principal("admin",ISSUER,"admin"), Principal("reader",ISSUER,"reader"),Principal("retiree",ISSUER,"retiree"),Principal("other",ISSUER,"other")),
            workspaces=(Workspace("alpha","Product Alpha"),Workspace("commons","Shared collaboration","shared"),Workspace("hidden","Hidden")),
            memberships=(Membership("alpha","admin","admin"),Membership("alpha","reader","viewer"),Membership("alpha","retiree","viewer"),Membership("commons","admin","admin"),Membership("hidden","other","admin")),
            modules=(), resources=(), operators=frozenset())
        api = WorkspaceApplication(store=store,providers=(Provider("test",ISSUER,"ui-tests",TestVerifier()),),provider_key="test")
        server=make_server("127.0.0.1",0,api,server_class=Threaded,handler_class=Quiet)
        origin=f"http://127.0.0.1:{server.server_port}"
        server.set_app(WorkspaceManagementUI(api=api,origin=origin))
        print(origin,flush=True)
        try:server.serve_forever()
        finally:server.server_close()
