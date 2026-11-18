"""Test harness ONLY. Never import this verifier into deployment configuration."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import json
import os
import ssl
import sys
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer
from socketserver import ThreadingMixIn

from packages.application_ui.tests.synthetic_shutdown import serve_synthetic
from packages.identity import AuthenticationError, Provider, VerifiedIdentity
from packages.workspace_api import WorkspaceApplication
from packages.workspace_admin_api import WorkspaceAdministrationApplication, ModuleTemplate, initialize_administration
from packages.workspace_invitations import WorkspaceInvitationApplication, initialize_invitations
from packages.workspace_ui import WorkspaceManagementUI
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, SQLiteWorkspaceService

ISSUER = "https://identity.example.invalid"


class TestVerifier:
    def verify(self, credential):
        subject = {"synthetic-admin": "admin", "synthetic-reader": "reader", "synthetic-operator": "operator", "synthetic-newcomer":"newcomer"}.get(credential.value)
        if subject is None:
            raise AuthenticationError()
        now = datetime.now(timezone.utc)
        return VerifiedIdentity(ISSUER, subject, "ui-tests", now, now + timedelta(seconds=60))


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):pass


class Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def transfer_main(cert, key):
    """Separate opt-in TLS fixture; the old HTTP mode is not initialized."""
    from packages.application_host.tests.fixtures import initialize
    from packages.application_ui.tests.browser_server import dispatch_admin_transfer, TRANSFER_MODE
    from packages.workspace_admin_api.tests.transfer_fixtures import BROWSER_SUBJECT, provision_browser
    from packages.workspace_admin_api.transfer_store import initialize_transfers

    class TransferVerifier:
        def verify(self, credential):
            subject = {'synthetic-transfer-actor': BROWSER_SUBJECT,
                       'synthetic-transfer-reader': 'reader-sub'}.get(credential.value)
            if subject is None:
                raise AuthenticationError()
            now = datetime.now(timezone.utc)
            return VerifiedIdentity(ISSUER, subject, 'ui-tests', now, now + timedelta(minutes=5))

    class QuietHTTPS(Quiet):
        def get_environ(self):
            env = super().get_environ(); env['HTTPS'] = 'on'; return env

    with tempfile.TemporaryDirectory(prefix='ms-collab-admin-transfer-') as directory:
        store = initialize(Path(directory) / 'policy.sqlite')
        provision_browser(store)
        transfers = initialize_transfers(store, layout='explicit-factory-v1')
        api = WorkspaceApplication(store=store,
            providers=(Provider('test', ISSUER, 'ui-tests', TransferVerifier()),), provider_key='test')
        api = WorkspaceAdministrationApplication(api=api, transfers=transfers,
            module_catalog=(ModuleTemplate('board', ('read', 'write')),))
        api = WorkspaceInvitationApplication(api=api)
        server = make_server('127.0.0.1', 0, api, server_class=Threaded, handler_class=QuietHTTPS)
        origin = f'https://127.0.0.1:{server.server_port}'
        app = WorkspaceManagementUI(api=api, origin=origin)

        def fixture(env, start):
            response = dispatch_admin_transfer(store, mode=TRANSFER_MODE, origin=origin,
                environ=env, start_response=start)
            return app(env, start) if response is None else response

        server.set_app(fixture)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        serve_synthetic(server, on_ready=lambda: print(json.dumps({'origin': origin}), flush=True))


def main():
    if len(sys.argv) != 1:
        if (len(sys.argv) != 4 or sys.argv[3] != 'admin-transfer'
                or os.environ.get('MS_COLLAB_SYNTHETIC_ADMIN_TRANSFER') != '1'):
            raise SystemExit('explicit transfer mode opt-in and temporary certificate required')
        return transfer_main(sys.argv[1], sys.argv[2])
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteWorkspaceService.initialize(Path(directory)/"ui.sqlite3",
            principals=tuple(Principal(p,ISSUER,p) for p in ("admin","reader","retiree","other","operator","newcomer","p" * 200)),
            workspaces=(Workspace("alpha","Product Alpha"),Workspace("commons","Shared collaboration","shared"),Workspace("hidden","Hidden")),
            memberships=(Membership("alpha","admin","admin"),Membership("alpha","reader","viewer"),Membership("alpha","retiree","viewer"),Membership("commons","admin","admin"),Membership("hidden","other","admin")),
            modules=(ModuleInstance("alpha","board",("read","write")),), resources=(), operators=frozenset({"operator"}))
        initialize_administration(store)
        initialize_invitations(store)
        api = WorkspaceApplication(store=store,providers=(Provider("test",ISSUER,"ui-tests",TestVerifier()),),provider_key="test")
        api = WorkspaceAdministrationApplication(api=api,module_catalog=(ModuleTemplate("board",("read","write")),ModuleTemplate("artifacts"),ModuleTemplate("pipelines")))
        api = WorkspaceInvitationApplication(api=api)
        server=make_server("127.0.0.1",0,api,server_class=Threaded,handler_class=Quiet)
        origin=f"http://127.0.0.1:{server.server_port}"
        server.set_app(WorkspaceManagementUI(api=api,origin=origin))
        serve_synthetic(server,on_ready=lambda:print(origin,flush=True))


if __name__ == "__main__":main()
