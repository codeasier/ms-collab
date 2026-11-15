"""Opt-in synthetic HTTPS assistant fixture; never an application entry point."""
import json
import os
from pathlib import Path
import signal
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
from packages.assistant_history import initialize, create_conversation, append_events, DisplayEvent
from packages.login_state import LoginBinding, SQLiteLoginStore
from packages.session_api import SessionPolicyStore
from packages.workspace_admin_api import initialize_administration
from packages.workspace_invitations import initialize_invitations
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, SQLiteWorkspaceService


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_ASSISTANT_SESSION_UI') != '1' or len(sys.argv) != 3:
        raise SystemExit('explicit synthetic opt-in and temporary certificate required')
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self, *args): pass
        def get_environ(self):
            env = super().get_environ(); env['HTTPS'] = 'on'; return env
    with tempfile.TemporaryDirectory(prefix='ms-collab-assistant-session-') as directory:
        server = make_server('127.0.0.1', 0, lambda e,s: [], handler_class=QuietHTTPS)
        origin = 'https://127.0.0.1:' + str(server.server_port)
        provider = 'https://localhost:' + str(server.server_port) + '/synthetic-provider'
        path = Path(directory) / 'policy.sqlite'
        people = ('admin', 'reader', 'second')
        spaces = ('alpha', 'beta', 'commons', 'empty')
        policy = SQLiteWorkspaceService.initialize(path,
            principals=tuple(Principal(p, ISSUER, p+'-sub') for p in people),
            workspaces=tuple(Workspace(w,w,'shared' if w=='commons' else 'product') for w in spaces),
            memberships=tuple(Membership(w,p,'developer' if p=='reader' else 'admin')
                for w in spaces for p in people if w!='commons' or p=='second'),
            modules=tuple(ModuleInstance(w,'assistant',('read','write')) for w in spaces))
        initialize_administration(policy); initialize_invitations(policy)
        fixed=lambda:datetime(2026,1,2,tzinfo=timezone.utc)
        def seed(owner,workspace,identity,title,events):
            create_conversation(policy,owner=owner,workspace=workspace,conversation=identity,title=title,clock=fixed)
            revision=1
            for offset in range(0,len(events),100):
                append_events(policy,owner=owner,workspace=workspace,conversation=identity,events=tuple(events[offset:offset+100]),expected_revision=revision,clock=fixed)
                revision+=1
        initialize(policy)
        events=[DisplayEvent('user_message','<img src=x onerror=alert(1)> literal user text'),
                DisplayEvent('agent_thought','Explicitly user-visible synthetic report'),
                DisplayEvent('tool','<script>never executed</script>',tool_name='<b>synthetic tool</b>',status='completed')]
        events += [DisplayEvent('assistant_message',f'Synthetic event {n:03d}') for n in range(4,106)]
        seed('admin','alpha','long','<img src=x onerror=alert(1)> Saved alpha',events)
        seed('second','alpha','private-second','Second owner private title',[DisplayEvent('notice','SECOND OWNER PRIVATE')])
        seed('admin','beta','long','Beta saved history',[DisplayEvent('notice','BETA ONLY')])
        seed('reader','alpha','readonly','Read-only saved history',[DisplayEvent('notice','READ ONLY OWNER')])
        # Test-only initial provisioning, before any request/session exists. Model
        # a current viewer-owned saved record, not a production fence migration.
        # No API or runtime control may rewrite membership revisions/fences.
        with sqlite3.connect(path) as db:
            db.execute("UPDATE memberships SET role='viewer' WHERE principal_id='reader'")
        clock=lambda:datetime.now(timezone.utc)
        binding=LoginBinding(ISSUER,'synthetic-assistant-client',origin+'/auth/callback')
        login=SQLiteLoginStore.initialize(Path(directory)/'login.sqlite',binding=binding,clock=clock)
        exchange=SyntheticExchange(binding,clock)
        host=ApplicationHost(config=HostConfig(origin,('assistant',)),sessions=SessionPolicyStore(policy=policy,login=login),exchange=exchange,authorization_endpoint=provider,authorization_scopes=('synthetic_user',),clock=clock)
        app=ApplicationUI(host=host); selected={'subject':'admin-sub'}
        def finish(start, status='200 OK', data=None):
            body=b'' if data is None else json.dumps(data).encode()
            start(status,[('Content-Length',str(len(body))),('Content-Type','application/json'),('Cache-Control','no-store')]);return [body]
        def fixture(env,start):
            route=env.get('PATH_INFO','')
            if route.startswith('/synthetic-assistant/'):
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin or env.get('HTTP_HOST')!='127.0.0.1:'+str(server.server_port):
                    return finish(start,'403 Forbidden')
                if route.startswith('/synthetic-assistant/subject/'):
                    subject=route.rsplit('/',1)[1]
                    if subject not in people:return finish(start,'404 Not Found')
                    selected['subject']=subject+'-sub'
                elif route=='/synthetic-assistant/conflict':
                    with sqlite3.connect(path) as db:
                        revision=db.execute("SELECT revision FROM assistant_conversations WHERE workspace_id='alpha' AND id='long'").fetchone()[0]
                    append_events(policy,owner='admin',workspace='alpha',conversation='long',events=(DisplayEvent('notice','Concurrent saved event'),),expected_revision=revision)
                elif route=='/synthetic-assistant/revoke-session':login.revoke_subject(selected['subject'])
                elif route=='/synthetic-assistant/revoke-membership':policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
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
        signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit()))
        print(json.dumps({'origin':origin,'provider':provider}),flush=True)
        try:server.serve_forever()
        finally:server.server_close()

if __name__=='__main__':main()
