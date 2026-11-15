"""Independent synthetic HTTPS/cookie responsibility fixture, not product APIs.

Requires an explicit test opt-in and disposable TLS certificate. The production
identity mutation entrypoint remains closed; only test helper ABA is simulated.
"""
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler
from packages.application_host.tests.fixtures import setup
from packages.application_ui import ApplicationUI
from .reference_fixture import identity_aba, membership_change
from packages.application_ui.tests.synthetic_shutdown import serve_synthetic


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_RESPONSIBILITY_UI') != '1' or len(sys.argv) != 3:
        raise SystemExit('explicit synthetic opt-in and temporary TLS certificate required')
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self,*args):pass
        def get_environ(self):
            env=super().get_environ();env['HTTPS']='on';return env
    with tempfile.TemporaryDirectory(prefix='ms-collab-responsibility-cookie-') as directory:
        with make_server('127.0.0.1',0,lambda e,s:[],handler_class=QuietHTTPS) as server:
            origin='https://127.0.0.1:'+str(server.server_port)
            provider='https://localhost:'+str(server.server_port)+'/synthetic-provider'
            host,policy,login,exchange=setup(directory,origin=origin,
                authorization_endpoint=provider,clock=lambda:datetime.now(timezone.utc))
            app=ApplicationUI(host=host)
            def fixture(env,start):
                path=env.get('PATH_INFO')
                if path in {'/synthetic/identity-aba','/synthetic/revoke-target','/synthetic/restore-target','/synthetic/revoke-session'}:
                    if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                        start('403 Forbidden',[('Content-Length','0')]);return [b'']
                    if path.endswith('identity-aba'):identity_aba(Path(directory)/'policy.sqlite','reader')
                    elif path.endswith('revoke-session'):login.revoke_subject('admin-sub')
                    else:membership_change(policy,Path(directory)/'policy.sqlite','reader','revoked' if path.endswith('revoke-target') else 'active')
                    start('200 OK',[('Content-Length','0'),('Cache-Control','no-store')]);return [b'']
                if path=='/synthetic-provider':
                    params=parse_qs(env.get('QUERY_STRING',''),strict_parsing=True)
                    if (env.get('HTTP_HOST')!='localhost:'+str(server.server_port)
                            or params.get('code_challenge_method')!=['S256']
                            or params.get('redirect_uri')!=[login.binding.callback_uri]
                            or params.get('client_id')!=[login.binding.audience]):
                        start('400 Bad Request',[('Content-Length','0')]);return [b'']
                    code=exchange.authorize(params['code_challenge'][0])
                    target=login.binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                    start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
                return app(env,start)
            server.set_app(fixture)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2]);server.socket=context.wrap_socket(server.socket,server_side=True)
            serve_synthetic(server,on_ready=lambda:print(json.dumps({'origin':origin,'provider':provider}),flush=True))

if __name__=='__main__':main()
