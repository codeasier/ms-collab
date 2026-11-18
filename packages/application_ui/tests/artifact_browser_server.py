"""Temporary HTTPS artifact/session acceptance. No fixture routes in production."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.application_ui.tests.artifact_fixtures import application, stage
from packages.application_ui.tests.synthetic_shutdown import serve_synthetic


def main():
    if os.environ.get('MS_COLLAB_SYNTHETIC_ARTIFACT_SESSION')!='1' or len(sys.argv)!=3:
        raise SystemExit('explicit synthetic opt-in and temporary certificate required')
    discovery = os.environ.get('MS_COLLAB_SYNTHETIC_ARTIFACT_DISCOVERY') == '1'
    product = os.environ.get('MS_COLLAB_SYNTHETIC_ARTIFACT_PRODUCT_VIEWS') == '1'
    if product and discovery:
        raise SystemExit('product and discovery fixtures require separate temporary processes')
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self,*args):pass
        def get_environ(self):
            result=super().get_environ();result['HTTPS']='on';return result
    with tempfile.TemporaryDirectory(prefix='ms-collab-artifact-session-') as directory:
        server=make_server('127.0.0.1',0,lambda e,s:[],handler_class=QuietHTTPS)
        origin='https://127.0.0.1:'+str(server.server_port)
        provider='https://localhost:'+str(server.server_port)+'/synthetic-provider'
        product_hooks = {}
        if product:
            from packages.application_ui.tests.artifact_product_fixtures import (
                product_application, republish_product, revoke_product_read)
            # Security time starts at current UTC, independently of the browser's
            # date-range clock. Only this dedicated process can advance it.
            security_time = datetime.now(timezone.utc)
            def expire_session():
                nonlocal security_time
                security_time += timedelta(hours=2)  # exceeds even the maximum LoginBinding TTL
            app,policy,login,exchange=product_application(directory, origin=origin,
                clock=lambda:security_time, authorization_endpoint=provider)
            product_hooks = {
                '/synthetic/artifact/product-republish': lambda: republish_product(policy),
                '/synthetic/artifact/product-revoke-read': lambda: revoke_product_read(policy),
                '/synthetic/artifact/product-expire-session': expire_session,
            }
        else:
            app,policy,login,exchange=application(directory,origin=origin,clock=lambda:datetime.now(timezone.utc),authorization_endpoint=provider)
        subject = ['admin-sub']
        if discovery:
            from packages.application_ui.tests.artifact_discovery_fixtures import seed_directory
            seed_directory(policy)
        def fixture(env,start):
            path=env.get('PATH_INFO')
            if path.startswith('/synthetic/artifact/'):
                if env.get('REQUEST_METHOD')!='POST' or env.get('HTTP_ORIGIN')!=origin:
                    start('403 Forbidden',[('Content-Length','0')]);return [b'']
                if product:
                    # These three fixed, bodyless loopback controls belong to
                    # the disposable product process only. No suffix matching.
                    if path not in product_hooks:
                        start('404 Not Found',[('Content-Length','0')]);return [b'']
                    if (env.get('HTTP_HOST') != '127.0.0.1:'+str(server.server_port)
                            or env.get('HTTP_SEC_FETCH_SITE') not in (None, 'same-origin', 'none')):
                        start('403 Forbidden',[('Content-Length','0')]);return [b'']
                    if (env.get('QUERY_STRING') or env.get('CONTENT_LENGTH') not in (None, '', '0')
                            or 'HTTP_TRANSFER_ENCODING' in env):
                        start('400 Bad Request',[('Content-Length','0')]);return [b'']
                    product_hooks[path]();result={'ok':True}
                elif discovery and path.endswith('/as-reader'):subject[0]='reader-sub';result={}
                elif discovery and path.endswith('/as-second'):subject[0]='second-sub';result={}
                elif discovery and path.endswith('/seed-pages'):
                    from packages.application_ui.tests.artifact_discovery_fixtures import seed_pages
                    seed_pages(policy);result={}
                elif discovery and path.endswith('/invalidate-cursor'):
                    import sqlite3
                    with sqlite3.connect(Path(directory)/'policy.sqlite') as db:
                        db.execute("UPDATE artifact_indexes_v1 SET published=NULL,published_revision=NULL WHERE workspace='alpha' AND bucket='p022'")
                    result={}
                elif discovery and path.endswith('/as-admin'):subject[0]='admin-sub';result={}
                elif path.endswith('/stage'):result={'revision':stage(directory)}
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
                code=exchange.authorize(params['code_challenge'][0],subject[0]);target=login.binding.callback_uri+'?'+urlencode({'code':code,'state':params['state'][0]})
                start('303 See Other',[('Location',target),('Content-Length','0'),('Cache-Control','no-store'),('Referrer-Policy','no-referrer')]);return [b'']
            return app(env,start)
        server.set_app(fixture)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(sys.argv[1],sys.argv[2]);server.socket=context.wrap_socket(server.socket,server_side=True)
        serve_synthetic(server, on_ready=lambda: print(
            json.dumps({'origin':origin,'provider':provider}),flush=True))

if __name__=='__main__':main()
