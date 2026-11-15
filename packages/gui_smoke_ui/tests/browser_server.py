"""Opt-in synthetic HTTPS fixture only; never a production server or identity."""
from datetime import datetime, timezone
import json
import os
import signal
import ssl
import sys
import tempfile
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_GUI_UI_TEST') != '1' or len(sys.argv) != 3:
        raise SystemExit('synthetic opt-in and temporary certificate required')
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self,*args): pass
        def get_environ(self):
            env=super().get_environ();env['HTTPS']='on';return env
    with tempfile.TemporaryDirectory(prefix='ms-collab-gui-fixture-') as temp:
        server=make_server('127.0.0.1',0,lambda e,s:[],handler_class=QuietHTTPS)
        origin='https://127.0.0.1:'+str(server.server_port)
        provider='https://localhost:'+str(server.server_port)+'/synthetic-provider'
        from packages.gui_smoke_ui.tests.fixtures import setup,tasks
        from packages.gui_smoke import publish_snapshot, regrant_connection
        clock=lambda:datetime.now(timezone.utc)
        app,policy,login,exchange,connections=setup(temp,origin=origin,clock=clock,authorization_endpoint=provider)
        revision=1;grant_revision=1
        def fixture(env,start):
            nonlocal revision,grant_revision
            path=env.get('PATH_INFO')
            if path in {'/synthetic-republish','/synthetic-regrant','/synthetic-revoke','/synthetic-policy-revoke'}:
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                if path=='/synthetic-republish':
                    publish_snapshot(policy,connections[0],actor='writer',tasks=tasks(),observed_at=clock().isoformat(),expected_revision=revision,expected_grant_revision=grant_revision,clock=clock)
                    revision+=1
                elif path=='/synthetic-regrant':
                    regrant_connection(policy,connections[0],actor='admin',expected_grant_revision=grant_revision,expected_snapshot_revision=revision,clock=clock)
                    grant_revision+=1;revision+=1
                elif path=='/synthetic-policy-revoke':
                    policy.set_membership('admin','alpha','alice',role='viewer',state='revoked',expected_revision=1)
                else: login.revoke_subject('alice-sub')
                start('200 OK',[('Content-Length','0'),('Cache-Control','no-store')]);return [b'']
            if path=='/synthetic-provider':
                params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                if params.get('code_challenge_method')!=['S256'] or params.get('redirect_uri')!=[login.binding.callback_uri]:
                    start('400 Bad Request',[('Content-Length','0')]);return [b'']
                code=exchange.authorize(params['code_challenge'][0],'alice-sub')
                target=login.binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
            return app(env,start)
        server.set_app(fixture)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2])
        server.socket=context.wrap_socket(server.socket,server_side=True)
        signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit()))
        print(json.dumps({'origin':origin,'provider':provider}),flush=True)
        try: server.serve_forever()
        finally: server.server_close()

if __name__=='__main__':main()
