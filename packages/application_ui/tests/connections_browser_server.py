"""Opt-in synthetic HTTPS connection management fixture; never an application entry point."""
import json
import os
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests.fixtures import SyntheticExchange, ISSUER
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.synthetic_shutdown import serve_synthetic
from packages.community_api import ConnectionPolicy, initialize
from packages.community_api.records import encode, snapshot_data, fingerprint, read, approval_text, capture_approval
from packages.community_board import Scope, Snapshot, BoardItem
from packages.community_sync import Connection
from packages.login_state import LoginBinding, SQLiteLoginStore
from packages.session_api import SessionPolicyStore
from packages.workspace_admin_api import initialize_administration
from packages.workspace_invitations import initialize_invitations
from packages.workspace_connections import CommunityConnectionTemplate, initialize_connections
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, SQLiteWorkspaceService


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_CONNECTIONS_UI') != '1' or len(sys.argv) != 3:
        raise SystemExit('explicit synthetic opt-in and temporary certificate required')
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self, *args): pass
        def get_environ(self):
            env = super().get_environ(); env['HTTPS'] = 'on'; return env
    with tempfile.TemporaryDirectory(prefix='ms-collab-community-session-') as directory:
        server = make_server('127.0.0.1', 0, lambda e,s: [], handler_class=QuietHTTPS)
        origin = 'https://127.0.0.1:' + str(server.server_port)
        provider = 'https://localhost:' + str(server.server_port) + '/synthetic-provider'
        path = Path(directory) / 'policy.sqlite'
        people = ('admin', 'reader', 'reviewer', 'second')
        spaces = ('alpha', 'beta', 'commons', 'empty')
        policy = SQLiteWorkspaceService.initialize(path,
            principals=tuple(Principal(p, ISSUER, p+'-sub') for p in people),
            workspaces=tuple(Workspace(w,w,'shared' if w=='commons' else 'product') for w in spaces),
            memberships=tuple(Membership(w,p,'viewer' if p=='reader' else 'admin')
                for w in spaces for p in people if w!='commons' or p=='second'),
            modules=tuple(ModuleInstance(w,'community',('read','write')) for w in spaces))
        initialize_administration(policy); initialize_invitations(policy)
        scopes=tuple(Scope(w,'source','sample/project') for w in spaces)
        initialize(policy,tuple(ConnectionPolicy(Connection(s),'synthetic-unused',('admin','reader'),('admin',),('admin','reviewer')) for s in scopes))
        with sqlite3.connect(path) as db:
            current_policy=policy._load(db)
            for scope in scopes:
                if scope.workspace_id=='empty': continue
                items=tuple(BoardItem('issue',n,('<img src=x onerror=alert(1)>' if n==101 else f'Synthetic issue {n}') + (' beta' if scope.workspace_id=='beta' else ''), 'open','2026-01-01T00:00:00Z',('<b>plain label</b>',)) for n in range(1,102))
                snapshot=Snapshot(scope,'2026-01-02T00:00:00Z',items)
                approvals=[fingerprint(scope,item) for item in items if item.number in (1,101)]
                db.execute('UPDATE community_connections SET snapshot=?,approvals=? WHERE workspace_id=?',(encode(snapshot_data(snapshot)),approval_text(approvals,capture_approval(current_policy,read(db,scope),"admin")) if scope.workspace_id!="commons" else approval_text([],None),scope.workspace_id))
        templates=tuple(CommunityConnectionTemplate('source-policy',ConnectionPolicy(Connection(scope),'synthetic-unused',('admin','reader','second'),('admin','second'),('admin','reviewer','second')),('admin',)) for scope in scopes)
        initialize_connections(policy,templates)
        clock=lambda:datetime.now(timezone.utc)
        binding=LoginBinding(ISSUER,'synthetic-community-client',origin+'/auth/callback')
        login=SQLiteLoginStore.initialize(Path(directory)/'login.sqlite',binding=binding,clock=clock)
        exchange=SyntheticExchange(binding,clock)
        host=ApplicationHost(config=HostConfig(origin,('community',),community_connections=templates),sessions=SessionPolicyStore(policy=policy,login=login),exchange=exchange,authorization_endpoint=provider,authorization_scopes=('synthetic_user',),clock=clock)
        app=ApplicationUI(host=host); selected={'subject':'admin-sub'}
        def finish(start, status='200 OK', data=None):
            body=b'' if data is None else json.dumps(data).encode()
            start(status,[('Content-Length',str(len(body))),('Content-Type','application/json'),('Cache-Control','no-store')]);return [body]
        def fixture(env,start):
            route=env.get('PATH_INFO','')
            if route.startswith('/synthetic-community/'):
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin or env.get('HTTP_HOST')!='127.0.0.1:'+str(server.server_port):
                    return finish(start,'403 Forbidden')
                if route.startswith('/synthetic-community/subject/'):
                    subject=route.rsplit('/',1)[1]
                    if subject not in people:return finish(start,'404 Not Found')
                    selected['subject']=subject+'-sub'
                elif route=='/synthetic-community/conflict':
                    with sqlite3.connect(path) as db:db.execute("UPDATE community_connections SET revision=revision+1 WHERE workspace_id='alpha'")
                elif route=='/synthetic-community/revoke-session':login.revoke_subject(selected['subject'])
                elif route=='/synthetic-community/revoke-membership':policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
                else:return finish(start,'404 Not Found')
                return finish(start)
            if route=='/synthetic-provider':
                params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                if (env.get('HTTP_HOST')!='localhost:'+str(server.server_port) or params.get('code_challenge_method')!=['S256'] or params.get('redirect_uri')!=[binding.callback_uri] or params.get('client_id')!=[binding.audience]):return finish(start,'400 Bad Request')
                code=exchange.authorize(params['code_challenge'][0],selected['subject'])
                target=binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
            return app(env,start)
        server.set_app(fixture)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2]);server.socket=context.wrap_socket(server.socket,server_side=True)
        serve_synthetic(server, on_ready=lambda: print(json.dumps({'origin':origin,'provider':provider}),flush=True))

if __name__=='__main__':main()
