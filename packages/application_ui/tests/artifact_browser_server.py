"""Temporary HTTPS artifact/session acceptance. No fixture routes in production."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import ssl
import sys
import tempfile
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.application_ui.tests.artifact_fixtures import application, stage


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_ARTIFACT_SESSION')!='1' or len(sys.argv)!=3:
        raise SystemExit('explicit synthetic opt-in and temporary certificate required')
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self,*args):pass
        def get_environ(self):
            result=super().get_environ();result['HTTPS']='on';return result
    with tempfile.TemporaryDirectory(prefix='ms-collab-artifact-session-') as directory:
        server=make_server('127.0.0.1',0,lambda e,s:[],handler_class=QuietHTTPS)
        origin='https://127.0.0.1:'+str(server.server_port)
        provider='https://localhost:'+str(server.server_port)+'/synthetic-provider'
        app,policy,login,exchange=application(directory,origin=origin,clock=lambda:datetime.now(timezone.utc),authorization_endpoint=provider)
        def fixture(env,start):
            path=env.get('PATH_INFO')
            if path.startswith('/synthetic/artifact/'):
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                if path.endswith('/stage'):result={'revision':stage(directory)}
                elif path.endswith('/revoke-membership'):
                    policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1);result={}
                elif path.endswith('/revoke-session'):
                    login.revoke_subject('admin-sub');result={}
                else:
                    start('404 Not Found',[('Content-Length','0')]);return [b'']
                raw=json.dumps(result).encode();start('200 OK',[('Content-Type','application/json'),('Content-Length',str(len(raw))),('Cache-Control','no-store')]);return [raw]
            if path=='/synthetic-provider':
                params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                if (env.get('HTTP_HOST')!='localhost:'+str(server.server_port) or params.get('code_challenge_method')!=['S256']
                        or params.get('redirect_uri')!=[login.binding.callback_uri] or params.get('client_id')!=[login.binding.audience]):
                    start('400 Bad Request',[('Content-Length','0')]);return [b'']
                code=exchange.authorize(params['code_challenge'][0]);target=login.binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
            return app(env,start)
        server.set_app(fixture)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2]);server.socket=context.wrap_socket(server.socket,server_side=True)
        signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit()))
        print(json.dumps({'origin':origin,'provider':provider}),flush=True)
        try:server.serve_forever()
        finally:server.server_close()

if __name__=='__main__':main()
